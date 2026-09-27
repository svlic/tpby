from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"missing required environment variable: {name}")
    return value


def _channel(name: str) -> int | str:
    value = _required(name)
    try:
        return int(value)
    except ValueError:
        return value


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} must be at least 1")
    return value


@dataclass(frozen=True)
class Settings:
    api_id: int
    api_hash: str
    reader_session: str
    writer_session: str
    code_channel: int | str
    source_channel: int | str
    blacklist_channel: int | str
    up_channel: int | str
    deal1_channel: int | str
    deal2_channel: int | str
    database_path: Path
    media_dir: Path
    source_search_limit: int = 100
    source_prepare_concurrency: int = 4
    reader_io_concurrency: int = 4
    media_hash_concurrency: int = 2
    writer_io_concurrency: int = 2

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            api_id=int(_required("TPBY_API_ID")),
            api_hash=_required("TPBY_API_HASH"),
            reader_session=os.getenv("TPBY_READER_SESSION", "sessions/reader"),
            writer_session=os.getenv("TPBY_WRITER_SESSION", "sessions/writer"),
            code_channel=_channel("TPBY_CODE_CHANNEL"),
            source_channel=_channel("TPBY_SOURCE_CHANNEL"),
            blacklist_channel=_channel("TPBY_BLACKLIST_CHANNEL"),
            up_channel=_channel("TPBY_UP_CHANNEL"),
            deal1_channel=_channel("TPBY_DEAL1_CHANNEL"),
            deal2_channel=_channel("TPBY_DEAL2_CHANNEL"),
            database_path=Path(os.getenv("TPBY_DATABASE_PATH", "data/tpby.sqlite3")),
            media_dir=Path(os.getenv("TPBY_MEDIA_DIR", "data/media")),
            source_search_limit=_positive_int("TPBY_SOURCE_SEARCH_LIMIT", 100),
            source_prepare_concurrency=_positive_int(
                "TPBY_SOURCE_PREPARE_CONCURRENCY", 4
            ),
            reader_io_concurrency=_positive_int(
                "TPBY_READER_IO_CONCURRENCY", 4
            ),
            media_hash_concurrency=_positive_int("TPBY_MEDIA_HASH_CONCURRENCY", 2),
            writer_io_concurrency=_positive_int(
                "TPBY_WRITER_IO_CONCURRENCY", 2
            ),
        )
