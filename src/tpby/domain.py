from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

CODE_PATTERN = re.compile(r"(?<![\d(（])\d{6}(?![\d)）])")
NUMBERED_CODE_PATTERN = re.compile(r"编号\s*[:：]\s*(?P<code>\d{6})(?![\d)）])")
DEAL_MEDIA_PATTERN = re.compile(r"[（(]\s*验证视频\s*[）)]")

GroupKind = Literal["DEAL1", "DEAL2", "UP"]


def extract_code(text: str) -> str | None:
    match = CODE_PATTERN.search(text or "")
    return match.group(0) if match else None


def contains_code(text: str, code: str) -> bool:
    return any(match.group(0) == code for match in CODE_PATTERN.finditer(text or ""))


def contains_numbered_code(text: str, code: str) -> bool:
    return any(
        match.group("code") == code
        for match in NUMBERED_CODE_PATTERN.finditer(text or "")
    )


def is_recent_date_code(code: str, today: date | None = None) -> bool:
    today = today or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    try:
        parsed = date(2000 + int(code[:2]), int(code[2:4]), int(code[4:6]))
    except ValueError:
        return False

    lower_bound = today - timedelta(days=180)
    return lower_bound <= parsed <= today


def is_deal_media_caption(text: str) -> bool:
    return bool(DEAL_MEDIA_PATTERN.search(text or ""))


@dataclass(frozen=True)
class MediaInput:
    sha256: str
    path: Path
    mime_type: str | None = None
    telegram_message_id: int | None = None


@dataclass(frozen=True)
class SourceTask:
    code: str
    media: tuple[MediaInput, ...]
    deal_media_hash: str
    code_chat_id: int | None = None
    code_message_id: int | None = None

    def __post_init__(self) -> None:
        hashes = {item.sha256 for item in self.media}
        if not hashes:
            raise ValueError("source task has no media")
        if self.deal_media_hash not in hashes:
            raise ValueError("deal media must belong to source media")

    @property
    def hashes(self) -> set[str]:
        return {item.sha256 for item in self.media}


@dataclass(frozen=True)
class StoredTask:
    id: int
    code: str
    media: tuple[MediaInput, ...]
    deal_media_hash: str


@dataclass(frozen=True)
class GroupSnapshot:
    id: int
    kind: GroupKind
    tasks: tuple[StoredTask, ...]
    hidden_hashes: frozenset[str] = frozenset()

    @property
    def codes(self) -> list[str]:
        return list(dict.fromkeys(task.code for task in self.tasks))

    @property
    def hashes(self) -> set[str]:
        return {media.sha256 for task in self.tasks for media in task.media}

    def display_media(self) -> list[MediaInput]:
        if self.kind in {"DEAL1", "DEAL2"}:
            wanted = [task.deal_media_hash for task in self.tasks]
        else:
            wanted = [
                media.sha256
                for task in self.tasks
                for media in task.media
                if media.sha256 not in self.hidden_hashes
            ]

        by_hash = {media.sha256: media for task in self.tasks for media in task.media}
        return [by_hash[value] for value in dict.fromkeys(wanted) if value in by_hash]
