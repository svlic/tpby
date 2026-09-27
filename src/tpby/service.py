from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from .domain import GroupKind, GroupSnapshot, SourceTask
from .repository import Repository


class Publisher(Protocol):
    async def publish_group(
        self, group: GroupSnapshot
    ) -> Sequence[tuple[int, str | None]]: ...

    async def delete_messages(self, messages: Sequence[tuple[str, int]]) -> None: ...

    async def publish_blacklist_metadata(self, content: str) -> None: ...


@dataclass(frozen=True)
class ProcessResult:
    disposition: str
    group_id: int | None = None


class RoutingService:
    def __init__(self, repository: Repository, publisher: Publisher) -> None:
        self.repository = repository
        self.publisher = publisher

    async def process(self, task: SourceTask) -> ProcessResult:
        hashes = task.hashes
        if self.repository.blacklist_matches(hashes):
            await self.publisher.publish_blacklist_metadata(
                self._blacklist_metadata([task.code], hashes)
            )
            with self.repository.transaction():
                self.repository.insert_task(task, "BLACKLIST")
                self.repository.add_blacklist_hashes(hashes)
            return ProcessResult("BLACKLIST")

        if self.repository.active_groups_touching(hashes, ["UP"]):
            old_groups = self.repository.connected_groups(
                hashes, ["UP", "DEAL1", "DEAL2"]
            )
            return await self._replace_groups("UP", old_groups, task)

        if not self.repository.historical_hashes(hashes):
            return await self._replace_groups("DEAL1", [], task)
        old_groups = self.repository.connected_groups(hashes, ["DEAL1", "DEAL2"])
        return await self._replace_groups("DEAL2", old_groups, task)

    async def promote_deal_to_up(self, deal_group_id: int) -> ProcessResult:
        if not self.repository.group_is_active(deal_group_id):
            raise ValueError("DEAL group is no longer active")
        deal = self.repository.get_group(deal_group_id)
        if deal.kind not in {"DEAL1", "DEAL2"}:
            raise ValueError("only an active DEAL group can be promoted")
        group_ids = self.repository.connected_groups(
            deal.hashes, ["UP", "DEAL1", "DEAL2"]
        )
        return await self._replace_groups("UP", group_ids, None)

    async def move_deal_to_blacklist(self, deal_group_id: int) -> ProcessResult:
        if not self.repository.group_is_active(deal_group_id):
            raise ValueError("DEAL group is no longer active")
        deal = self.repository.get_group(deal_group_id)
        if deal.kind not in {"DEAL1", "DEAL2"}:
            raise ValueError("only a DEAL group can be blacklisted")
        messages = self.repository.messages_for_groups([deal_group_id])
        await self.publisher.publish_blacklist_metadata(
            self._blacklist_metadata(deal.codes, deal.hashes)
        )
        with self.repository.transaction():
            self.repository.add_blacklist_hashes(deal.hashes)
            self.repository.deactivate_groups([deal_group_id])
        await self.publisher.delete_messages(messages)
        return ProcessResult("BLACKLIST")

    async def hide_deleted_up_messages(self, message_ids: Sequence[int]) -> None:
        grouped: dict[int, set[str]] = {}
        with self.repository.transaction():
            for message_id in message_ids:
                row = self.repository.lookup_message("UP", message_id)
                if row and row["sha256"]:
                    grouped.setdefault(int(row["group_id"]), set()).add(row["sha256"])
            for group_id, hashes in grouped.items():
                self.repository.add_hidden_hashes(group_id, hashes)

    async def _replace_groups(
        self, kind: GroupKind, old_group_ids: Sequence[int], task: SourceTask | None
    ) -> ProcessResult:
        old_messages = self.repository.messages_for_groups(old_group_ids)
        task_ids = self.repository.task_ids_for_groups(old_group_ids)
        hidden = self.repository.hidden_hashes_for_groups(old_group_ids)
        new_task_id: int | None = None
        with self.repository.transaction():
            if task is not None:
                new_task_id = self.repository.insert_task(task, kind)
                task_ids.append(new_task_id)
            group_id = self.repository.create_group(kind, task_ids, active=False)
            self.repository.add_hidden_hashes(group_id, hidden)

        try:
            group = self.repository.get_group(group_id)
            messages = await self.publisher.publish_group(group)
            with self.repository.transaction():
                self.repository.replace_message_index(group.kind, group_id, messages)
        except BaseException:
            self.repository.discard_staged_group(group_id, new_task_id)
            raise
        with self.repository.transaction():
            self.repository.activate_group(group_id)
            self.repository.deactivate_groups(old_group_ids)
            # Remove UP indexes before deleting old UP messages so deletion events
            # are not interpreted as user-hidden media. Retain DEAL indexes so the
            # remaining events from a manually forwarded album can be recognized.
            self.repository.remove_message_index_for_groups(old_group_ids, "UP")
        if old_group_ids:
            await self.publisher.delete_messages(old_messages)
        return ProcessResult(kind, group_id)

    @staticmethod
    def _blacklist_metadata(codes: Sequence[str], hashes: set[str]) -> str:
        return json.dumps(
            {
                "version": 1,
                "codes": list(codes),
                "hashes": sorted(hashes),
            },
            ensure_ascii=False,
            indent=2,
        )
