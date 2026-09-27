from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from tpby.telegram import AmbiguousSourceError, TelegramApplication


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
    return app


@pytest.mark.asyncio
async def test_source_search_is_quoted_and_locally_filters_fuzzy_results() -> None:
    app = application(
        [
            message(1, "编号：1234567"),
            message(2, "编号：(123456)"),
            message(3, "编号：123456"),
            message(4, "补充 编号：123456"),
        ]
    )

    objects = await app._find_source_objects("123456")

    assert app.reader.queries == ['"编号：123456"']
    assert all(
        query.startswith('"') and query.endswith('"') for query in app.reader.queries
    )
    assert [item.messages[0].id for item in objects] == [3, 4]


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
