from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tpby.domain import GroupSnapshot, MediaInput, SourceTask
from tpby.repository import Repository
from tpby.service import RoutingService


class FakePublisher:
    def __init__(self) -> None:
        self.next_message_id = 100
        self.groups: list[GroupSnapshot] = []
        self.deleted: list[tuple[str, int]] = []
        self.delete_calls = 0
        self.metadata: list[dict[str, object]] = []

    async def publish_group(self, group: GroupSnapshot) -> list[tuple[int, str]]:
        self.groups.append(group)
        result = []
        for media in group.display_media():
            self.next_message_id += 1
            result.append((self.next_message_id, media.sha256))
        return result

    async def delete_messages(self, messages: list[tuple[str, int]]) -> None:
        self.delete_calls += 1
        self.deleted.extend(messages)

    async def publish_blacklist_metadata(self, content: str) -> None:
        self.metadata.append(json.loads(content))


class FailingPublisher(FakePublisher):
    async def publish_group(self, group: GroupSnapshot) -> list[tuple[int, str]]:
        raise RuntimeError("upload failed")


class CancellingPublisher(FakePublisher):
    async def publish_group(self, group: GroupSnapshot) -> list[tuple[int, str]]:
        raise asyncio.CancelledError


def task(code: str, hashes: list[str], deal: str, message_id: int) -> SourceTask:
    return SourceTask(
        code,
        tuple(MediaInput(value, Path(f"/{value}.bin")) for value in hashes),
        deal,
        -1001,
        message_id,
    )


@pytest.fixture
def system(tmp_path: Path) -> tuple[Repository, FakePublisher, RoutingService]:
    repository = Repository(tmp_path / "test.sqlite3")
    publisher = FakePublisher()
    return repository, publisher, RoutingService(repository, publisher)


def test_repository_preserves_media_mime_type(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "test.sqlite3")
    source = SourceTask(
        "111111",
        (MediaInput("A", Path("/A.mp4"), "video/mp4"),),
        "A",
    )
    with repository.transaction():
        task_id = repository.insert_task(source, "UP")
        group_id = repository.create_group("UP", [task_id])

    assert repository.get_group(group_id).tasks[0].media[0].mime_type == "video/mp4"


def test_repository_get_group_preserves_task_and_media_order(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "test.sqlite3")
    first = SourceTask(
        "111111",
        (
            MediaInput("A", Path("/A.mp4"), telegram_message_id=12),
            MediaInput("B", Path("/B.mp4"), telegram_message_id=11),
        ),
        "B",
    )
    second = SourceTask("222222", (MediaInput("C", Path("/C.mp4")),), "C")
    with repository.transaction():
        first_id = repository.insert_task(first, "UP")
        second_id = repository.insert_task(second, "UP")
        group_id = repository.create_group("UP", [second_id, first_id])

    group = repository.get_group(group_id)

    assert group.codes == ["111111", "222222"]
    assert [item.sha256 for item in group.tasks[0].media] == ["A", "B"]
    assert [item.telegram_message_id for item in group.tasks[0].media] == [12, 11]


def test_repository_empty_queries_are_noops(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "test.sqlite3")

    assert repository.historical_hashes(set()) == set()
    assert repository.blacklist_matches(set()) == set()
    assert repository.active_groups_touching(set()) == []
    assert repository.task_ids_for_groups([]) == []
    assert repository.hidden_hashes_for_groups([]) == set()
    assert repository.messages_for_groups([]) == []
    repository.deactivate_groups([])
    repository.remove_message_index_for_groups([])


@pytest.mark.asyncio
async def test_new_task_goes_to_deal1_with_only_deal_media(system) -> None:
    repository, publisher, service = system
    result = await service.process(task("111111", ["A", "B", "A"], "B", 1))

    assert result.disposition == "DEAL1"
    group = repository.get_group(result.group_id)
    assert group.hashes == {"A", "B"}
    assert [item.sha256 for item in publisher.groups[-1].display_media()] == ["B"]
    assert publisher.delete_calls == 0


@pytest.mark.asyncio
async def test_replacement_calls_delete_even_when_old_index_is_empty(system) -> None:
    repository, publisher, service = system
    first = await service.process(task("111111", ["A"], "A", 1))
    with repository.transaction():
        repository.remove_message_index_for_groups([first.group_id])

    await service.process(task("222222", ["A"], "A", 2))

    assert publisher.delete_calls == 1
    assert publisher.deleted == []


@pytest.mark.asyncio
async def test_repeat_upgrades_all_connected_deal1_groups_to_deal2(system) -> None:
    repository, publisher, service = system
    first = await service.process(task("111111", ["A", "B"], "B", 1))
    second = await service.process(task("222222", ["C", "D"], "D", 2))
    merged = await service.process(task("333333", ["B", "C", "E"], "E", 3))

    group = repository.get_group(merged.group_id)
    assert merged.disposition == "DEAL2"
    assert group.codes == ["111111", "222222", "333333"]
    assert group.hashes == {"A", "B", "C", "D", "E"}
    assert [item.sha256 for item in group.display_media()] == ["B", "D", "E"]
    assert not repository.group_is_active(first.group_id)
    assert not repository.group_is_active(second.group_id)
    assert len(publisher.deleted) == 2


@pytest.mark.asyncio
async def test_blacklist_match_expands_to_whole_current_task(system) -> None:
    repository, publisher, service = system
    with repository.transaction():
        repository.add_blacklist_hashes({"B"})

    result = await service.process(task("111111", ["A", "B", "C"], "C", 1))

    assert result.disposition == "BLACKLIST"
    assert repository.blacklist_matches({"A", "B", "C"}) == {"A", "B", "C"}
    assert publisher.metadata[-1]["hashes"] == ["A", "B", "C"]
    assert not publisher.groups


@pytest.mark.asyncio
async def test_up_merge_absorbs_connected_deals_and_preserves_hidden_hash(
    system,
) -> None:
    repository, _publisher, service = system
    deal = await service.process(task("111111", ["A", "B"], "B", 1))
    promoted = await service.promote_deal_to_up(deal.group_id)
    up_messages = [
        message_id
        for kind, message_id in repository.messages_for_groups([promoted.group_id])
        if kind == "UP"
    ]
    await service.hide_deleted_up_messages([up_messages[0]])

    other_deal = await service.process(task("222222", ["C", "D"], "D", 2))
    merged = await service.process(task("333333", ["B", "C", "E"], "E", 3))

    group = repository.get_group(merged.group_id)
    assert merged.disposition == "UP"
    assert group.codes == ["111111", "222222", "333333"]
    assert group.hashes == {"A", "B", "C", "D", "E"}
    assert "A" in group.hidden_hashes
    assert "A" not in {item.sha256 for item in group.display_media()}
    assert not repository.group_is_active(other_deal.group_id)


@pytest.mark.asyncio
async def test_manual_deal_to_blacklist_uses_full_hash_set(system) -> None:
    repository, publisher, service = system
    deal = await service.process(task("111111", ["A", "B", "C"], "B", 1))

    result = await service.move_deal_to_blacklist(deal.group_id)

    assert result.disposition == "BLACKLIST"
    assert repository.blacklist_matches({"A", "B", "C"}) == {"A", "B", "C"}
    assert publisher.metadata[-1]["codes"] == ["111111"]
    assert len(publisher.deleted) == 1


@pytest.mark.asyncio
async def test_failed_replacement_keeps_old_group_active(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "test.sqlite3")
    working_service = RoutingService(repository, FakePublisher())
    deal = await working_service.process(task("111111", ["A", "B"], "B", 1))
    up = await working_service.promote_deal_to_up(deal.group_id)
    failing_service = RoutingService(repository, FailingPublisher())

    with pytest.raises(RuntimeError, match="upload failed"):
        await failing_service.process(task("222222", ["B", "C"], "C", 2))

    assert repository.group_is_active(up.group_id)
    assert repository.historical_hashes({"C"}) == set()


@pytest.mark.asyncio
async def test_cancelled_publish_discards_staged_task(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "test.sqlite3")
    service = RoutingService(repository, CancellingPublisher())

    with pytest.raises(asyncio.CancelledError):
        await service.process(task("111111", ["A", "B"], "B", 1))

    assert repository.historical_hashes({"A", "B"}) == set()
    assert not repository.is_code_message_processed(-1001, 1)
