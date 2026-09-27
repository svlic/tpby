from pathlib import Path

from tpby import telegram
from tpby.config import Settings


def test_application_configures_finite_telegram_retries(
    monkeypatch, tmp_path: Path
) -> None:
    clients = []

    class FakeTelegramClient:
        def __init__(self, *args, **kwargs) -> None:
            clients.append((args, kwargs))

    monkeypatch.setattr(telegram, "TelegramClient", FakeTelegramClient)
    settings = Settings(
        api_id=123,
        api_hash="hash",
        reader_session="reader",
        writer_session="writer",
        code_channel=-1001,
        source_channel=-1002,
        blacklist_channel=-1003,
        up_channel=-1004,
        deal1_channel=-1005,
        deal2_channel=-1006,
        database_path=tmp_path / "tpby.sqlite3",
        media_dir=tmp_path / "media",
    )

    application = telegram.TelegramApplication(settings)

    assert len(clients) == 2
    for _args, options in clients:
        assert options == {
            "request_retries": 3,
            "connection_retries": 5,
            "retry_delay": 2,
            "flood_sleep_threshold": 10,
            "raise_last_call_error": True,
        }
    application.repository.close()
