import asyncio
import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw, ImageOps

from tpby.domain import SourceTask
from tpby.repository import Repository
from tpby.service import RoutingService
from tpby.telegram import AdaptiveTelegramGate, TelegramApplication


def picture() -> Image.Image:
    image = Image.new("RGB", (160, 120), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 15, 60, 100), fill="black")
    draw.ellipse((80, 5, 150, 65), fill="gray")
    draw.line((20, 110, 155, 75), fill="black", width=7)
    return image


def application(tmp_path: Path) -> TelegramApplication:
    class Reader:
        async def download_media(self, message, *, file):
            if file is bytes:
                return message.payload
            shutil.copyfile(message.path, file)
            return file

        async def iter_messages(self, channel):
            for message in self.messages:
                yield message

    app = object.__new__(TelegramApplication)
    app.settings = SimpleNamespace(media_dir=tmp_path / "media", blacklist_channel=-1001)
    app.settings.media_dir.mkdir()
    app.reader = Reader()
    app._reader_gate = AdaptiveTelegramGate("reader", 1)
    app._hash_semaphore = asyncio.Semaphore(2)
    return app


def message(path: Path, message_id: int = 1):
    return SimpleNamespace(
        id=message_id, path=path,
        file=SimpleNamespace(ext=path.suffix, mime_type=None),
    )


@pytest.mark.asyncio
async def test_image_phash_reuses_visual_content_across_encodings(tmp_path: Path) -> None:
    image = picture()
    image.save(tmp_path / "original.png")
    image.save(tmp_path / "same.bmp")
    ImageOps.mirror(image).save(tmp_path / "different.png")
    app = application(tmp_path)

    first = await app._download_media(message(tmp_path / "original.png"))
    same = await app._download_media(message(tmp_path / "same.bmp", 2))
    different = await app._download_media(message(tmp_path / "different.png", 3))

    assert first.media_hash.startswith("image-") and len(first.media_hash) == 22
    assert same.media_hash == first.media_hash
    assert same.path == first.path
    assert different.media_hash != first.media_hash
    assert len(list(app.settings.media_dir.iterdir())) == 2

    repository = Repository(tmp_path / "state.sqlite3")
    with repository.transaction():
        repository.insert_task(SourceTask("111111", (first,), first.media_hash), "DEAL1")
        repository.add_blacklist_hashes({first.media_hash})
    repository.close()
    repository = Repository(tmp_path / "state.sqlite3")
    assert repository.historical_hashes({same.media_hash}) == {first.media_hash}
    assert repository.blacklist_matches({same.media_hash, different.media_hash}) == {first.media_hash}
    repository.close()


@pytest.mark.asyncio
async def test_video_keeps_bytewise_sha256_and_existing_cache(tmp_path: Path) -> None:
    # Hashing a video must not require decoding it or any video tools.
    payload = b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 5000
    original_path = tmp_path / "original.mp4"
    original_path.write_bytes(payload)
    changed_path = tmp_path / "changed.mp4"
    changed_path.write_bytes(payload + b"changed metadata")
    expected = hashlib.sha256(payload).hexdigest()
    app = application(tmp_path)
    cached = app.settings.media_dir / f"{expected}.mp4"
    cached.write_bytes(payload)
    original = await app._download_media(message(original_path))
    changed = await app._download_media(message(changed_path, 2))

    assert original.media_hash == expected
    assert original.path == cached
    assert changed.media_hash == hashlib.sha256(changed_path.read_bytes()).hexdigest()
    assert changed.media_hash != original.media_hash
    assert cached.read_bytes() == payload


def test_legacy_video_database_remains_usable(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    repository = Repository(path)
    with repository.transaction():
        repository.connection.execute(
            "INSERT INTO media(sha256, path, mime_type) VALUES (?, ?, ?)",
            ("a" * 64, "/old.mp4", "video/mp4"),
        )
        repository.add_blacklist_hashes({"a" * 64})
    repository.close()

    repository = Repository(path)
    assert repository.blacklist_matches({"a" * 64}) == {"a" * 64}
    assert repository.connection.execute("SELECT path FROM media").fetchone()[0] == "/old.mp4"
    repository.close()


@pytest.mark.asyncio
async def test_blacklist_metadata_roundtrip_and_legacy_video_support(tmp_path: Path) -> None:
    app = application(tmp_path)
    phash = "image-0123456789abcdef"
    video_hash = "a" * 64
    payload = RoutingService._blacklist_metadata(["111111"], {phash, video_hash}).encode()
    app.reader.messages = [SimpleNamespace(
        id=1, photo=None, video=None, document=True,
        file=SimpleNamespace(name="metadata.txt"), payload=payload,
    )]
    assert await app._scan_blacklist_hashes() == {phash, video_hash}

    app.reader.messages[0].payload = json.dumps({"version": 1, "hashes": [video_hash]}).encode()
    assert await app._scan_blacklist_hashes() == {video_hash}
