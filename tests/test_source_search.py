from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from tpby.domain import GroupSnapshot, MediaInput, StoredTask
from tpby.telegram import (
    AdaptiveTelegramGate,
    AmbiguousSourceError,
    SourceObject,
    TelegramApplication,
    TelegramPublisher,
)


class FakeReader:
    def __init__(self, messages) -> None:
        self.messages = messages
        self.queries: list[str] = []

    async def iter_messages(self, channel, *, search: str, limit: int):
        self.queries.append(search)
        for message in self.messages:
            yield message


def message(message_id: int, text: str):
    return SimpleNamespace(
        id=message_id,
        message=text,
        grouped_id=None,
        date=datetime(2026, 9, 27, message_id, tzinfo=UTC),
    )


def application(messages) -> TelegramApplication:
    app = object.__new__(TelegramApplication)
    app.reader = FakeReader(messages)
    app.settings = SimpleNamespace(source_channel=-1001, source_search_limit=100)
    app._reader_gate = AdaptiveTelegramGate("reader", 1)
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["123456", "0123456"])
async def test_source_search_is_quoted_and_locally_filters_fuzzy_results(code) -> None:
    app = application(
        [
            message(1, f"正文出现 {code}"),
            message(2, f"编号：{code}7"),
            message(3, f"编号：({code})"),
            message(4, f"编号 : {code}"),
            message(5, f"补充 编号： {code}"),
            message(6, f"编号：{code[:-1]}"),
        ]
    )

    objects = await app._find_source_objects(code)

    assert app.reader.queries == [f'"{code}"']
    assert [item.messages[0].id for item in objects] == [4, 5]


@pytest.mark.asyncio
async def test_more_than_two_locally_verified_objects_is_rejected() -> None:
    app = application(
        [
            message(1, "编号：123456"),
            message(2, "编号：123456"),
            message(3, "编号：123456"),
        ]
    )

    with pytest.raises(AmbiguousSourceError, match="3 locally verified objects"):
        await app._find_source_objects("123456")


@pytest.mark.asyncio
async def test_build_source_task_tracks_deal_media_by_message_id() -> None:
    messages = tuple(
        SimpleNamespace(
            id=message_id,
            message=caption,
            photo=True,
            video=False,
            file=SimpleNamespace(mime_type="video/mp4"),
        )
        for message_id, caption in (
            (1, "编号：123456"),
            (2, "编号：123456（验证视频）"),
            (3, "编号：123456"),
        )
    )
    app = object.__new__(TelegramApplication)

    async def find_source_objects(code: str) -> list[SourceObject]:
        return [SourceObject(messages)]

    async def download_media(source_message) -> MediaInput:
        return MediaInput(str(source_message.id), Path(f"/{source_message.id}.mp4"))

    app._find_source_objects = find_source_objects
    app._download_media = download_media

    task = await app._build_source_task("123456", -1001, 10)

    assert [media.media_hash for media in task.media] == ["1", "2", "3"]
    assert task.deal_media_hash == "2"


@pytest.mark.asyncio
async def test_published_mixed_length_codes_survive_up_recovery() -> None:
    messages = []

    class Channel:
        async def send_file(self, channel, files, caption):
            assert caption == "123456\n0123456"
            sent = [
                SimpleNamespace(id=1, grouped_id=None, media=True, message=caption)
            ]
            messages.extend(sent)
            return sent

        async def iter_messages(self, channel):
            for item in messages:
                yield item

    channel = Channel()
    settings = SimpleNamespace(
        deal1_channel=-1001,
        deal2_channel=-1002,
        up_channel=-1003,
        blacklist_channel=-1004,
    )
    media = MediaInput("A", Path("/A.mp4"))
    group = GroupSnapshot(
        1,
        "UP",
        (
            StoredTask(1, "123456", (media,), "A"),
            StoredTask(2, "0123456", (media,), "A"),
        ),
    )
    publisher = TelegramPublisher(channel, settings, AdaptiveTelegramGate("writer", 1))
    await publisher.publish_group(group)
    messages[0].message += "\n12345678\n（260927）\n0123456"

    app = object.__new__(TelegramApplication)
    app.reader = channel
    app.settings = settings
    app._reader_gate = AdaptiveTelegramGate("reader", 1)

    async def download_media(source_message):
        return media

    app._download_media = download_media

    recovered = await app._scan_up_groups()

    assert recovered == {("123456", "0123456"): [(1, media)]}
