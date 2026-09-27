import asyncio
from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest
from telethon import errors

from tpby.domain import GroupSnapshot, MediaInput, StoredTask
from tpby.repository import Repository
from tpby.telegram import (
    AdaptiveTelegramGate,
    TelegramApplication,
    TelegramPublisher,
)


def test_code_jobs_survive_restart_and_remain_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite3"
    repository = Repository(path)
    assert repository.enqueue_code_job(-1001, 10, "111111")
    assert not repository.enqueue_code_job(-1001, 10, "111111")
    assert repository.claim_code_job()["code"] == "111111"
    repository.close()

    repository = Repository(path)
    repository.reset_running_code_jobs()
    recovered = repository.claim_code_job()
    assert (recovered["code_chat_id"], recovered["code_message_id"]) == (-1001, 10)

    repository.record_failure(-1001, 10, "rejected")
    repository.complete_code_job(-1001, 10)
    assert repository.claim_code_job() is None
    assert not repository.enqueue_code_job(-1001, 10, "111111")
    repository.close()


@pytest.mark.asyncio
async def test_adaptive_gate_retries_flood_wait_and_reduces_concurrency(
    monkeypatch,
) -> None:
    gate = AdaptiveTelegramGate("reader", 4)
    gate._limit = 4
    waits: list[int] = []

    async def avoid_real_wait(seconds: int) -> None:
        waits.append(seconds)
        await AdaptiveTelegramGate._on_flood_wait(gate, 0)

    monkeypatch.setattr(gate, "_on_flood_wait", avoid_real_wait)
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise errors.FloodWaitError(None, capture=17)
        return "ok"

    assert await gate.run(operation) == "ok"
    assert attempts == 2
    assert waits == [17]
    assert gate._limit == 2


@pytest.mark.asyncio
async def test_partial_group_upload_removes_confirmed_messages() -> None:
    class FakeWriter:
        def __init__(self) -> None:
            self.send_calls = 0
            self.deleted: list[int] = []

        async def send_file(self, channel, files, caption):
            self.send_calls += 1
            if self.send_calls == 2:
                raise RuntimeError("second chunk failed")
            return [SimpleNamespace(id=index) for index in range(1, len(files) + 1)]

        async def delete_messages(self, channel, message_ids):
            self.deleted.extend(message_ids)

    media = tuple(
        MediaInput(str(index), Path(f"/{index}.bin")) for index in range(11)
    )
    group = GroupSnapshot(
        7,
        "UP",
        (StoredTask(1, "111111", media, media[0].sha256),),
    )
    writer = FakeWriter()
    settings = SimpleNamespace(
        deal1_channel=-1001,
        deal2_channel=-1002,
        up_channel=-1003,
        blacklist_channel=-1004,
    )
    publisher = TelegramPublisher(
        writer, settings, AdaptiveTelegramGate("writer", 1)
    )

    with pytest.raises(RuntimeError, match="second chunk failed"):
        await publisher.publish_group(group)

    assert writer.deleted == list(range(1, 11))


@pytest.mark.asyncio
async def test_source_preparation_overlaps_but_mutations_stay_serial() -> None:
    class FakeRepository:
        def __init__(self) -> None:
            self.completed: list[int] = []

        def is_code_message_processed(self, chat_id: int, message_id: int) -> bool:
            return False

        def complete_code_job(self, chat_id: int, message_id: int) -> None:
            self.completed.append(message_id)

    class FakeService:
        def __init__(self) -> None:
            self.active = 0
            self.maximum = 0

        async def process(self, task):
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            await asyncio.sleep(0)
            self.active -= 1
            return SimpleNamespace(disposition="DEAL1")

    app = object.__new__(TelegramApplication)
    app.repository = FakeRepository()
    app.service = FakeService()
    app._mutation_lock = asyncio.Lock()
    both_preparing = asyncio.Event()
    preparing = 0
    maximum_preparing = 0

    async def build_source_task(self, code, chat_id, message_id):
        nonlocal preparing, maximum_preparing
        preparing += 1
        maximum_preparing = max(maximum_preparing, preparing)
        if preparing == 2:
            both_preparing.set()
        await both_preparing.wait()
        preparing -= 1
        return SimpleNamespace(code=code)

    app._build_source_task = MethodType(build_source_task, app)

    await asyncio.wait_for(
        asyncio.gather(
            app._process_code_job("111111", -1001, 1),
            app._process_code_job("222222", -1001, 2),
        ),
        timeout=1,
    )

    assert maximum_preparing == 2
    assert app.service.maximum == 1
    assert sorted(app.repository.completed) == [1, 2]
