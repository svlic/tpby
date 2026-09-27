from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from telethon import TelegramClient, events, utils

from .config import Settings
from .domain import (
    CODE_PATTERN,
    GroupSnapshot,
    MediaInput,
    SourceTask,
    contains_numbered_code,
    extract_code,
    is_deal_media_caption,
    is_recent_date_code,
)
from .repository import Repository
from .service import RoutingService

LOG = logging.getLogger(__name__)

TELEGRAM_REQUEST_RETRIES = 3
TELEGRAM_CONNECTION_RETRIES = 3
TELEGRAM_RETRY_DELAY_SECONDS = 2
TELEGRAM_FLOOD_SLEEP_THRESHOLD_SECONDS = 60


@dataclass(frozen=True)
class SourceObject:
    messages: tuple[Any, ...]

    @property
    def date(self) -> Any:
        return max(message.date for message in self.messages)


class AmbiguousSourceError(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_source_media(message: Any) -> bool:
    mime_type = getattr(getattr(message, "file", None), "mime_type", "") or ""
    return bool(
        message.photo or message.video or mime_type.startswith(("image/", "video/"))
    )


class TelegramPublisher:
    def __init__(self, writer: TelegramClient, settings: Settings) -> None:
        self.writer = writer
        self.channels = {
            "DEAL1": settings.deal1_channel,
            "DEAL2": settings.deal2_channel,
            "UP": settings.up_channel,
            "BLACKLIST": settings.blacklist_channel,
        }

    async def publish_group(
        self, group: GroupSnapshot
    ) -> Sequence[tuple[int, str | None]]:
        media = group.display_media()
        if not media:
            raise RuntimeError(f"group {group.id} has no displayable media")
        caption = "\n".join(group.codes)
        published: list[tuple[int, str | None]] = []
        for offset in range(0, len(media), 10):
            chunk = media[offset : offset + 10]
            result = await self.writer.send_file(
                self.channels[group.kind],
                [str(item.path) for item in chunk],
                caption=caption,
            )
            messages = result if isinstance(result, list) else [result]
            if len(messages) != len(chunk):
                raise RuntimeError("Telegram returned an unexpected album size")
            published.extend(
                (message.id, item.sha256)
                for message, item in zip(messages, chunk, strict=True)
            )
        return published

    async def delete_messages(self, messages: Sequence[tuple[str, int]]) -> None:
        by_kind: dict[str, list[int]] = {}
        for kind, message_id in messages:
            by_kind.setdefault(kind, []).append(message_id)
        for kind, message_ids in by_kind.items():
            await self.writer.delete_messages(self.channels[kind], message_ids)

    async def publish_blacklist_metadata(self, content: str) -> None:
        document = io.BytesIO(content.encode("utf-8"))
        document.name = "blacklist-metadata.txt"
        await self.writer.send_file(self.channels["BLACKLIST"], document)


class TelegramApplication:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        client_options = {
            "request_retries": TELEGRAM_REQUEST_RETRIES,
            "connection_retries": TELEGRAM_CONNECTION_RETRIES,
            "retry_delay": TELEGRAM_RETRY_DELAY_SECONDS,
            "flood_sleep_threshold": TELEGRAM_FLOOD_SLEEP_THRESHOLD_SECONDS,
            "raise_last_call_error": True,
        }
        self.reader = TelegramClient(
            settings.reader_session,
            settings.api_id,
            settings.api_hash,
            **client_options,
        )
        self.writer = TelegramClient(
            settings.writer_session,
            settings.api_id,
            settings.api_hash,
            **client_options,
        )
        self.repository = Repository(settings.database_path)
        self.publisher = TelegramPublisher(self.writer, settings)
        self.service = RoutingService(self.repository, self.publisher)
        self._operation_lock = asyncio.Lock()
        self._source_peer_ids: dict[int, str] = {}

    async def run(self) -> None:
        self.settings.media_dir.mkdir(parents=True, exist_ok=True)
        await self.reader.start()
        await self.writer.start()
        await self._resolve_channel_ids()
        self.reader.add_event_handler(
            self._on_code_message,
            events.NewMessage(chats=self.settings.code_channel),
        )
        self.reader.add_event_handler(
            self._on_up_message,
            events.NewMessage(chats=self.settings.up_channel),
        )
        self.reader.add_event_handler(
            self._on_blacklist_message,
            events.NewMessage(chats=self.settings.blacklist_channel),
        )
        self.reader.add_event_handler(
            self._on_up_deleted,
            events.MessageDeleted(chats=self.settings.up_channel),
        )
        LOG.info("tpby is listening")
        try:
            await self.reader.run_until_disconnected()
        finally:
            await self.writer.disconnect()
            self.repository.close()

    async def rebuild(self) -> None:
        """Destructively rebuild long-term state from UP and BLACKLIST."""
        self.settings.media_dir.mkdir(parents=True, exist_ok=True)
        await self.reader.start()
        try:
            up_groups = await self._scan_up_groups()
            blacklist_hashes = await self._scan_blacklist_hashes()
            self.repository.reset_for_rebuild()
            with self.repository.transaction():
                self.repository.add_blacklist_hashes(blacklist_hashes)
                for codes, message_media in up_groups.items():
                    media = tuple(item for _, item in message_media)
                    if not media:
                        continue
                    task_ids = []
                    for code in codes:
                        recovered = SourceTask(code, media, media[0].sha256)
                        task_ids.append(self.repository.insert_task(recovered, "UP"))
                    group_id = self.repository.create_group("UP", task_ids)
                    self.repository.replace_message_index(
                        "UP",
                        group_id,
                        [
                            (message_id, item.sha256)
                            for message_id, item in message_media
                        ],
                    )
            LOG.info(
                "rebuild complete: up_groups=%d blacklist_hashes=%d",
                len(up_groups),
                len(blacklist_hashes),
            )
        finally:
            await self.reader.disconnect()
            self.repository.close()

    async def _scan_up_groups(
        self,
    ) -> dict[tuple[str, ...], list[tuple[int, MediaInput]]]:
        albums: dict[tuple[str, int], list[Any]] = {}
        async for message in self.reader.iter_messages(self.settings.up_channel):
            if message.media is None:
                continue
            key = (
                "album" if message.grouped_id is not None else "message",
                int(message.grouped_id or message.id),
            )
            albums.setdefault(key, []).append(message)

        groups: dict[tuple[str, ...], list[tuple[int, MediaInput]]] = {}
        for messages in albums.values():
            captions = "\n".join(message.message or "" for message in messages)
            codes = tuple(
                dict.fromkeys(
                    match.group(0) for match in CODE_PATTERN.finditer(captions)
                )
            )
            if not codes:
                LOG.warning("skipping UP media without a recoverable code caption")
                continue
            target = groups.setdefault(codes, [])
            for message in sorted(messages, key=lambda item: item.id):
                target.append((message.id, await self._download_media(message)))
        return groups

    async def _scan_blacklist_hashes(self) -> set[str]:
        hashes: set[str] = set()
        async for message in self.reader.iter_messages(self.settings.blacklist_channel):
            if message.photo or message.video:
                hashes.add((await self._download_media(message)).sha256)
                continue
            if message.document is None:
                continue
            name = getattr(getattr(message, "file", None), "name", "") or ""
            if not name.endswith((".json", ".txt")):
                hashes.add((await self._download_media(message)).sha256)
                continue
            payload = await self.reader.download_media(message, file=bytes)
            if not payload:
                continue
            try:
                metadata = json.loads(payload.decode("utf-8"))
                hashes.update(str(value) for value in metadata.get("hashes", []))
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                LOG.warning(
                    "ignoring invalid blacklist metadata message %s", message.id
                )
        return hashes

    async def _resolve_channel_ids(self) -> None:
        for kind, channel in (
            ("DEAL1", self.settings.deal1_channel),
            ("DEAL2", self.settings.deal2_channel),
        ):
            entity = await self.reader.get_entity(channel)
            self._source_peer_ids[utils.get_peer_id(entity)] = kind

    async def _on_code_message(self, event: Any) -> None:
        async with self._operation_lock:
            chat_id = int(event.chat_id)
            message_id = int(event.message.id)
            if self.repository.is_code_message_processed(chat_id, message_id):
                return
            code = extract_code(event.raw_text or "")
            if code is None:
                return
            try:
                if is_recent_date_code(code):
                    await self.writer.forward_messages(
                        self.settings.deal1_channel,
                        message_id,
                        self.settings.code_channel,
                    )
                    self.repository.record_bypass(chat_id, message_id, code)
                    return
                task = await self._build_source_task(code, chat_id, message_id)
                result = await self.service.process(task)
                LOG.info("processed code=%s disposition=%s", code, result.disposition)
            except AmbiguousSourceError as error:
                LOG.warning("rejected code=%s: %s", code, error)
                self.repository.record_failure(chat_id, message_id, str(error))
            except Exception as error:
                LOG.exception("failed to process code=%s", code)
                self.repository.record_failure(chat_id, message_id, str(error))

    async def _build_source_task(
        self, code: str, code_chat_id: int, code_message_id: int
    ) -> SourceTask:
        objects = await self._find_source_objects(code)
        candidates = [
            message
            for source_object in objects
            for message in source_object.messages
            if _is_source_media(message)
        ]
        deal_messages = [
            message
            for message in candidates
            if is_deal_media_caption(message.message or "")
        ]
        if len(deal_messages) != 1:
            raise AmbiguousSourceError(
                f"expected exactly one deal_media, found {len(deal_messages)}"
            )

        downloaded: list[MediaInput] = []
        deal_hash: str | None = None
        for message in candidates:
            media = await self._download_media(message)
            downloaded.append(media)
            if message.id == deal_messages[0].id:
                deal_hash = media.sha256
        if deal_hash is None:
            raise AmbiguousSourceError("deal_media could not be downloaded")
        return SourceTask(
            code,
            tuple(downloaded),
            deal_hash,
            code_chat_id,
            code_message_id,
        )

    async def _find_source_objects(self, code: str) -> list[SourceObject]:
        matched: list[Any] = []
        async for message in self.reader.iter_messages(
            self.settings.source_channel,
            search=f'"{code}"',
            limit=self.settings.source_search_limit,
        ):
            if contains_numbered_code(message.message or "", code):
                matched.append(message)
        if not matched:
            raise AmbiguousSourceError("no locally verified source result")

        objects: dict[tuple[str, int], SourceObject] = {}
        for message in matched:
            if message.grouped_id is None:
                objects[("message", message.id)] = SourceObject((message,))
                continue
            key = ("album", int(message.grouped_id))
            if key in objects:
                continue
            nearby = await self.reader.get_messages(
                self.settings.source_channel,
                ids=range(max(1, message.id - 20), message.id + 21),
            )
            members = tuple(
                sorted(
                    (
                        item
                        for item in nearby
                        if item is not None and item.grouped_id == message.grouped_id
                    ),
                    key=lambda item: item.id,
                )
            )
            objects[key] = SourceObject(members)

        if len(objects) > 2:
            raise AmbiguousSourceError(
                f"source search returned {len(objects)} locally verified objects"
            )
        return sorted(objects.values(), key=lambda item: item.date)

    async def _download_media(self, message: Any) -> MediaInput:
        extension = getattr(getattr(message, "file", None), "ext", "") or ""
        temporary = (
            self.settings.media_dir / f"download-{message.id}-{uuid4().hex}{extension}"
        )
        downloaded = await self.reader.download_media(message, file=str(temporary))
        if not downloaded:
            raise RuntimeError(f"failed to download source message {message.id}")
        downloaded_path = Path(downloaded)
        sha256 = await asyncio.to_thread(_sha256_file, downloaded_path)
        existing = next(self.settings.media_dir.glob(f"{sha256}.*"), None)
        destination = (
            existing or self.settings.media_dir / f"{sha256}{downloaded_path.suffix}"
        )
        if existing is not None:
            downloaded_path.unlink()
        else:
            downloaded_path.replace(destination)
        mime_type = getattr(getattr(message, "file", None), "mime_type", None)
        return MediaInput(sha256, destination, mime_type, message.id)

    async def _on_up_message(self, event: Any) -> None:
        await self._handle_manual_forward(event, "UP")

    async def _on_blacklist_message(self, event: Any) -> None:
        await self._handle_manual_forward(event, "BLACKLIST")

    async def _handle_manual_forward(self, event: Any, destination: str) -> None:
        forward = event.message.fwd_from
        source_peer = getattr(forward, "from_id", None) if forward else None
        source_message_id = getattr(forward, "channel_post", None) if forward else None
        if source_peer is None or source_message_id is None:
            return
        source_kind = self._source_peer_ids.get(utils.get_peer_id(source_peer))
        if source_kind not in {"DEAL1", "DEAL2"}:
            return
        row = self.repository.lookup_message(source_kind, int(source_message_id))
        if row is None:
            LOG.warning(
                "manual forward references unknown %s message %s",
                source_kind,
                source_message_id,
            )
            return
        async with self._operation_lock:
            group_id = int(row["group_id"])
            if not self.repository.group_is_active(group_id):
                if destination == "UP":
                    await self.writer.delete_messages(
                        self.settings.up_channel, [event.message.id]
                    )
                return
            if destination == "UP":
                await self.service.promote_deal_to_up(group_id)
                await self.writer.delete_messages(
                    self.settings.up_channel, [event.message.id]
                )
            else:
                await self.service.move_deal_to_blacklist(group_id)

    async def _on_up_deleted(self, event: Any) -> None:
        async with self._operation_lock:
            await self.service.hide_deleted_up_messages(event.deleted_ids)
