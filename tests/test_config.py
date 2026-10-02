from tpby.config import Settings


def test_settings_load_distinct_account_credentials(monkeypatch) -> None:
    values = {
        "TPBY_READER_API_ID": "123",
        "TPBY_READER_API_HASH": "reader-hash",
        "TPBY_WRITER_API_ID": "456",
        "TPBY_WRITER_API_HASH": "writer-hash",
        "TPBY_CODE_CHANNEL": "-1001",
        "TPBY_SOURCE_CHANNEL": "-1002",
        "TPBY_BLACKLIST_CHANNEL": "-1003",
        "TPBY_UP_CHANNEL": "-1004",
        "TPBY_DEAL1_CHANNEL": "-1005",
        "TPBY_DEAL2_CHANNEL": "-1006",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    settings = Settings.from_env()

    assert settings.reader_api_id == 123
    assert settings.reader_api_hash == "reader-hash"
    assert settings.writer_api_id == 456
    assert settings.writer_api_hash == "writer-hash"
