from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

from telethon import TelegramClient, errors, events, utils

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
TELEGRAM_CONNECTION_RETRIES = 5
TELEGRAM_RETRY_DELAY_SECONDS = 2
TELEGRAM_FLOOD_SLEEP_THRESHOLD_SECONDS = 10
TELEGRAM_CONCURRENCY_INCREASE_AFTER = 8

ResultT = TypeVar("ResultT")


@dataclass(frozen=True)
class SourceObject:
    messages: tuple[Any, ...]

    @property
    def date(self) -> Any:
        return max(message.date for message in self.messages)


class AmbiguousSourceError(RuntimeError):
    pass


class AdaptiveTelegramGate:
    """Bound account I/O and coordinate server-requested flood waits."""

    def __init__(self, name: str, maximum: int) -> None:
        self.name = name
        self.maximum = maximum
        self._limit = 1
        self._active = 0
        self._successes = 0
        self._cooldown_until = 0.0
        self._condition = asyncio.Condition()

    @asynccontextmanager
    async def _slot(self):
        while True:
            async with self._condition:
                delay = self._cooldown_until - asyncio.get_running_loop().time()
                if delay <= 0 and self._active < self._limit:
                    self._active += 1
                    break
                if delay <= 0:
                    await self._condition.wait()
                    continue
            await asyncio.sleep(delay)
        try:
            yield
        finally:
            async with self._condition:
                self._active -= 1
                self._condition.notify_all()

    async def run(self, operation: Callable[[], Awaitable[ResultT]]) -> ResultT:
        while True:
            try:
                async with self._slot():
                    result = await operation()
            except (errors.FloodWaitError, errors.SlowModeWaitError) as error:
                await self._on_flood_wait(int(error.seconds))
                continue
            await self._on_success()
            return result

    async def _on_flood_wait(self, seconds: int) -> None:
        async with self._condition:
            old_limit = self._limit
            self._limit = max(1, self._limit // 2)
            self._successes = 0
            self._cooldown_until = max(
                self._cooldown_until,
                asyncio.get_running_loop().time() + max(1, seconds) + 1,
            )
            self._condition.notify_all()
        LOG.warning(
            "%s Telegram I/O cooling down for %ds; concurrency %d -> %d",
            self.name,
            seconds,
            old_limit,
            self._limit,
        )

    async def _on_success(self) -> None:
        async with self._condition:
            if self._limit >= self.maximum:
                return
            self._successes += 1
            if self._successes < TELEGRAM_CONCURRENCY_INCREASE_AFTER:
                return
            self._limit += 1
            self._successes = 0
            self._condition.notify_all()
            LOG.info("%s Telegram I/O concurrency increased to %d", self.name, self._limit)


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
    def __init__(
        self,
        writer: TelegramClient,
        settings: Settings,
        writer_gate: AdaptiveTelegramGate,
    ) -> None:
        self.writer = writer
        self._writer_gate = writer_gate
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
        try:
            for offset in range(0, len(media), 10):
                chunk = media[offset : offset + 10]
                result = await self._writer_gate.run(
                    lambda chunk=chunk: self.writer.send_file(
                        self.channels[group.kind],
                        [str(item.path) for item in chunk],
                        caption=caption,
                    )
                )
                messages = result if isinstance(result, list) else [result]
                if len(messages) != len(chunk):
                    raise RuntimeError("Telegram returned an unexpected album size")
                published.extend(
                    (message.id, item.sha256)
                    for message, item in zip(messages, chunk, strict=True)
                )
        except BaseException:
            if published:
                try:
                    await self._writer_gate.run(
                        lambda: self.writer.delete_messages(
                            self.channels[group.kind],
                            [message_id for message_id, _ in published],
                        )
                    )
                except Exception:
                    LOG.exception(
                        "failed to remove partially published Telegram group %s",
                        group.id,
                    )
            raise
        return published

    async def delete_messages(self, messages: Sequence[tuple[str, int]]) -> None:
        by_kind: dict[str, list[int]] = {}
        for kind, message_id in messages:
            by_kind.setdefault(kind, []).append(message_id)
        for kind, message_ids in by_kind.items():
            await self._writer_gate.run(
                lambda kind=kind, message_ids=message_ids: self.writer.delete_messages(
                    self.channels[kind], message_ids
                )
            )

    async def publish_blacklist_metadata(self, content: str) -> None:
        async def publish() -> Any:
            document = io.BytesIO(content.encode("utf-8"))
            document.name = "blacklist-metadata.txt"
            return await self.writer.send_file(self.channels["BLACKLIST"], document)

        await self._writer_gate.run(publish)


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
        self._reader_gate = AdaptiveTelegramGate(
            "reader", settings.reader_io_concurrency
        )
        self._writer_gate = AdaptiveTelegramGate(
            "writer", settings.writer_io_concurrency
        )
        self.publisher = TelegramPublisher(self.writer, settings, self._writer_gate)
        self.service = RoutingService(self.repository, self.publisher)
        self._mutation_lock = asyncio.Lock()
        self._hash_semaphore = asyncio.Semaphore(settings.media_hash_concurrency)
        self._code_jobs_available = asyncio.Event()
        self._ready = asyncio.Event()
        self._source_peer_ids: dict[int, str] = {}

    async def run(self) -> None:
        self.settings.media_dir.mkdir(parents=True, exist_ok=True)
        workers: list[asyncio.Task[None]] = []
        try:
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
            self.repository.reset_running_code_jobs()
            await self.reader.start()
            await self.writer.start()
            await self._resolve_channel_ids()
            self._code_jobs_available.set()
            workers = [
                asyncio.create_task(self._run_code_worker(), name=f"code-worker-{index}")
                for index in range(self.settings.source_prepare_concurrency)
            ]
            self._ready.set()
            await self._reader_gate.run(self.reader.catch_up)
            LOG.info(
                "tpby is listening with %d source preparation workers",
                len(workers),
            )
            await self.reader.run_until_disconnected()
        finally:
            for worker in workers:
                worker.cancel()
            if workers:
                await asyncio.gather(*workers, return_exceptions=True)
            await self.reader.disconnect()
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
        async def collect_albums() -> dict[tuple[str, int], list[Any]]:
            albums: dict[tuple[str, int], list[Any]] = {}
            async for message in self.reader.iter_messages(self.settings.up_channel):
                if message.media is None:
                    continue
                key = (
                    "album" if message.grouped_id is not None else "message",
                    int(message.grouped_id or message.id),
                )
                albums.setdefault(key, []).append(message)
            return albums

        albums = await self._reader_gate.run(collect_albums)

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
        async def collect_messages() -> list[Any]:
            return [
                message
                async for message in self.reader.iter_messages(
                    self.settings.blacklist_channel
                )
            ]

        hashes: set[str] = set()
        messages = await self._reader_gate.run(collect_messages)
        for message in messages:
            if message.photo or message.video:
                hashes.add((await self._download_media(message)).sha256)
                continue
            if message.document is None:
                continue
            name = getattr(getattr(message, "file", None), "name", "") or ""
            if not name.endswith((".json", ".txt")):
                hashes.add((await self._download_media(message)).sha256)
                continue
            payload = await self._reader_gate.run(
                lambda message=message: self.reader.download_media(message, file=bytes)
            )
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
            entity = await self._reader_gate.run(
                lambda channel=channel: self.reader.get_entity(channel)
            )
            self._source_peer_ids[utils.get_peer_id(entity)] = kind

    async def _on_code_message(self, event: Any) -> None:
        await self._ready.wait()
        chat_id = int(event.chat_id)
        message_id = int(event.message.id)
        code = extract_code(event.raw_text or "")
        if code is None:
            return
        if self.repository.enqueue_code_job(chat_id, message_id, code):
            self._code_jobs_available.set()

    async def _run_code_worker(self) -> None:
        while True:
            job = self.repository.claim_code_job()
            if job is None:
                self._code_jobs_available.clear()
                await self._code_jobs_available.wait()
                continue
            await self._process_code_job(
                str(job["code"]),
                int(job["code_chat_id"]),
                int(job["code_message_id"]),
            )

    async def _process_code_job(
        self, code: str, chat_id: int, message_id: int
    ) -> None:
        if self.repository.is_code_message_processed(chat_id, message_id):
            self.repository.complete_code_job(chat_id, message_id)
            return
        try:
            if is_recent_date_code(code):
                async with self._mutation_lock:
                    if self.repository.is_code_message_processed(chat_id, message_id):
                        self.repository.complete_code_job(chat_id, message_id)
                        return
                    await self._writer_gate.run(
                        lambda: self.writer.forward_messages(
                            self.settings.deal1_channel,
                            message_id,
                            self.settings.code_channel,
                        )
                    )
                    self.repository.record_bypass(chat_id, message_id, code)
            else:
                task = await self._build_source_task(code, chat_id, message_id)
                async with self._mutation_lock:
                    if self.repository.is_code_message_processed(chat_id, message_id):
                        self.repository.complete_code_job(chat_id, message_id)
                        return
                    result = await self.service.process(task)
                    LOG.info(
                        "processed code=%s disposition=%s", code, result.disposition
                    )
        except AmbiguousSourceError as error:
            LOG.warning("rejected code=%s: %s", code, error)
            async with self._mutation_lock:
                if not self.repository.is_code_message_processed(chat_id, message_id):
                    self.repository.record_failure(chat_id, message_id, str(error))
        except Exception as error:
            LOG.exception("failed to process code=%s", code)
            async with self._mutation_lock:
                if not self.repository.is_code_message_processed(chat_id, message_id):
                    self.repository.record_failure(chat_id, message_id, str(error))
        self.repository.complete_code_job(chat_id, message_id)

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

        download_results = await asyncio.gather(
            *(self._download_media(message) for message in candidates),
            return_exceptions=True,
        )
        download_error = next(
            (
                result
                for result in download_results
                if isinstance(result, BaseException)
            ),
            None,
        )
        if download_error is not None:
            raise download_error
        downloaded = [
            result
            for result in download_results
            if isinstance(result, MediaInput)
        ]
        deal_hash = next(
            media.sha256
            for message, media in zip(candidates, downloaded, strict=True)
            if message.id == deal_messages[0].id
        )
        return SourceTask(
            code,
            tuple(downloaded),
            deal_hash,
            code_chat_id,
            code_message_id,
        )

    async def _find_source_objects(self, code: str) -> list[SourceObject]:
        async def find() -> list[SourceObject]:
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
                            if item is not None
                            and item.grouped_id == message.grouped_id
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

        return await self._reader_gate.run(find)

    async def _download_media(self, message: Any) -> MediaInput:
        extension = getattr(getattr(message, "file", None), "ext", "") or ""
        temporary = (
            self.settings.media_dir / f"download-{message.id}-{uuid4().hex}{extension}"
        )
        downloaded_path: Path | None = None
        finalized = False
        try:
            downloaded = await self._reader_gate.run(
                lambda: self.reader.download_media(message, file=str(temporary))
            )
            if not downloaded:
                raise RuntimeError(f"failed to download source message {message.id}")
            downloaded_path = Path(downloaded)
            async with self._hash_semaphore:
                sha256 = await asyncio.to_thread(_sha256_file, downloaded_path)
            existing = next(self.settings.media_dir.glob(f"{sha256}.*"), None)
            destination = (
                existing or self.settings.media_dir / f"{sha256}{downloaded_path.suffix}"
            )
            if existing is not None:
                downloaded_path.unlink()
            else:
                downloaded_path.replace(destination)
            finalized = True
            mime_type = getattr(getattr(message, "file", None), "mime_type", None)
            return MediaInput(sha256, destination, mime_type, message.id)
        finally:
            if not finalized:
                temporary.unlink(missing_ok=True)
                if downloaded_path is not None and downloaded_path != temporary:
                    downloaded_path.unlink(missing_ok=True)

    async def _on_up_message(self, event: Any) -> None:
        await self._ready.wait()
        await self._handle_manual_forward(event, "UP")

    async def _on_blacklist_message(self, event: Any) -> None:
        await self._ready.wait()
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
        async with self._mutation_lock:
            group_id = int(row["group_id"])
            if not self.repository.group_is_active(group_id):
                if destination == "UP":
                    await self._writer_gate.run(
                        lambda: self.writer.delete_messages(
                            self.settings.up_channel, [event.message.id]
                        )
                    )
                return
            if destination == "UP":
                await self.service.promote_deal_to_up(group_id)
                await self._writer_gate.run(
                    lambda: self.writer.delete_messages(
                        self.settings.up_channel, [event.message.id]
                    )
                )
            else:
                await self.service.move_deal_to_blacklist(group_id)

    async def _on_up_deleted(self, event: Any) -> None:
        await self._ready.wait()
        async with self._mutation_lock:
            await self.service.hide_deleted_up_messages(event.deleted_ids)
