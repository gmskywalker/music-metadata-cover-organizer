#!/usr/bin/env python3
"""Safely complete core music tags and embed verified album artwork.

Run without --apply for a read-only preview. The companion .cmd file runs it
with --apply after asking for confirmation.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import io
import json
import os
import re
import shutil
import struct
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable


FROZEN = bool(getattr(sys, "frozen", False))
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
SCRIPT_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
WORK_DIR = PROJECT_DIR / "work"
APP_DATA_DIR = SCRIPT_DIR / "音乐整理工具数据"
CACHE_DIR = APP_DATA_DIR / "封面缓存"
RECORD_DIR = APP_DATA_DIR / "执行记录"
SETTINGS_PATH = APP_DATA_DIR / "设置.json"
DEFAULT_MUSIC_DIR = Path(r"D:\gmskywalker\Music")
REVIEWED_SEED = (
    BUNDLE_DIR / "reviewed_cover_seed.json"
    if (BUNDLE_DIR / "reviewed_cover_seed.json").exists()
    else WORK_DIR / "reviewed_cover_seed.json"
)

if not FROZEN:
    sys.path.insert(0, str(WORK_DIR / "vendor"))

from PIL import Image, ImageOps  # noqa: E402
from mutagen.aac import AAC  # noqa: E402
from mutagen.asf import ASF  # noqa: E402
from mutagen.flac import FLAC, Picture  # noqa: E402
from mutagen.id3 import (  # noqa: E402
    APIC,
    ID3,
    ID3NoHeaderError,
    TALB,
    TDOR,
    TDRC,
    TIT2,
    TPE1,
    TPE2,
)
from mutagen.mp3 import MP3  # noqa: E402
from mutagen.mp4 import MP4, MP4Cover  # noqa: E402


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "CodexPersonalMusicLibrary/2.0"
)
CORE_FIELDS = ("TITLE", "ARTIST", "ALBUM", "ALBUMARTIST", "YEAR", "DATE")
DATE_RE = re.compile(r"^(?:19|20)\d{2}(?:-\d{2}(?:-\d{2})?)?$")
IMAGE_MIME = {"JPEG": "image/jpeg", "PNG": "image/png"}
CATALOG_CACHE_VERSION = "2"
ARTIST_PHOTO_CACHE_VERSION = "3"
ProgressCallback = Callable[[str, int, int, str], None]
_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST: dict[str, float] = {}
_PROVIDER_BLOCKED_UNTIL: dict[str, float] = {}


SPECIAL_METADATA = {
    "伍佰 & China Blue-美丽新世界 (2015台北演唱会).mp3": {
        "TITLE": "美丽新世界 (Live)",
        "ARTIST": "伍佰 & China Blue",
        "ALBUM": "光和热：“无尽闪亮的世界”台北演唱会影音全纪录",
        "ALBUMARTIST": "伍佰 & China Blue",
        "YEAR": "2015",
        "DATE": "2015-06-19",
    },
    "陈奕迅-谁知我这种男孩子(来不及听你说爱我插曲)-《野孩子》改编.mp3": {
        "TITLE": "谁知我这种男孩子（《野孩子》改编片段）",
        "ARTIST": "陈奕迅",
        "ALBUM": "903移动剧场《来不及听你说爱我》",
        "ALBUMARTIST": "陈奕迅",
        "YEAR": "2006",
        "DATE": "2006",
    },
    "S.H.E-Only Lonely.flac": {
        "TITLE": "Only Lonely",
        "ARTIST": "S.H.E",
        "ALBUM": "奇幻旅程",
        "ALBUMARTIST": "S.H.E",
        "YEAR": "2004",
        "DATE": "2004-02-06",
    },
    "S.H.E-Super Star.flac": {
        "TITLE": "Super Star",
        "ARTIST": "S.H.E",
        "ALBUM": "Super Star",
        "ALBUMARTIST": "S.H.E",
        "YEAR": "2003",
        "DATE": "2003-08-22",
    },
    "S.H.E-十面埋伏.flac": {
        "TITLE": "十面埋伏",
        "ARTIST": "S.H.E",
        "ALBUM": "奇幻旅程",
        "ALBUMARTIST": "S.H.E",
        "YEAR": "2004",
        "DATE": "2004-02-06",
    },
    "孙燕姿-雨天.flac": {
        "TITLE": "雨天",
        "ARTIST": "孙燕姿",
        "ALBUM": "My Story 2006 新歌+精选",
        "ALBUMARTIST": "孙燕姿",
        "YEAR": "2006",
        "DATE": "2006-09-21",
    },
    "张信哲-爱就一个字.flac": {
        "TITLE": "爱就一个字",
        "ARTIST": "张信哲",
        "ALBUM": "宝莲灯 电影原声音乐",
        "ALBUMARTIST": "张信哲",
        "YEAR": "1999",
        "DATE": "1999-09-01",
    },
    "张国荣-倩女幽魂.flac": {
        "TITLE": "倩女幽魂",
        "ARTIST": "张国荣",
        "ALBUM": "Summer Romance 87",
        "ALBUMARTIST": "张国荣",
        "YEAR": "1987",
        "DATE": "1987-08-21",
    },
    "张国荣-拒绝再玩.flac": {
        "TITLE": "拒绝再玩",
        "ARTIST": "张国荣",
        "ALBUM": "Summer Romance 87",
        "ALBUMARTIST": "张国荣",
        "YEAR": "1987",
        "DATE": "1987-08-21",
    },
    "张学友&高慧君-你最珍贵.flac": {
        "TITLE": "你最珍贵",
        "ARTIST": "张学友&高慧君",
        "ALBUM": "不後悔",
        "ALBUMARTIST": "张学友&高慧君",
        "YEAR": "1998",
        "DATE": "1998-09-22",
    },
    "林忆莲-至少还有你.flac": {
        "TITLE": "至少还有你",
        "ARTIST": "林忆莲",
        "ALBUM": "林忆莲's",
        "ALBUMARTIST": "林忆莲",
        "YEAR": "2000",
        "DATE": "2000-01-18",
    },
    "林志炫-你的样子.flac": {
        "TITLE": "你的样子",
        "ARTIST": "林志炫",
        "ALBUM": "一个人的样子",
        "ALBUMARTIST": "林志炫",
        "YEAR": "1995",
        "DATE": "1995-12-01",
    },
    "林隆璇&周慧敏-流言.flac": {
        "TITLE": "流言",
        "ARTIST": "林隆璇&周慧敏",
        "ALBUM": "不够大胆",
        "ALBUMARTIST": "林隆璇&周慧敏",
        "YEAR": "1992",
        "DATE": "1992-03-04",
    },
    "王蓝茵-恶作剧.flac": {
        "TITLE": "恶作剧",
        "ARTIST": "王蓝茵",
        "ALBUM": "恶作剧之吻 电视剧原声带",
        "ALBUMARTIST": "王蓝茵",
        "YEAR": "2005",
        "DATE": "2005-10-14",
    },
    "田馥甄-小幸运.flac": {
        "TITLE": "小幸运",
        "ARTIST": "田馥甄",
        "ALBUM": "我的少女时代 电影原声带",
        "ALBUMARTIST": "田馥甄",
        "YEAR": "2015",
        "DATE": "2015-10-21",
    },
    "蔡依林-日不落.flac": {
        "TITLE": "日不落",
        "ARTIST": "蔡依林",
        "ALBUM": "特务J",
        "ALBUMARTIST": "蔡依林",
        "YEAR": "2007",
        "DATE": "2007-09-21",
    },
    "谢安琪-钟无艳.flac": {
        "TITLE": "钟无艳",
        "ARTIST": "谢安琪",
        "ALBUM": "3/8 (新歌+精选)",
        "ALBUMARTIST": "谢安琪",
        "YEAR": "2007",
        "DATE": "2007-12-11",
    },
    "郑伊健-甘心替代你.flac": {
        "TITLE": "甘心替代你",
        "ARTIST": "郑伊健",
        "ALBUM": "古惑仔 最强精选集 电影原声带",
        "ALBUMARTIST": "郑伊健",
        "YEAR": "2001",
        "DATE": "2001-03-12",
    },
    "陈奕迅、王菲-因为爱情.flac": {
        "TITLE": "因为爱情",
        "ARTIST": "陈奕迅、王菲",
        "ALBUM": "Stranger Under My Skin",
        "ALBUMARTIST": "陈奕迅、王菲",
        "YEAR": "2011",
        "DATE": "2011-02-22",
    },
}


SPECIAL_CATALOGS_BY_FILENAME = {
    "林忆莲、梅艳芳-两个女人.flac": {
        "provider": "apple-hk",
        "id": 1596893264,
        "artist": "梅艳芳 & 林忆莲",
        "album": "With",
        "release_date": "1990-01-01",
        "artwork_url": "https://is1-ssl.mzstatic.com/image/thumb/Music126/v4/91/2a/48/912a4832-3243-8363-7a8f-bb3b9f53665a/12UMGIM50182.rgb.jpg/1000x1000bb.jpg",
        "catalog_url": "https://music.apple.com/hk/album/with/1596893264",
        "score": 0.88,
        "album_similarity": 1.0,
        "artist_similarity": 0.5,
    },
    "陈百强-一生何求.flac": {
        "provider": "qqmusic",
        "id": "000jLmMB3DapVe",
        "artist": "陈百强",
        "album": "Danny Chan - True Legend",
        "release_date": "2013-08-28",
        "artwork_url": "https://y.gtimg.cn/music/photo_new/T002R800x800M000000jLmMB3DapVe_3.jpg",
        "catalog_url": "https://y.qq.com/n/ryqq/albumDetail/000jLmMB3DapVe",
        "score": 0.87,
        "album_similarity": 0.82,
        "artist_similarity": 1.0,
    },
    "陈百强-偏偏喜欢你.flac": {
        "provider": "qqmusic",
        "id": "000jLmMB3DapVe",
        "artist": "陈百强",
        "album": "Danny Chan - True Legend",
        "release_date": "2013-08-28",
        "artwork_url": "https://y.gtimg.cn/music/photo_new/T002R800x800M000000jLmMB3DapVe_3.jpg",
        "catalog_url": "https://y.qq.com/n/ryqq/albumDetail/000jLmMB3DapVe",
        "score": 0.87,
        "album_similarity": 0.82,
        "artist_similarity": 1.0,
    },
    "陈百强-念亲恩.flac": {
        "provider": "qqmusic",
        "id": "000jLmMB3DapVe",
        "artist": "陈百强",
        "album": "Danny Chan - True Legend",
        "release_date": "2013-08-28",
        "artwork_url": "https://y.gtimg.cn/music/photo_new/T002R800x800M000000jLmMB3DapVe_3.jpg",
        "catalog_url": "https://y.qq.com/n/ryqq/albumDetail/000jLmMB3DapVe",
        "score": 0.87,
        "album_similarity": 0.82,
        "artist_similarity": 1.0,
    },
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, path)


def to_simplified(text: str) -> str:
    text = text or ""
    if not text or os.name != "nt":
        return text
    needed = ctypes.windll.kernel32.LCMapStringEx(
        "zh-CN", 0x02000000, text, len(text), None, 0, None, None, 0
    )
    if needed <= 0:
        return text
    buffer = ctypes.create_unicode_buffer(needed)
    written = ctypes.windll.kernel32.LCMapStringEx(
        "zh-CN", 0x02000000, text, len(text), buffer, needed, None, None, 0
    )
    return buffer.value[:written] if written > 0 else text


def normalized(text: str) -> str:
    text = to_simplified(unicodedata.normalize("NFKC", text or "")).casefold()
    text = text.replace("＆", "&").replace("·", "")
    text = re.sub(
        r"(?:remaster(?:ed)?|deluxe|k2hd|黑胶|复黑|纪念版|hong kong version)",
        "",
        text,
        flags=re.I,
    )
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", text)


def similarity(left: str, right: str) -> float:
    left_norm = normalized(left)
    right_norm = normalized(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    if left_norm in right_norm or right_norm in left_norm:
        return 0.62 + 0.38 * min(len(left_norm), len(right_norm)) / max(
            len(left_norm), len(right_norm)
        )
    return SequenceMatcher(None, left_norm, right_norm).ratio()


def split_artists(text: str) -> list[str]:
    return [
        part.strip()
        for part in re.split(
            r"\s*(?:&|＆|、|,|，|/|\+|\band\b|feat\.?|ft\.?)\s*",
            text or "",
            flags=re.I,
        )
        if part.strip()
    ]


def artist_similarity(expected: str, actual: str) -> float:
    direct = similarity(expected, actual)
    expected_parts = split_artists(expected)
    actual_parts = split_artists(actual)
    if not expected_parts or not actual_parts:
        return direct
    coverage = sum(
        max(similarity(expected_part, actual_part) for actual_part in actual_parts)
        for expected_part in expected_parts
    ) / len(expected_parts)
    reverse = sum(
        max(similarity(actual_part, expected_part) for expected_part in expected_parts)
        for actual_part in actual_parts
    ) / len(actual_parts)
    return max(direct, min(coverage, reverse), 0.85 * coverage)


def title_without_version(text: str) -> str:
    text = to_simplified(unicodedata.normalize("NFKC", text or ""))
    text = re.sub(
        r"[（(][^）)]*(?:live|现场|演唱会|国语|粤语|remaster)[^）)]*[）)]",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(r"\s+(?:live|remaster(?:ed)?)\s*$", "", text, flags=re.I)
    return text.strip()


def parse_filename(filename: str) -> tuple[str, str]:
    stem = Path(filename).stem.strip()
    if stem == "张学友不老的传说":
        return "张学友", "不老的传说"
    if "-" not in stem:
        return "", stem
    artist, title = stem.split("-", 1)
    if normalized(artist) in {"大时代小过客粤", "下一站天后"}:
        return title.strip(), artist.strip()
    return artist.strip(), title.strip()


def first_tag(tags: Any, *keys: str) -> str:
    if not tags:
        return ""
    folded = {str(key).casefold(): key for key in tags.keys()}
    for wanted in keys:
        actual_key = folded.get(wanted.casefold())
        if actual_key is None:
            continue
        value = tags[actual_key]
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ""
        if hasattr(value, "text"):
            value = value.text
            if isinstance(value, (list, tuple)):
                value = value[0] if value else ""
        return str(value).strip()
    return ""


def open_audio(path: Path) -> Any:
    with path.open("rb") as handle:
        header = handle.read(32)
        post_id3 = b""
        if header.startswith(b"ID3") and len(header) >= 10:
            size = (
                (header[6] & 0x7F) << 21
                | (header[7] & 0x7F) << 14
                | (header[8] & 0x7F) << 7
                | (header[9] & 0x7F)
            )
            handle.seek(10 + size)
            post_id3 = handle.read(4)
    if header.startswith(b"fLaC"):
        return FLAC(path)
    if header.startswith(b"\x30\x26\xb2\x75\x8e\x66\xcf\x11"):
        return ASF(path)
    if len(header) >= 8 and header[4:8] == b"ftyp":
        return MP4(path)
    if (
        len(header) >= 2
        and header[0] == 0xFF
        and (header[1] & 0xF6) == 0xF0
    ) or (
        post_id3
        and post_id3[0] == 0xFF
        and (post_id3[1] & 0xF6) == 0xF0
    ):
        return AAC(path)
    if header.startswith(b"ID3") or (
        len(header) >= 2
        and header[0] == 0xFF
        and (header[1] & 0xE0) == 0xE0
        and (header[1] & 0x06) != 0
    ):
        return MP3(path)
    return None


def id3_for(path: Path, create: bool = False) -> ID3 | None:
    try:
        return ID3(path)
    except ID3NoHeaderError:
        return ID3() if create else None


def read_metadata(path: Path, audio: Any) -> dict[str, str]:
    kind = type(audio).__name__
    tags = audio.tags
    if kind == "FLAC":
        result = {
            "TITLE": first_tag(tags, "title"),
            "ARTIST": first_tag(tags, "artist"),
            "ALBUM": first_tag(tags, "album"),
            "ALBUMARTIST": first_tag(tags, "albumartist", "album artist"),
            "YEAR": first_tag(tags, "year"),
            "DATE": first_tag(tags, "date"),
        }
    elif kind in {"MP3", "AAC"}:
        id3 = id3_for(path)
        result = {
            "TITLE": first_tag(id3, "TIT2"),
            "ARTIST": first_tag(id3, "TPE1"),
            "ALBUM": first_tag(id3, "TALB"),
            "ALBUMARTIST": first_tag(id3, "TPE2"),
            "YEAR": first_tag(id3, "TDRC", "TYER"),
            "DATE": first_tag(id3, "TDOR", "TORY", "TDRC"),
        }
    elif kind == "MP4":
        day = first_tag(tags, "\xa9day")
        result = {
            "TITLE": first_tag(tags, "\xa9nam"),
            "ARTIST": first_tag(tags, "\xa9ART"),
            "ALBUM": first_tag(tags, "\xa9alb"),
            "ALBUMARTIST": first_tag(tags, "aART"),
            "YEAR": day[:4],
            "DATE": day,
        }
    elif kind == "ASF":
        result = {
            "TITLE": first_tag(tags, "Title"),
            "ARTIST": first_tag(tags, "Author", "WM/Artist"),
            "ALBUM": first_tag(tags, "WM/AlbumTitle"),
            "ALBUMARTIST": first_tag(tags, "WM/AlbumArtist"),
            "YEAR": first_tag(tags, "WM/Year"),
            "DATE": first_tag(
                tags, "WM/OriginalReleaseTime", "WM/ReleaseDate", "WM/Year"
            ),
        }
    else:
        result = {field: "" for field in CORE_FIELDS}
    return {field: (result.get(field) or "").strip() for field in CORE_FIELDS}


def read_cover(path: Path, audio: Any) -> tuple[bytes | None, str | None, int]:
    kind = type(audio).__name__
    if kind == "FLAC":
        pictures = list(audio.pictures)
        if not pictures:
            return None, None, 0
        front = next((picture for picture in pictures if picture.type == 3), pictures[0])
        return bytes(front.data), front.mime or "image/jpeg", len(pictures)
    if kind in {"MP3", "AAC"}:
        id3 = id3_for(path)
        pictures = [] if id3 is None else id3.getall("APIC")
        if not pictures:
            return None, None, 0
        front = next((picture for picture in pictures if picture.type == 3), pictures[0])
        return bytes(front.data), front.mime or "image/jpeg", len(pictures)
    if kind == "MP4":
        pictures = [] if not audio.tags else list(audio.tags.get("covr", []))
        if not pictures:
            return None, None, 0
        data = bytes(pictures[0])
        mime = "image/png" if getattr(pictures[0], "imageformat", None) == MP4Cover.FORMAT_PNG else "image/jpeg"
        return data, mime, len(pictures)
    if kind == "ASF":
        pictures = [] if not audio.tags else list(audio.tags.get("WM/Picture", []))
        return None, None, len(pictures)
    return None, None, 0


def scan_paths(
    paths: list[Path],
    progress: ProgressCallback | None = None,
    phase: str = "scan",
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    tracks = []
    errors = []
    unique_paths = {
        path.resolve()
        for path in paths
        if path.is_file() and path.name.casefold() != "desktop.ini"
    }
    ordered_paths = sorted(unique_paths, key=lambda item: str(item).casefold())
    total = len(ordered_paths)
    for index, path in enumerate(ordered_paths, 1):
        if progress:
            progress(phase, index - 1, total, path.name)
        try:
            audio = open_audio(path)
            if audio is None:
                raise ValueError("unrecognized or unsupported audio content")
            cover_data, cover_mime, cover_count = read_cover(path, audio)
            tracks.append(
                {
                    "path": path,
                    "filename": path.name,
                    "type": type(audio).__name__,
                    "size": path.stat().st_size,
                    "mtime_ns": path.stat().st_mtime_ns,
                    "duration_ms": round(audio.info.length * 1000)
                    if getattr(audio.info, "length", None)
                    else None,
                    "metadata": read_metadata(path, audio),
                    "cover_data": cover_data,
                    "cover_mime": cover_mime,
                    "cover_count": cover_count,
                }
            )
        except Exception as exc:
            errors.append({"path": str(path), "error": f"{type(exc).__name__}: {exc}"})
        if progress:
            progress(phase, index, total, path.name)
    return tracks, errors


def scan_music(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    return scan_paths([item for item in root.iterdir() if item.is_file()])


def load_reviewed_seed() -> dict[str, dict[str, Any]]:
    if not REVIEWED_SEED.exists():
        return {}
    payload = json.loads(REVIEWED_SEED.read_text(encoding="utf-8-sig"))
    return payload.get("tracks", {})


def initial_target_metadata(track: dict[str, Any], reviewed: dict[str, dict[str, Any]]) -> tuple[dict[str, str], str]:
    if track["filename"] in SPECIAL_METADATA:
        return dict(SPECIAL_METADATA[track["filename"]]), "special-reviewed"
    if track["filename"] in reviewed:
        return dict(reviewed[track["filename"]]["metadata"]), "previously-reviewed"

    current = dict(track["metadata"])
    file_artist, file_title = parse_filename(track["filename"])
    if not current["TITLE"]:
        current["TITLE"] = file_title
    if not current["ARTIST"]:
        current["ARTIST"] = current["ALBUMARTIST"] or file_artist
    if not current["ALBUMARTIST"] and current["ARTIST"]:
        current["ALBUMARTIST"] = current["ARTIST"]
    if current["ARTIST"]:
        current["ALBUMARTIST"] = current["ARTIST"]
    if not current["YEAR"] and DATE_RE.match(current["DATE"] or ""):
        current["YEAR"] = current["DATE"][:4]
    if not current["DATE"] and re.fullmatch(r"(?:19|20)\d{2}", current["YEAR"] or ""):
        current["DATE"] = current["YEAR"]
    return current, "existing-safe-fill"


def request_bytes(url: str, attempts: int = 3, timeout: int = 25) -> bytes:
    last_error: Exception | None = None
    for attempt in range(attempts):
        if "itunes.apple.com" in url:
            bucket, interval = "apple-api", 0.45
        elif "music.163.com" in url:
            bucket, interval = "netease-api", 0.20
        elif "c.y.qq.com" in url:
            bucket, interval = "qq-api", 0.20
        else:
            bucket, interval = "images", 0.03
        with _REQUEST_LOCK:
            blocked_until = _PROVIDER_BLOCKED_UNTIL.get(bucket, 0.0)
            if time.monotonic() < blocked_until:
                raise RuntimeError(f"{bucket} is cooling down after rate limiting")
            elapsed = time.monotonic() - _LAST_REQUEST.get(bucket, 0.0)
            if elapsed < interval:
                time.sleep(interval - elapsed)
            _LAST_REQUEST[bucket] = time.monotonic()
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json,image/avif,image/webp,image/apng,image/*,*/*",
                "Referer": (
                    "https://music.apple.com/"
                    if "mzstatic.com" in url
                    else "https://y.qq.com/"
                    if "gtimg.cn" in url or "y.qq.com" in url
                    else "https://music.163.com/"
                ),
                "Connection": "close",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                retry_after = 0.0
                if isinstance(exc, urllib.error.HTTPError):
                    try:
                        retry_after = float(exc.headers.get("Retry-After") or 0)
                    except (TypeError, ValueError):
                        retry_after = 0.0
                if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
                    with _REQUEST_LOCK:
                        _PROVIDER_BLOCKED_UNTIL[bucket] = time.monotonic() + max(
                            retry_after, 120.0
                        )
                    raise RuntimeError(
                        f"{bucket} rate limited; switching to another catalog"
                    ) from exc
                else:
                    time.sleep(0.8 * (attempt + 1))
    raise last_error or RuntimeError(f"request failed: {url}")


def request_json(url: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    if data is not None:
        encoded = urllib.parse.urlencode(data).encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(3):
            bucket = "netease-api" if "music.163.com" in url else "post-api"
            interval = 0.20
            with _REQUEST_LOCK:
                elapsed = time.monotonic() - _LAST_REQUEST.get(bucket, 0.0)
                if elapsed < interval:
                    time.sleep(interval - elapsed)
                _LAST_REQUEST[bucket] = time.monotonic()
            request = urllib.request.Request(
                url,
                data=encoded,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                    "Referer": "https://music.163.com/",
                    "Connection": "close",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=25) as response:
                    return json.loads(response.read().decode("utf-8"))
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.8 * (attempt + 1))
        raise last_error or RuntimeError("JSON request failed")
    return json.loads(request_bytes(url).decode("utf-8"))


def apple_artwork_url(url: str) -> str:
    if not url:
        return ""
    return re.sub(r"/\d+x\d+bb\.(?:jpg|png)$", "/1000x1000bb.jpg", url)


def album_candidate_score(artist: str, album: str, candidate: dict[str, Any]) -> tuple[float, float, float]:
    album_score = similarity(album, candidate.get("album") or "")
    artist_score = artist_similarity(artist, candidate.get("artist") or "")
    total = album_score * 0.72 + artist_score * 0.28
    if album_score >= 0.96 and normalized(candidate.get("artist") or "") in {
        "variousartists",
        "群星",
    }:
        total = max(total, 0.90)
    return total, album_score, artist_score


def search_apple_album(artist: str, album: str) -> dict[str, Any] | None:
    if not album:
        return None
    candidates = []
    for country in ("HK", "TW"):
        query = urllib.parse.urlencode(
            {
                "term": f"{artist} {album}".strip(),
                "entity": "album",
                "media": "music",
                "country": country,
                "limit": 50,
            }
        )
        try:
            payload = request_json(f"https://itunes.apple.com/search?{query}")
        except Exception:
            continue
        for item in payload.get("results", []) or []:
            candidate = {
                "provider": f"apple-{country.lower()}",
                "id": item.get("collectionId"),
                "artist": item.get("artistName") or "",
                "album": item.get("collectionName") or "",
                "release_date": (item.get("releaseDate") or "")[:10] or None,
                "artwork_url": apple_artwork_url(item.get("artworkUrl100") or ""),
                "catalog_url": item.get("collectionViewUrl"),
            }
            total, album_score, artist_score = album_candidate_score(artist, album, candidate)
            candidate.update(
                {
                    "score": total,
                    "album_similarity": album_score,
                    "artist_similarity": artist_score,
                }
            )
            candidates.append(candidate)
        if any(candidate["score"] >= 0.88 for candidate in candidates):
            break
    if not candidates:
        return None
    best = max(candidates, key=lambda candidate: candidate["score"])
    if best["album_similarity"] < 0.64 or best["score"] < 0.72:
        return None
    return best


def search_netease_album(artist: str, album: str) -> dict[str, Any] | None:
    if not album:
        return None
    try:
        payload = request_json(
            "https://music.163.com/api/search/get/web",
            {"s": f"{artist} {album}", "type": 10, "offset": 0, "total": "true", "limit": 30},
        )
    except Exception:
        return None
    candidates = []
    for item in payload.get("result", {}).get("albums", []) or []:
        candidate = {
            "provider": "netease",
            "id": item.get("id"),
            "artist": (item.get("artist") or {}).get("name") or "",
            "album": item.get("name") or "",
            "release_date": None,
            "artwork_url": (item.get("picUrl") or "") + "?param=1000y1000",
            "catalog_url": f"https://music.163.com/#/album?id={item.get('id')}",
        }
        timestamp = item.get("publishTime")
        if isinstance(timestamp, (int, float)) and timestamp > 0:
            candidate["release_date"] = datetime.fromtimestamp(
                timestamp / 1000, tz=timezone.utc
            ).date().isoformat()
        total, album_score, artist_score = album_candidate_score(artist, album, candidate)
        candidate.update(
            {
                "score": total,
                "album_similarity": album_score,
                "artist_similarity": artist_score,
            }
        )
        candidates.append(candidate)
    if not candidates:
        return None
    best = max(candidates, key=lambda candidate: candidate["score"])
    if best["album_similarity"] < 0.64 or best["score"] < 0.70:
        return None
    return best


def search_qq_album(artist: str, album: str) -> dict[str, Any] | None:
    if not album:
        return None
    query = urllib.parse.urlencode(
        {"p": 1, "n": 30, "w": f"{artist} {album}", "format": "json", "t": 8}
    )
    try:
        payload = request_json(
            f"https://c.y.qq.com/soso/fcgi-bin/client_search_cp?{query}"
        )
    except Exception:
        return None
    candidates = []
    for item in payload.get("data", {}).get("album", {}).get("list", []) or []:
        album_mid = item.get("albumMID")
        album_pic = item.get("albumPic") or ""
        if album_pic:
            album_pic = re.sub(r"^http://", "https://", album_pic)
            album_pic = re.sub(r"T002R\d+x\d+", "T002R800x800", album_pic)
        candidate = {
            "provider": "qqmusic",
            "id": album_mid or item.get("albumID"),
            "artist": item.get("singerName") or "",
            "album": item.get("albumName") or "",
            "release_date": item.get("publicTime") or None,
            "artwork_url": album_pic
            or (
                f"https://y.gtimg.cn/music/photo_new/T002R800x800M000{album_mid}.jpg"
                if album_mid
                else ""
            ),
            "catalog_url": f"https://y.qq.com/n/ryqq/albumDetail/{album_mid}" if album_mid else None,
        }
        total, album_score, artist_score = album_candidate_score(artist, album, candidate)
        candidate.update(
            {
                "score": total,
                "album_similarity": album_score,
                "artist_similarity": artist_score,
            }
        )
        candidates.append(candidate)
    if not candidates:
        return None
    best = max(candidates, key=lambda candidate: candidate["score"])
    if best["album_similarity"] < 0.64 or best["score"] < 0.70:
        return None
    return best


def artist_photo_candidate_score(artist: str, candidate: dict[str, Any]) -> float:
    # Aliases in public catalogs are user-editable and can contain unrelated artists.
    # Only the catalog's primary artist name is safe enough for an artwork fallback.
    return artist_similarity(artist, candidate.get("artist") or "")


def search_netease_artist_photos(artist: str) -> list[dict[str, Any]]:
    if not artist or len(split_artists(artist)) != 1:
        return []
    try:
        payload = request_json(
            "https://music.163.com/api/search/get/web",
            {"s": artist, "type": 100, "offset": 0, "total": "true", "limit": 20},
        )
    except Exception:
        return []
    candidates = []
    for item in payload.get("result", {}).get("artists", []) or []:
        for variant, raw_url in (
            ("cover", item.get("picUrl")),
            ("avatar", item.get("img1v1Url")),
        ):
            image_url = raw_url or ""
            if not image_url:
                continue
            image_url += ("&" if "?" in image_url else "?") + "param=1000y1000"
            candidate = {
                "provider": "netease-artist",
                "variant": variant,
                "id": item.get("id"),
                "artist": item.get("name") or "",
                "aliases": item.get("alias") or item.get("transNames") or [],
                "artwork_url": image_url,
                "catalog_url": f"https://music.163.com/#/artist?id={item.get('id')}",
            }
            candidate["artist_similarity"] = artist_photo_candidate_score(artist, candidate)
            candidates.append(candidate)
    return sorted(
        [
            candidate
            for candidate in candidates
            if candidate["artist_similarity"] >= 0.92
            and candidate.get("artwork_url")
        ],
        key=lambda candidate: candidate["artist_similarity"],
        reverse=True,
    )


def search_qq_artist_photos(artist: str) -> list[dict[str, Any]]:
    if not artist or len(split_artists(artist)) != 1:
        return []
    query = urllib.parse.urlencode(
        {"p": 1, "n": 20, "w": artist, "format": "json", "t": 9}
    )
    try:
        payload = request_json(
            f"https://c.y.qq.com/soso/fcgi-bin/client_search_cp?{query}"
        )
    except Exception:
        return []
    candidates = []
    for item in payload.get("data", {}).get("singer", {}).get("list", []) or []:
        singer_mid = item.get("singerMID") or item.get("singerMid") or item.get("singer_mid")
        image_url = item.get("singerPic") or item.get("singer_pic") or ""
        if image_url:
            image_url = re.sub(r"^http://", "https://", image_url)
            image_url = re.sub(r"T001R\d+x\d+", "T001R800x800", image_url)
        elif singer_mid:
            image_url = f"https://y.gtimg.cn/music/photo_new/T001R800x800M000{singer_mid}.jpg"
        candidate = {
            "provider": "qqmusic-artist",
            "id": singer_mid or item.get("singerID"),
            "artist": item.get("singerName") or item.get("singer_name") or "",
            "aliases": [],
            "artwork_url": image_url,
            "catalog_url": f"https://y.qq.com/n/ryqq/singer/{singer_mid}" if singer_mid else None,
        }
        candidate["artist_similarity"] = artist_photo_candidate_score(artist, candidate)
        candidates.append(candidate)
    return sorted(
        [
            candidate
            for candidate in candidates
            if candidate["artist_similarity"] >= 0.92
            and candidate.get("artwork_url")
        ],
        key=lambda candidate: candidate["artist_similarity"],
        reverse=True,
    )


def search_wikipedia_artist_photos(artist: str) -> list[dict[str, Any]]:
    if not artist or len(split_artists(artist)) != 1:
        return []
    query = urllib.parse.urlencode(
        {
            "action": "query",
            "format": "json",
            "formatversion": 2,
            "prop": "pageimages|info",
            "inprop": "url",
            "piprop": "thumbnail",
            "pithumbsize": 1200,
            "redirects": 1,
            "titles": artist,
        }
    )
    try:
        payload = request_json(f"https://zh.wikipedia.org/w/api.php?{query}")
    except Exception:
        return []
    candidates = []
    for item in payload.get("query", {}).get("pages", []) or []:
        thumbnail = item.get("thumbnail") or {}
        candidate = {
            "provider": "wikipedia-artist",
            "id": item.get("pageid"),
            "artist": item.get("title") or "",
            "aliases": [],
            "artwork_url": thumbnail.get("source") or "",
            "catalog_url": item.get("fullurl"),
        }
        candidate["artist_similarity"] = artist_photo_candidate_score(artist, candidate)
        if (
            candidate["artist_similarity"] >= 0.92
            and candidate.get("artwork_url")
        ):
            candidates.append(candidate)
    return sorted(
        candidates,
        key=lambda candidate: candidate["artist_similarity"],
        reverse=True,
    )


def artist_photo_cache_path(artist: str) -> Path:
    digest = hashlib.sha256(
        f"{ARTIST_PHOTO_CACHE_VERSION}|{normalized(artist)}".encode("utf-8")
    ).hexdigest()
    return CACHE_DIR / "歌手目录" / f"{digest}.json"


def load_artist_photo_cache(artist: str) -> list[dict[str, Any]]:
    path = artist_photo_cache_path(artist)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_candidates = payload.get("candidates")
        if not isinstance(raw_candidates, list):
            raw_candidates = [payload.get("candidate") or {}]
        candidates = []
        for candidate in raw_candidates:
            score = artist_photo_candidate_score(artist, candidate)
            if not candidate.get("artwork_url") or score < 0.92:
                continue
            candidate["artist_similarity"] = score
            candidate["from_cache"] = True
            candidates.append(candidate)
        return candidates
    except Exception:
        return []


def save_artist_photo_cache(artist: str, candidates: list[dict[str, Any]]) -> None:
    json_write(
        artist_photo_cache_path(artist),
        {"cached_at": now_iso(), "query_artist": artist, "candidates": candidates},
    )


def search_artist_photos(artist: str) -> list[dict[str, Any]]:
    cached = load_artist_photo_cache(artist)
    fresh = (
        search_netease_artist_photos(artist)
        + search_qq_artist_photos(artist)
        + search_wikipedia_artist_photos(artist)
    )
    by_url: dict[str, dict[str, Any]] = {}
    for candidate in cached + fresh:
        url = candidate.get("artwork_url") or ""
        if url and url not in by_url:
            by_url[url] = candidate
    candidates = sorted(
        by_url.values(),
        key=lambda candidate: candidate.get("artist_similarity", 0.0),
        reverse=True,
    )
    if candidates:
        save_artist_photo_cache(artist, candidates)
    return candidates


def lookup_seed_catalog(seed: dict[str, Any], target: dict[str, str]) -> dict[str, Any] | None:
    catalog = seed.get("catalog") or {}
    provider = catalog.get("provider") or ""
    identifier = catalog.get("id")
    if not identifier:
        return None
    try:
        if provider.startswith("itunes"):
            query = urllib.parse.urlencode({"id": identifier, "country": "HK"})
            payload = request_json(f"https://itunes.apple.com/lookup?{query}")
            item = next(iter(payload.get("results", []) or []), None)
            if not item:
                return None
            result = {
                "provider": provider,
                "id": identifier,
                "artist": item.get("artistName") or catalog.get("candidate_artist") or "",
                "album": item.get("collectionName") or catalog.get("candidate_album") or "",
                "release_date": (item.get("releaseDate") or "")[:10] or None,
                "artwork_url": apple_artwork_url(item.get("artworkUrl100") or ""),
                "catalog_url": item.get("trackViewUrl") or item.get("collectionViewUrl"),
            }
        elif provider == "netease":
            query = urllib.parse.urlencode({"ids": json.dumps([int(identifier)])})
            payload = request_json(f"https://music.163.com/api/song/detail/?{query}")
            item = next(iter(payload.get("songs", []) or []), None)
            if not item:
                return None
            album = item.get("album") or {}
            result = {
                "provider": provider,
                "id": identifier,
                "artist": " / ".join(
                    artist.get("name", "") for artist in item.get("artists", []) if artist.get("name")
                ),
                "album": album.get("name") or "",
                "release_date": None,
                "artwork_url": (album.get("picUrl") or "") + "?param=1000y1000",
                "catalog_url": f"https://music.163.com/#/song?id={identifier}",
            }
        elif provider == "qqmusic":
            query = urllib.parse.urlencode({"songmid": identifier, "format": "json"})
            payload = request_json(
                f"https://c.y.qq.com/v8/fcg-bin/fcg_play_single_song.fcg?{query}"
            )
            item = next(iter(payload.get("data", []) or []), None)
            if not item:
                return None
            album = item.get("album") or {}
            album_mid = album.get("mid")
            album_pmid = album.get("pmid") or album_mid
            result = {
                "provider": provider,
                "id": identifier,
                "artist": " / ".join(
                    artist.get("name", "") for artist in item.get("singer", []) if artist.get("name")
                ),
                "album": album.get("name") or "",
                "release_date": album.get("time_public") or None,
                "artwork_url": (
                    f"https://y.gtimg.cn/music/photo_new/T002R800x800M000{album_pmid}.jpg"
                    if album_pmid
                    else ""
                ),
                "catalog_url": f"https://y.qq.com/n/ryqq/songDetail/{identifier}",
            }
        else:
            return None
    except Exception:
        return None
    total, album_score, artist_score = album_candidate_score(
        target["ARTIST"], target["ALBUM"], result
    )
    result.update(
        {
            "score": total,
            "album_similarity": album_score,
            "artist_similarity": artist_score,
        }
    )
    if album_score < 0.60 or total < 0.68 or not result.get("artwork_url"):
        return None
    return result


def album_key(metadata: dict[str, str]) -> str:
    return normalized(metadata.get("ALBUMARTIST") or metadata.get("ARTIST")) + "|" + normalized(metadata.get("ALBUM"))


def catalog_cache_path(key: str) -> Path:
    digest = hashlib.sha256(
        f"{CATALOG_CACHE_VERSION}|{key}".encode("utf-8")
    ).hexdigest()
    return CACHE_DIR / "唱片目录" / f"{digest}.json"


def load_catalog_cache(key: str, metadata: dict[str, str]) -> dict[str, Any] | None:
    path = catalog_cache_path(key)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        candidate = payload.get("candidate") or {}
        total, album_score, artist_score = album_candidate_score(
            metadata["ARTIST"], metadata["ALBUM"], candidate
        )
        if (
            candidate.get("artwork_url")
            and album_score >= 0.64
            and total >= 0.70
        ):
            candidate.update(
                {
                    "score": total,
                    "album_similarity": album_score,
                    "artist_similarity": artist_score,
                    "from_cache": True,
                }
            )
            return candidate
    except Exception:
        return None
    return None


def save_catalog_cache(key: str, metadata: dict[str, str], candidate: dict[str, Any]) -> None:
    json_write(
        catalog_cache_path(key),
        {
            "cached_at": now_iso(),
            "query_artist": metadata["ARTIST"],
            "query_album": metadata["ALBUM"],
            "candidate": candidate,
        },
    )


def resolve_catalogs(
    plans: list[dict[str, Any]],
    reviewed: dict[str, dict[str, Any]],
    workers: int,
    progress: ProgressCallback | None = None,
) -> dict[str, dict[str, Any] | None]:
    by_key: dict[str, dict[str, str]] = {}
    seed_by_key: dict[str, dict[str, Any]] = {}
    special_by_key: dict[str, dict[str, Any]] = {}
    label_by_key: dict[str, str] = {}
    for plan in plans:
        key = album_key(plan["target_metadata"])
        if key and not key.endswith("|"):
            by_key.setdefault(key, plan["target_metadata"])
            label_by_key.setdefault(key, plan["filename"])
            special = SPECIAL_CATALOGS_BY_FILENAME.get(plan["filename"])
            if special:
                special_by_key.setdefault(key, dict(special))
            seed = reviewed.get(plan["filename"])
            if seed and seed.get("catalog_match") in {"excellent", "good"}:
                seed_by_key.setdefault(key, seed)

    results: dict[str, dict[str, Any] | None] = {}

    def safe_album_only_candidate(
        metadata: dict[str, str], candidate: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if not candidate or candidate.get("album_similarity", 0.0) < 0.96:
            return None
        expected_year = metadata.get("YEAR") or ""
        release_date = candidate.get("release_date") or ""
        if not expected_year or not release_date.startswith(expected_year):
            return None
        return candidate

    def resolve_one(key: str, metadata: dict[str, str]) -> tuple[str, dict[str, Any] | None]:
        candidate = special_by_key.get(key)
        if candidate is None:
            candidate = load_catalog_cache(key, metadata)
        if candidate is None and key in seed_by_key:
            candidate = lookup_seed_catalog(seed_by_key[key], metadata)
        if candidate is None:
            candidate = search_apple_album(metadata["ARTIST"], metadata["ALBUM"])
        if candidate is None:
            candidate = search_qq_album(metadata["ARTIST"], metadata["ALBUM"])
        if candidate is None:
            candidate = safe_album_only_candidate(
                metadata, search_qq_album("", metadata["ALBUM"])
            )
        if candidate is None:
            candidate = search_netease_album(metadata["ARTIST"], metadata["ALBUM"])
        if candidate is not None:
            save_catalog_cache(key, metadata, candidate)
        return key, candidate

    print(f"正在核对 {len(by_key)} 个不同唱片集……", flush=True)
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 6))) as executor:
        futures = {
            executor.submit(resolve_one, key, metadata): key
            for key, metadata in by_key.items()
        }
        completed = 0
        for future in as_completed(futures):
            key = futures[future]
            try:
                resolved_key, candidate = future.result()
                results[resolved_key] = candidate
            except Exception:
                results[key] = None
            completed += 1
            if progress:
                progress(
                    "catalog",
                    completed,
                    len(futures),
                    label_by_key.get(key, key),
                )
            if completed % 25 == 0 or completed == len(futures):
                print(f"  唱片集核对 {completed}/{len(futures)}", flush=True)

    for plan in plans:
        key = album_key(plan["target_metadata"])
        if results.get(key) is not None:
            continue
        seed = reviewed.get(plan["filename"])
        if seed:
            fallback = lookup_seed_catalog(seed, plan["target_metadata"])
            if fallback:
                results[key] = fallback
    return results


def normalize_cover_image(data: bytes) -> tuple[bytes, dict[str, Any]]:
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        image = ImageOps.exif_transpose(image)
        original_size = image.size
        if image.mode not in {"RGB", "L"}:
            background = Image.new("RGB", image.size, "white")
            if "A" in image.getbands():
                background.paste(image, mask=image.getchannel("A"))
            else:
                background.paste(image)
            image = background
        else:
            image = image.convert("RGB")
        if max(image.size) > 1200:
            image.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=91, optimize=True, progressive=True)
        rendered = output.getvalue()
        if min(image.size) < 300:
            raise ValueError(f"cover is too small: {image.size}")
        return rendered, {
            "original_width": original_size[0],
            "original_height": original_size[1],
            "width": image.size[0],
            "height": image.size[1],
            "mime": "image/jpeg",
            "bytes": len(rendered),
            "sha256": hashlib.sha256(rendered).hexdigest(),
        }


def cache_cover(url: str) -> tuple[Path, dict[str, Any]]:
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    path = CACHE_DIR / f"{key}.jpg"
    metadata_path = CACHE_DIR / f"{key}.json"
    if path.exists() and metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            data = path.read_bytes()
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
            if metadata.get("sha256") == hashlib.sha256(data).hexdigest():
                return path, metadata
        except Exception:
            pass
    raw = request_bytes(url)
    rendered, metadata = normalize_cover_image(raw)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".jpg.tmp")
    temporary.write_bytes(rendered)
    os.replace(temporary, path)
    metadata.update({"source_url": url, "cached_at": now_iso()})
    json_write(metadata_path, metadata)
    return path, metadata


def download_catalog_covers(
    catalogs: dict[str, dict[str, Any] | None],
    workers: int,
    progress: ProgressCallback | None = None,
    url_labels: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    urls = {
        candidate["artwork_url"]
        for candidate in catalogs.values()
        if candidate and candidate.get("artwork_url")
    }
    resolved: dict[str, dict[str, Any]] = {}
    print(f"正在下载并校验 {len(urls)} 张不同封面……", flush=True)
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 6))) as executor:
        futures = {executor.submit(cache_cover, url): url for url in urls}
        completed = 0
        for future in as_completed(futures):
            url = futures[future]
            try:
                path, metadata = future.result()
                resolved[url] = {"path": path, "image": metadata}
            except Exception as exc:
                resolved[url] = {"error": f"{type(exc).__name__}: {exc}"}
            completed += 1
            if progress:
                progress(
                    "cover",
                    completed,
                    len(futures),
                    (url_labels or {}).get(url, "唱片集封面"),
                )
            if completed % 25 == 0 or completed == len(futures):
                print(f"  封面下载 {completed}/{len(futures)}", flush=True)
    return resolved


def resolve_artist_photo_fallbacks(
    plans: list[dict[str, Any]],
    workers: int,
    progress: ProgressCallback | None = None,
) -> dict[str, dict[str, Any]]:
    artists = {
        plan["target_metadata"]["ARTIST"]
        for plan in plans
        if not plan["cover_ready"]
        and plan["type"] != "ASF"
        and plan["target_metadata"].get("ARTIST")
        and len(split_artists(plan["target_metadata"]["ARTIST"])) == 1
    }
    resolved: dict[str, dict[str, Any]] = {}
    print(f"正式封面仍缺失，正在核对 {len(artists)} 位歌手的照片候选……", flush=True)

    def visual_signatures(path: Path) -> list[bytes]:
        signatures = []
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image).convert("L")
            width, height = image.size
            for ratio in (1.0, 0.9, 0.8, 0.7, 0.6):
                side = max(1, int(min(width, height) * ratio))
                left = (width - side) // 2
                top = (height - side) // 2
                square = image.crop((left, top, left + side, top + side))
                square = square.resize((48, 48), Image.Resampling.LANCZOS)
                signatures.append(square.tobytes())
        return signatures

    def visual_distance(left: list[bytes], right: list[bytes]) -> float:
        return min(
            sum(abs(a - b) for a, b in zip(left_variant, right_variant))
            / len(left_variant)
            for left_variant in left
            for right_variant in right
        )

    def resolve_one(artist: str) -> tuple[str, dict[str, Any]]:
        candidates = search_artist_photos(artist)
        if not candidates:
            return artist, {"error": "no reliable artist photo found"}
        choices = []
        seen_hashes = set()
        seen_signatures: list[list[bytes]] = []
        errors = []
        for candidate in candidates[:8]:
            try:
                path, image = cache_cover(candidate["artwork_url"])
                image_hash = image.get("sha256")
                if image_hash and image_hash in seen_hashes:
                    continue
                if image_hash:
                    seen_hashes.add(image_hash)
                signatures = visual_signatures(path)
                if any(
                    visual_distance(signatures, existing) < 36.0
                    for existing in seen_signatures
                ):
                    continue
                seen_signatures.append(signatures)
                choices.append({"candidate": candidate, "path": path, "image": image})
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
        if not choices:
            return artist, {"error": "；".join(errors) or "no usable artist photo"}
        return artist, {"choices": choices, "errors": errors}

    with ThreadPoolExecutor(max_workers=max(1, min(workers, 6))) as executor:
        futures = {executor.submit(resolve_one, artist): artist for artist in artists}
        for completed, future in enumerate(as_completed(futures), 1):
            artist = futures[future]
            try:
                resolved_artist, result = future.result()
                resolved[resolved_artist] = result
            except Exception as exc:
                resolved[artist] = {"error": f"{type(exc).__name__}: {exc}"}
            if progress:
                progress("artist_photo", completed, len(futures), artist)
            if completed % 25 == 0 or completed == len(futures):
                print(f"  歌手照片核对 {completed}/{len(futures)}", flush=True)
    return resolved


def delete_casefold(tags: Any, *names: str) -> None:
    wanted = {name.casefold() for name in names}
    for key in list(tags.keys()):
        if str(key).casefold() in wanted:
            del tags[key]


def write_flac(path: Path, metadata: dict[str, str], cover: bytes | None) -> None:
    audio = FLAC(path)
    for key in ("title", "artist", "album", "albumartist", "year", "date"):
        delete_casefold(audio.tags, key)
    audio.tags["title"] = [metadata["TITLE"]]
    audio.tags["artist"] = [metadata["ARTIST"]]
    audio.tags["album"] = [metadata["ALBUM"]]
    audio.tags["albumartist"] = [metadata["ALBUMARTIST"]]
    audio.tags["year"] = [metadata["YEAR"]]
    audio.tags["date"] = [metadata["DATE"]]
    if cover is not None:
        audio.clear_pictures()
        picture = Picture()
        picture.type = 3
        picture.mime = "image/jpeg"
        picture.desc = "Cover (front)"
        picture.data = cover
        with Image.open(io.BytesIO(cover)) as image:
            picture.width, picture.height = image.size
            picture.depth = 24
        audio.add_picture(picture)
    audio.save()


def write_id3(path: Path, metadata: dict[str, str], cover: bytes | None) -> None:
    tags = id3_for(path, create=True)
    assert tags is not None
    for frame in ("TIT2", "TPE1", "TALB", "TPE2", "TDRC", "TDOR", "TYER", "TORY"):
        tags.delall(frame)
    tags.add(TIT2(encoding=3, text=[metadata["TITLE"]]))
    tags.add(TPE1(encoding=3, text=[metadata["ARTIST"]]))
    tags.add(TALB(encoding=3, text=[metadata["ALBUM"]]))
    tags.add(TPE2(encoding=3, text=[metadata["ALBUMARTIST"]]))
    tags.add(TDRC(encoding=3, text=[metadata["YEAR"]]))
    tags.add(TDOR(encoding=3, text=[metadata["DATE"]]))
    if cover is not None:
        tags.delall("APIC")
        tags.add(
            APIC(
                encoding=3,
                mime="image/jpeg",
                type=3,
                desc="Cover (front)",
                data=cover,
            )
        )
    tags.save(path, v2_version=4)


def write_mp4(path: Path, metadata: dict[str, str], cover: bytes | None) -> None:
    audio = MP4(path)
    if audio.tags is None:
        audio.add_tags()
    for key in ("\xa9nam", "\xa9ART", "\xa9alb", "aART", "\xa9day"):
        audio.tags.pop(key, None)
    audio.tags["\xa9nam"] = [metadata["TITLE"]]
    audio.tags["\xa9ART"] = [metadata["ARTIST"]]
    audio.tags["\xa9alb"] = [metadata["ALBUM"]]
    audio.tags["aART"] = [metadata["ALBUMARTIST"]]
    audio.tags["\xa9day"] = [metadata["DATE"]]
    if cover is not None:
        audio.tags["covr"] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]
    audio.save()


def write_temp_file(temp: Path, kind: str, metadata: dict[str, str], cover: bytes | None) -> None:
    if kind == "FLAC":
        write_flac(temp, metadata, cover)
    elif kind in {"MP3", "AAC"}:
        write_id3(temp, metadata, cover)
    elif kind == "MP4":
        write_mp4(temp, metadata, cover)
    else:
        raise ValueError(f"cover writing is not supported for {kind}")


def verify_temp_file(temp: Path, expected_type: str, metadata: dict[str, str], expect_cover: bool) -> dict[str, Any]:
    audio = open_audio(temp)
    if audio is None or type(audio).__name__ != expected_type:
        raise ValueError(
            f"type changed: expected {expected_type}, got {type(audio).__name__ if audio else None}"
        )
    actual = read_metadata(temp, audio)
    if actual != metadata:
        raise ValueError(f"metadata mismatch: {actual!r} != {metadata!r}")
    cover, mime, count = read_cover(temp, audio)
    if expect_cover and not cover:
        raise ValueError("embedded cover could not be read back")
    return {
        "type": type(audio).__name__,
        "duration_ms": round(audio.info.length * 1000)
        if getattr(audio.info, "length", None)
        else None,
        "metadata": actual,
        "cover_count": count,
        "cover_mime": mime,
        "cover_bytes": len(cover) if cover else 0,
        "cover_sha256": hashlib.sha256(cover).hexdigest() if cover else None,
    }


def metadata_valid(metadata: dict[str, str]) -> bool:
    return all(metadata.get(field) for field in CORE_FIELDS) and bool(
        DATE_RE.match(metadata["DATE"])
    ) and metadata["YEAR"] == metadata["DATE"][:4]


def build_plans(tracks: list[dict[str, Any]], reviewed: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    plans = []
    for track in tracks:
        target, metadata_source = initial_target_metadata(track, reviewed)
        plans.append(
            {
                **track,
                "target_metadata": target,
                "metadata_source": metadata_source,
                "metadata_changes": {
                    field: {"before": track["metadata"][field], "after": target[field]}
                    for field in CORE_FIELDS
                    if track["metadata"][field] != target[field]
                },
            }
        )
    return plans


def finalize_plans(
    plans: list[dict[str, Any]],
    catalogs: dict[str, dict[str, Any] | None],
    downloads: dict[str, dict[str, Any]],
) -> None:
    local_covers: dict[str, tuple[bytes, str]] = {}
    for plan in plans:
        if plan["cover_data"]:
            local_covers.setdefault(
                album_key(plan["target_metadata"]),
                (plan["cover_data"], plan["cover_mime"] or "image/jpeg"),
            )

    for plan in plans:
        key = album_key(plan["target_metadata"])
        catalog = catalogs.get(key)
        plan["catalog"] = catalog
        target = plan["target_metadata"]
        if catalog and not target["DATE"] and DATE_RE.match(catalog.get("release_date") or ""):
            target["DATE"] = catalog["release_date"]
            target["YEAR"] = target["DATE"][:4]
        if target["DATE"] and not target["YEAR"]:
            target["YEAR"] = target["DATE"][:4]
        if target["YEAR"] and not target["DATE"]:
            target["DATE"] = target["YEAR"]
        if target["ARTIST"]:
            target["ALBUMARTIST"] = target["ARTIST"]

        plan["metadata_changes"] = {
            field: {"before": plan["metadata"][field], "after": target[field]}
            for field in CORE_FIELDS
            if plan["metadata"][field] != target[field]
        }
        plan["metadata_ready"] = metadata_valid(target)

        plan["cover_source"] = None
        plan["cover_kind"] = None
        plan["cover_path"] = None
        plan["cover_image"] = None
        plan["artist_photo_candidate"] = None
        plan["artist_photo_choices"] = []
        plan["artist_photo_index"] = 0
        plan["artist_photo_pending"] = False
        plan["artist_photo_approved"] = False
        plan["cover_ready"] = bool(plan["cover_data"])
        if plan["cover_data"]:
            plan["cover_source"] = "existing-embedded"
            plan["cover_kind"] = "album-cover"
        elif key in local_covers:
            data, _ = local_covers[key]
            normalized_data, image_metadata = normalize_cover_image(data)
            local_path = CACHE_DIR / f"local-{hashlib.sha256(normalized_data).hexdigest()}.jpg"
            local_path.parent.mkdir(parents=True, exist_ok=True)
            if not local_path.exists():
                local_path.write_bytes(normalized_data)
            plan["cover_path"] = local_path
            plan["cover_image"] = image_metadata
            plan["cover_ready"] = True
            plan["cover_source"] = "same-album-local"
            plan["cover_kind"] = "album-cover"
        elif catalog and catalog.get("artwork_url"):
            downloaded = downloads.get(catalog["artwork_url"], {})
            if downloaded.get("path"):
                plan["cover_path"] = downloaded["path"]
                plan["cover_image"] = downloaded["image"]
                plan["cover_ready"] = True
                plan["cover_source"] = catalog["provider"]
                plan["cover_kind"] = "album-cover"

        plan["action"] = "skip"
        plan["skip_reason"] = None
        if not plan["metadata_ready"]:
            plan["skip_reason"] = "core metadata is still incomplete or invalid"
        elif plan["type"] == "ASF":
            plan["skip_reason"] = "ASF/WMA cover writing is intentionally unsupported"
        elif plan["cover_data"] and not plan["metadata_changes"]:
            plan["skip_reason"] = "already complete with an embedded cover"
        elif not plan["cover_ready"] and not plan["metadata_changes"]:
            plan["skip_reason"] = "no verified album cover was found"
        else:
            plan["action"] = "write"


def apply_artist_photo_fallbacks(
    plans: list[dict[str, Any]], photos: dict[str, dict[str, Any]]
) -> None:
    for plan in plans:
        if plan["cover_ready"] or plan["type"] == "ASF":
            continue
        artist = plan["target_metadata"].get("ARTIST") or ""
        result = photos.get(artist) or {}
        choices = result.get("choices") or []
        if not choices:
            continue
        plan["artist_photo_choices"] = choices
        plan["artist_photo_index"] = 0
        selected = choices[0]
        candidate = selected["candidate"]
        plan["artist_photo_candidate"] = candidate
        plan["cover_path"] = selected["path"]
        plan["cover_image"] = selected["image"]
        plan["cover_source"] = candidate.get("provider") if candidate else "artist-photo"
        plan["cover_kind"] = "artist-photo"
        plan["cover_ready"] = True
        plan["artist_photo_pending"] = True
        if plan["metadata_ready"]:
            plan["action"] = "write"
            plan["skip_reason"] = None


def safe_result_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": str(plan["path"]),
        "filename": plan["filename"],
        "type": plan["type"],
        "duration_ms": plan["duration_ms"],
        "metadata_source": plan["metadata_source"],
        "metadata_before": plan["metadata"],
        "metadata_after": plan["target_metadata"],
        "metadata_changes": plan["metadata_changes"],
        "had_cover": bool(plan["cover_data"]),
        "existing_cover_bytes": len(plan["cover_data"]) if plan["cover_data"] else 0,
        "cover_source": plan["cover_source"],
        "cover_kind": plan.get("cover_kind"),
        "cover_image": plan["cover_image"],
        "artist_photo_candidate": plan.get("artist_photo_candidate"),
        "artist_photo_choice_count": len(plan.get("artist_photo_choices") or []),
        "artist_photo_index": int(plan.get("artist_photo_index") or 0),
        "artist_photo_pending": bool(plan.get("artist_photo_pending")),
        "artist_photo_approved": bool(plan.get("artist_photo_approved")),
        "catalog": plan["catalog"],
        "action": plan["action"],
        "skip_reason": plan["skip_reason"],
    }


def apply_plans(
    root: Path,
    plans: list[dict[str, Any]],
    run_dir: Path,
    progress: ProgressCallback | None = None,
) -> list[dict[str, Any]]:
    results = []
    rollback = {
        "created_at": now_iso(),
        "music_root": str(root),
        "tracks": [],
    }
    rollback_path = run_dir / "回滚记录.json"
    json_write(rollback_path, rollback)

    writable = [plan for plan in plans if plan["action"] == "write"]
    for index, plan in enumerate(writable, 1):
        path = plan["path"]
        if progress:
            progress("write", index - 1, len(writable), plan["filename"])
        current_stat = path.stat()
        if current_stat.st_size != plan["size"] or current_stat.st_mtime_ns != plan["mtime_ns"]:
            results.append(
                {
                    "filename": plan["filename"],
                    "success": False,
                    "error": "file changed after scanning",
                }
            )
            if progress:
                progress("write", index, len(writable), plan["filename"])
            continue

        rollback["tracks"].append(
            {
                "path": str(path),
                "filename": plan["filename"],
                "type": plan["type"],
                "original_metadata": plan["metadata"],
                "original_had_cover": bool(plan["cover_data"]),
                "original_cover_mime": plan["cover_mime"],
                "original_cover_base64": base64.b64encode(plan["cover_data"]).decode("ascii") if plan["cover_data"] else None,
            }
        )
        json_write(rollback_path, rollback)

        temp = path.with_name(f".{path.name}.codex-cover-tmp{path.suffix}")
        if temp.exists():
            temp.unlink()
        try:
            shutil.copy2(path, temp)
            use_candidate_cover = not (
                plan.get("cover_kind") == "artist-photo"
                and not plan.get("artist_photo_approved")
            )
            cover_bytes = (
                plan["cover_path"].read_bytes()
                if plan["cover_path"] is not None and use_candidate_cover
                else None
            )
            if plan["cover_data"] and cover_bytes is None:
                cover_bytes = None
            write_temp_file(
                temp,
                plan["type"],
                plan["target_metadata"],
                cover_bytes,
            )
            verified = verify_temp_file(
                temp,
                plan["type"],
                plan["target_metadata"],
                expect_cover=bool(
                    plan["cover_data"]
                    or (plan["cover_path"] and use_candidate_cover)
                ),
            )
            os.replace(temp, path)
            results.append(
                {
                    "filename": plan["filename"],
                    "path": str(path),
                    "success": True,
                    "verified": verified,
                }
            )
        except Exception as exc:
            if temp.exists():
                temp.unlink()
            results.append(
                {
                    "filename": plan["filename"],
                    "path": str(path),
                    "success": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        if index % 20 == 0 or index == len(writable):
            print(f"  文件写入 {index}/{len(writable)}", flush=True)
        if progress:
            progress("write", index, len(writable), plan["filename"])
    return results


def verification_payload(tracks: list[dict[str, Any]], errors: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "total": len(tracks),
        "errors": errors,
        "with_cover": sum(track["cover_count"] > 0 for track in tracks),
        "without_cover": [track["filename"] for track in tracks if track["cover_count"] == 0],
        "complete_metadata": sum(metadata_valid(track["metadata"]) for track in tracks),
        "incomplete_metadata": [
            {"filename": track["filename"], "metadata": track["metadata"]}
            for track in tracks
            if not metadata_valid(track["metadata"])
        ],
        "album_artist_mismatches": [
            track["filename"]
            for track in tracks
            if track["metadata"]["ALBUMARTIST"] != track["metadata"]["ARTIST"]
        ],
    }


def independent_verify(root: Path) -> dict[str, Any]:
    tracks, errors = scan_music(root)
    return verification_payload(tracks, errors)


def independent_verify_paths(
    paths: list[Path], progress: ProgressCallback | None = None
) -> dict[str, Any]:
    tracks, errors = scan_paths(paths, progress=progress, phase="verify")
    return verification_payload(tracks, errors)


def common_parent(paths: list[Path]) -> Path:
    if not paths:
        return DEFAULT_MUSIC_DIR
    try:
        return Path(os.path.commonpath([str(path.resolve().parent) for path in paths]))
    except ValueError:
        return paths[0].resolve().parent


def prepare_selected_files(
    paths: list[Path],
    workers: int = 4,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    paths = [path.resolve() for path in paths if path.is_file()]
    if not paths:
        raise ValueError("没有可处理的音乐文件")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    RECORD_DIR.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = RECORD_DIR / run_id
    suffix = 1
    while run_dir.exists():
        run_dir = RECORD_DIR / f"{run_id}-{suffix}"
        suffix += 1
    run_dir.mkdir(parents=True)

    print(f"扫描 {len(paths)} 个已选择文件……", flush=True)
    tracks, scan_errors = scan_paths(paths, progress=progress, phase="scan")
    if not tracks:
        raise ValueError("没有识别到受支持的音乐文件")
    reviewed = load_reviewed_seed()
    plans = build_plans(tracks, reviewed)
    print(
        f"识别到 {len(tracks)} 首音乐，已有封面 {sum(track['cover_count'] > 0 for track in tracks)} 首。",
        flush=True,
    )
    catalogs = resolve_catalogs(plans, reviewed, workers, progress=progress)
    url_labels = {
        catalog["artwork_url"]: plan["filename"]
        for plan in plans
        if (catalog := catalogs.get(album_key(plan["target_metadata"])))
        and catalog.get("artwork_url")
    }
    downloads = download_catalog_covers(
        catalogs,
        workers,
        progress=progress,
        url_labels=url_labels,
    )
    finalize_plans(plans, catalogs, downloads)
    artist_photos = resolve_artist_photo_fallbacks(
        plans, workers, progress=progress
    )
    apply_artist_photo_fallbacks(plans, artist_photos)
    preview = {
        "created_at": now_iso(),
        "mode": "preview",
        "selected_files": len(paths),
        "recognized_audio": len(plans),
        "scan_errors": scan_errors,
        "will_write": sum(plan["action"] == "write" for plan in plans),
        "will_skip": sum(plan["action"] != "write" for plan in plans),
        "cover_ready": sum(plan["cover_ready"] for plan in plans),
        "artist_photo_pending": sum(plan["artist_photo_pending"] for plan in plans),
        "metadata_ready": sum(plan["metadata_ready"] for plan in plans),
        "plans": [safe_result_plan(plan) for plan in plans],
    }
    json_write(run_dir / "执行预览.json", preview)
    print(
        f"预览：可安全处理 {preview['will_write']} 首；"
        f"封面已匹配 {preview['cover_ready']}/{preview['recognized_audio']}；"
        f"信息完整 {preview['metadata_ready']}/{preview['recognized_audio']}。",
        flush=True,
    )
    return {
        "paths": paths,
        "plans": plans,
        "run_dir": run_dir,
        "common_root": common_parent(paths),
        "preview": preview,
    }


def execute_prepared_files(
    prepared: dict[str, Any],
    selected_paths: set[str] | None = None,
    progress: ProgressCallback | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    plans = prepared["plans"]
    if selected_paths:
        plans = [plan for plan in plans if str(plan["path"]) in selected_paths]
    if not plans:
        raise ValueError("没有选择要处理的曲目")
    results = apply_plans(
        prepared["common_root"], plans, prepared["run_dir"], progress=progress
    )
    result_payload = {
        "completed_at": now_iso(),
        "selected": len(plans),
        "attempted": len(results),
        "succeeded": sum(result.get("success") for result in results),
        "failed": sum(not result.get("success") for result in results),
        "results": results,
    }
    json_write(prepared["run_dir"] / "写入结果.json", result_payload)
    print("正在从目标文件独立复查……", flush=True)
    verification = independent_verify_paths(
        [plan["path"] for plan in plans], progress=progress
    )
    json_write(prepared["run_dir"] / "独立复查.json", verification)
    return result_payload, verification


def gui_main() -> None:
    import contextlib
    import queue
    import threading
    import traceback
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from PIL import ImageTk

    class QueueWriter:
        def __init__(self, events: queue.Queue):
            self.events = events
            self.buffer = ""

        def write(self, value: str) -> int:
            self.buffer += value
            while "\n" in self.buffer:
                line, self.buffer = self.buffer.split("\n", 1)
                if line.strip():
                    self.events.put(("log", line))
            return len(value)

        def flush(self) -> None:
            if self.buffer.strip():
                self.events.put(("log", self.buffer))
                self.buffer = ""

    class MusicOrganizerApp:
        AUDIO_SUFFIXES = {".flac", ".mp3", ".aac", ".m4a", ".mp4", ".wma"}

        def __init__(self, window: tk.Tk):
            self.window = window
            self.window.title("音乐信息与专辑封面一键整理")
            self.window.geometry("1380x820")
            self.window.minsize(1080, 680)
            self.events: queue.Queue = queue.Queue()
            self.paths: dict[str, Path] = {}
            self.prepared: dict[str, Any] | None = None
            self.plan_by_path: dict[str, dict[str, Any]] = {}
            self.running = False
            self.cover_photo = None
            self.status_var = tk.StringVar(value="请选择音乐文件，或选择整个文件夹。")
            self.summary_var = tk.StringVar(value="尚未预览")
            self._build_ui()
            self.window.after(100, self._poll_events)
            self.window.protocol("WM_DELETE_WINDOW", self._on_close)

        def _build_ui(self) -> None:
            style = ttk.Style(self.window)
            try:
                style.theme_use("vista")
            except tk.TclError:
                pass
            style.configure("Treeview", rowheight=27)
            style.configure("Title.TLabel", font=("Microsoft YaHei UI", 16, "bold"))
            style.configure("Sub.TLabel", foreground="#555555")

            top = ttk.Frame(self.window, padding=(16, 14, 16, 8))
            top.pack(fill="x")
            ttk.Label(top, text="音乐信息与专辑封面一键整理", style="Title.TLabel").pack(anchor="w")
            ttk.Label(
                top,
                text="可多选单个音乐，也可导入整个文件夹。先预览匹配，再安全写入；已有高清封面默认保留。",
                style="Sub.TLabel",
            ).pack(anchor="w", pady=(3, 0))

            toolbar = ttk.Frame(self.window, padding=(16, 4, 16, 8))
            toolbar.pack(fill="x")
            self.add_files_button = ttk.Button(toolbar, text="选择音乐（可多选）", command=self._add_files)
            self.add_files_button.pack(side="left")
            self.add_folder_button = ttk.Button(toolbar, text="选择文件夹", command=self._add_folder)
            self.add_folder_button.pack(side="left", padx=(8, 0))
            self.remove_button = ttk.Button(toolbar, text="移除所选", command=self._remove_selected)
            self.remove_button.pack(side="left", padx=(8, 0))
            self.clear_button = ttk.Button(toolbar, text="清空列表", command=self._clear)
            self.clear_button.pack(side="left", padx=(8, 0))
            self.preview_button = ttk.Button(toolbar, text="① 联网核对并预览", command=self._preview)
            self.preview_button.pack(side="right")

            content = ttk.Panedwindow(self.window, orient="horizontal")
            content.pack(fill="both", expand=True, padx=16)
            table_frame = ttk.Frame(content)
            detail_frame = ttk.Frame(content, padding=(12, 8))
            content.add(table_frame, weight=5)
            content.add(detail_frame, weight=2)

            columns = ("type", "title", "artist", "album", "metadata", "cover", "action")
            self.tree = ttk.Treeview(table_frame, columns=columns, show="tree headings", selectmode="extended")
            self.tree.heading("#0", text="文件")
            self.tree.heading("type", text="格式")
            self.tree.heading("title", text="标题")
            self.tree.heading("artist", text="艺术家")
            self.tree.heading("album", text="唱片集")
            self.tree.heading("metadata", text="信息")
            self.tree.heading("cover", text="封面")
            self.tree.heading("action", text="预览结果")
            self.tree.column("#0", width=255, minwidth=160)
            self.tree.column("type", width=58, anchor="center")
            self.tree.column("title", width=180)
            self.tree.column("artist", width=120)
            self.tree.column("album", width=220)
            self.tree.column("metadata", width=72, anchor="center")
            self.tree.column("cover", width=72, anchor="center")
            self.tree.column("action", width=116, anchor="center")
            yscroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
            xscroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
            self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
            self.tree.grid(row=0, column=0, sticky="nsew")
            yscroll.grid(row=0, column=1, sticky="ns")
            xscroll.grid(row=1, column=0, sticky="ew")
            table_frame.rowconfigure(0, weight=1)
            table_frame.columnconfigure(0, weight=1)
            self.tree.bind("<<TreeviewSelect>>", self._show_detail)

            ttk.Label(detail_frame, text="所选曲目", font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
            self.cover_label = ttk.Label(detail_frame, text="暂无封面\n（预览后显示）", anchor="center")
            self.cover_label.pack(fill="x", pady=(12, 10))
            self.detail_text = tk.Text(
                detail_frame,
                height=18,
                wrap="word",
                relief="flat",
                background="#f5f5f5",
                font=("Microsoft YaHei UI", 9),
                padx=10,
                pady=8,
            )
            self.detail_text.pack(fill="both", expand=True)
            self.detail_text.configure(state="disabled")

            lower = ttk.Frame(self.window, padding=(16, 10, 16, 14))
            lower.pack(fill="x")
            summary = ttk.Frame(lower)
            summary.pack(fill="x")
            ttk.Label(summary, textvariable=self.summary_var, font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
            ttk.Label(summary, textvariable=self.status_var, style="Sub.TLabel").pack(side="right")
            self.progress = ttk.Progressbar(lower, mode="indeterminate")
            self.progress.pack(fill="x", pady=(7, 6))

            action_row = ttk.Frame(lower)
            action_row.pack(fill="x")
            self.log_button = ttk.Button(action_row, text="查看日志", command=self._toggle_log)
            self.log_button.pack(side="left")
            self.data_button = ttk.Button(action_row, text="打开记录文件夹", command=self._open_data_folder)
            self.data_button.pack(side="left", padx=(8, 0))
            self.apply_button = ttk.Button(
                action_row,
                text="② 处理所选（未选则处理全部）",
                command=self._apply,
                state="disabled",
            )
            self.apply_button.pack(side="right")

            self.log_frame = ttk.Frame(self.window, padding=(16, 0, 16, 12))
            self.log_text = tk.Text(
                self.log_frame,
                height=8,
                wrap="word",
                background="#181818",
                foreground="#e8e8e8",
                insertbackground="white",
                font=("Consolas", 9),
            )
            self.log_text.pack(fill="both", expand=True)

        def _iid(self, path: Path) -> str:
            return hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()

        def _append_log(self, text: str) -> None:
            self.log_text.insert("end", text.rstrip() + "\n")
            self.log_text.see("end")

        def _set_busy(self, busy: bool, status: str | None = None) -> None:
            self.running = busy
            state = "disabled" if busy else "normal"
            for button in (
                self.add_files_button,
                self.add_folder_button,
                self.remove_button,
                self.clear_button,
                self.preview_button,
            ):
                button.configure(state=state)
            self.apply_button.configure(
                state="disabled" if busy or self.prepared is None else "normal"
            )
            if busy:
                self.progress.start(12)
            else:
                self.progress.stop()
            if status:
                self.status_var.set(status)

        def _invalidate_preview(self) -> None:
            self.prepared = None
            self.plan_by_path.clear()
            self.apply_button.configure(state="disabled")
            self.summary_var.set(f"已选择 {len(self.paths)} 个文件，尚未预览")

        def _add_paths(self, paths: list[Path]) -> None:
            added = 0
            for path in paths:
                path = path.resolve()
                if not path.is_file() or path.suffix.casefold() not in self.AUDIO_SUFFIXES:
                    continue
                key = str(path)
                if key in self.paths:
                    continue
                self.paths[key] = path
                iid = self._iid(path)
                self.tree.insert(
                    "",
                    "end",
                    iid=iid,
                    text=path.name,
                    values=(path.suffix.lstrip(".").upper(), "", "", "", "待扫描", "待扫描", "待预览"),
                )
                added += 1
            if added:
                self._invalidate_preview()
                self.status_var.set(f"新增 {added} 个文件。")

        def _add_files(self) -> None:
            selected = filedialog.askopenfilenames(
                title="选择一个或多个音乐文件",
                initialdir=str(DEFAULT_MUSIC_DIR if DEFAULT_MUSIC_DIR.exists() else Path.home()),
                filetypes=[
                    ("支持的音乐", "*.flac *.mp3 *.aac *.m4a *.mp4 *.wma"),
                    ("所有文件", "*.*"),
                ],
            )
            self._add_paths([Path(path) for path in selected])

        def _add_folder(self) -> None:
            selected = filedialog.askdirectory(
                title="选择音乐文件夹",
                initialdir=str(DEFAULT_MUSIC_DIR if DEFAULT_MUSIC_DIR.exists() else Path.home()),
            )
            if not selected:
                return
            folder = Path(selected)
            paths = [
                path
                for path in folder.rglob("*")
                if path.is_file() and path.suffix.casefold() in self.AUDIO_SUFFIXES
            ]
            self._add_paths(paths)

        def _remove_selected(self) -> None:
            for iid in self.tree.selection():
                path = next((key for key, value in self.paths.items() if self._iid(value) == iid), None)
                if path:
                    self.paths.pop(path, None)
                self.tree.delete(iid)
            self._invalidate_preview()

        def _clear(self) -> None:
            for iid in self.tree.get_children():
                self.tree.delete(iid)
            self.paths.clear()
            self._invalidate_preview()
            self.status_var.set("列表已清空。")

        def _preview(self) -> None:
            if not self.paths:
                messagebox.showinfo("请选择音乐", "请先选择一个或多个音乐文件，或选择整个文件夹。")
                return
            self._set_busy(True, "正在联网核对唱片集和封面……")
            self.summary_var.set("预览进行中")
            paths = list(self.paths.values())

            def worker() -> None:
                writer = QueueWriter(self.events)
                try:
                    with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                        prepared = prepare_selected_files(paths, workers=4)
                    writer.flush()
                    self.events.put(("preview_done", prepared))
                except Exception:
                    writer.flush()
                    self.events.put(("error", traceback.format_exc()))

            threading.Thread(target=worker, daemon=True).start()

        def _apply(self) -> None:
            if self.prepared is None:
                return
            selected_iids = set(self.tree.selection())
            selected_paths = {
                str(path)
                for path in self.paths.values()
                if self._iid(path) in selected_iids
            }
            candidate_plans = self.prepared["plans"]
            if selected_paths:
                candidate_plans = [
                    plan for plan in candidate_plans if str(plan["path"]) in selected_paths
                ]
            writable = sum(plan["action"] == "write" for plan in candidate_plans)
            if writable == 0:
                messagebox.showinfo("无需处理", "所选曲目没有可安全写入的项目。")
                return
            if not messagebox.askyesno(
                "确认写入",
                f"将处理 {writable} 首音乐。\n\n"
                "每首都会先复制为同目录临时文件，写入并复查成功后才替换原文件；"
                "原标签和原封面会记录在回滚文件中。\n\n是否继续？",
            ):
                return
            self._set_busy(True, "正在安全写入并逐首复查……")

            def worker() -> None:
                writer = QueueWriter(self.events)
                try:
                    with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                        result, verification = execute_prepared_files(
                            self.prepared,
                            selected_paths if selected_paths else None,
                        )
                    writer.flush()
                    self.events.put(("apply_done", (result, verification)))
                except Exception:
                    writer.flush()
                    self.events.put(("error", traceback.format_exc()))

            threading.Thread(target=worker, daemon=True).start()

        def _refresh_preview_rows(self) -> None:
            assert self.prepared is not None
            self.plan_by_path = {str(plan["path"]): plan for plan in self.prepared["plans"]}
            for path_text, path in self.paths.items():
                iid = self._iid(path)
                plan = self.plan_by_path.get(path_text)
                if not plan or not self.tree.exists(iid):
                    continue
                metadata = plan["target_metadata"]
                info = "完整" if plan["metadata_ready"] else "待确认"
                cover = "已有" if plan["cover_data"] else ("已匹配" if plan["cover_ready"] else "未找到")
                action = "可安全处理" if plan["action"] == "write" else (plan["skip_reason"] or "跳过")
                self.tree.item(
                    iid,
                    values=(
                        plan["type"],
                        metadata["TITLE"],
                        metadata["ARTIST"],
                        metadata["ALBUM"],
                        info,
                        cover,
                        action,
                    ),
                )

        def _show_detail(self, _event: Any = None) -> None:
            selected = self.tree.selection()
            if not selected:
                return
            iid = selected[0]
            path = next((path for path in self.paths.values() if self._iid(path) == iid), None)
            if path is None:
                return
            plan = self.plan_by_path.get(str(path))
            if plan is None:
                detail = f"文件：{path.name}\n路径：{path}\n\n请先执行联网核对并预览。"
                self.cover_label.configure(image="", text="暂无封面\n（预览后显示）")
                self.cover_photo = None
            else:
                metadata = plan["target_metadata"]
                catalog = plan.get("catalog") or {}
                detail = (
                    f"文件：{path.name}\n"
                    f"格式：{plan['type']}\n"
                    f"标题：{metadata['TITLE']}\n"
                    f"艺术家：{metadata['ARTIST']}\n"
                    f"唱片集：{metadata['ALBUM']}\n"
                    f"唱片集艺术家：{metadata['ALBUMARTIST']}\n"
                    f"年份：{metadata['YEAR']}\n"
                    f"日期：{metadata['DATE']}\n\n"
                    f"封面来源：{plan.get('cover_source') or '未找到'}\n"
                    f"目录匹配：{catalog.get('provider') or '—'}\n"
                    f"处理结果：{'可安全处理' if plan['action'] == 'write' else plan.get('skip_reason') or '跳过'}"
                )
                image_data = plan.get("cover_data")
                if image_data is None and plan.get("cover_path"):
                    try:
                        image_data = plan["cover_path"].read_bytes()
                    except OSError:
                        image_data = None
                if image_data:
                    try:
                        with Image.open(io.BytesIO(image_data)) as image:
                            image = image.convert("RGB")
                            image.thumbnail((235, 235), Image.Resampling.LANCZOS)
                            self.cover_photo = ImageTk.PhotoImage(image.copy())
                        self.cover_label.configure(image=self.cover_photo, text="")
                    except Exception:
                        self.cover_photo = None
                        self.cover_label.configure(image="", text="封面预览失败")
                else:
                    self.cover_photo = None
                    self.cover_label.configure(image="", text="未找到可靠封面")
            self.detail_text.configure(state="normal")
            self.detail_text.delete("1.0", "end")
            self.detail_text.insert("1.0", detail)
            self.detail_text.configure(state="disabled")

        def _poll_events(self) -> None:
            try:
                while True:
                    event, payload = self.events.get_nowait()
                    if event == "log":
                        self._append_log(str(payload))
                    elif event == "preview_done":
                        self.prepared = payload
                        self._refresh_preview_rows()
                        preview = payload["preview"]
                        self.summary_var.set(
                            f"可安全处理 {preview['will_write']} 首｜"
                            f"封面 {preview['cover_ready']}/{preview['recognized_audio']}｜"
                            f"信息完整 {preview['metadata_ready']}/{preview['recognized_audio']}"
                        )
                        self._set_busy(False, "预览完成。可在列表中多选曲目后处理；不选择则处理全部可安全项。")
                        self.apply_button.configure(state="normal")
                    elif event == "apply_done":
                        result, verification = payload
                        self._set_busy(False, "写入和独立复查完成。")
                        self.summary_var.set(
                            f"成功 {result['succeeded']}/{result['attempted']}｜"
                            f"所处理曲目带封面 {verification['with_cover']}/{verification['total']}｜"
                            f"信息完整 {verification['complete_metadata']}/{verification['total']}"
                        )
                        messagebox.showinfo(
                            "处理完成",
                            f"成功：{result['succeeded']}\n失败：{result['failed']}\n"
                            f"复查带封面：{verification['with_cover']}/{verification['total']}\n\n"
                            f"记录位置：\n{self.prepared['run_dir']}",
                        )
                        self.prepared = None
                        self.apply_button.configure(state="disabled")
                    elif event == "error":
                        self._append_log(str(payload))
                        self._set_busy(False, "发生错误，音乐文件不会因未完成的临时写入而被替换。")
                        messagebox.showerror("处理失败", "处理过程中发生错误。请点击“查看日志”检查详情。")
            except queue.Empty:
                pass
            self.window.after(100, self._poll_events)

        def _toggle_log(self) -> None:
            if self.log_frame.winfo_ismapped():
                self.log_frame.pack_forget()
                self.log_button.configure(text="查看日志")
            else:
                self.log_frame.pack(fill="both")
                self.log_button.configure(text="隐藏日志")

        def _open_data_folder(self) -> None:
            APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
            try:
                os.startfile(APP_DATA_DIR)  # type: ignore[attr-defined]
            except Exception as exc:
                messagebox.showerror("无法打开", str(exc))

        def _on_close(self) -> None:
            if self.running:
                if not messagebox.askyesno("任务进行中", "任务仍在进行。强制关闭可能留下临时文件，确定退出吗？"):
                    return
            self.window.destroy()

    root = tk.Tk()
    try:
        root.iconname("音乐整理")
    except Exception:
        pass
    app = MusicOrganizerApp(root)
    ui_test_report = os.environ.get("MUSIC_ORGANIZER_UI_TEST_REPORT")
    if ui_test_report:
        def finish_ui_test() -> None:
            root.update_idletasks()
            buttons = []
            pending = list(root.winfo_children())
            while pending:
                widget = pending.pop()
                pending.extend(widget.winfo_children())
                if widget.winfo_class() in {"Button", "TButton"}:
                    try:
                        buttons.append(str(widget.cget("text")))
                    except tk.TclError:
                        pass
            json_write(
                Path(ui_test_report),
                {
                    "title": root.title(),
                    "geometry": root.geometry(),
                    "tree_selectmode": str(app.tree.cget("selectmode")),
                    "tree_columns": list(app.tree.cget("columns")),
                    "buttons": sorted(buttons),
                },
            )
            root.destroy()

        root.after(250, finish_ui_test)
    root.mainloop()


def gui_main_v2() -> None:
    import contextlib
    import queue
    import tkinter as tk
    import traceback
    from tkinter import filedialog, messagebox, ttk
    from PIL import ImageTk

    phase_names = {
        "scan": "读取当前信息",
        "catalog": "联网核对唱片集",
        "cover": "下载并校验封面",
        "artist_photo": "核对歌手照片",
        "write": "安全写入",
        "verify": "写后独立复查",
    }

    class QueueWriter:
        def __init__(self, events: queue.Queue):
            self.events = events
            self.buffer = ""

        def write(self, value: str) -> int:
            self.buffer += value
            while "\n" in self.buffer:
                line, self.buffer = self.buffer.split("\n", 1)
                if line.strip():
                    self.events.put(("log", line))
            return len(value)

        def flush(self) -> None:
            if self.buffer.strip():
                self.events.put(("log", self.buffer))
                self.buffer = ""

    class MusicOrganizerAppV2:
        AUDIO_SUFFIXES = {".flac", ".mp3", ".aac", ".m4a", ".mp4", ".wma"}

        def __init__(self, window: tk.Tk):
            self.window = window
            self.events: queue.Queue = queue.Queue()
            self.paths: dict[str, Path] = {}
            self.checked: dict[str, bool] = {}
            self.local_tracks: dict[str, dict[str, Any]] = {}
            self.prepared: dict[str, Any] | None = None
            self.plan_by_path: dict[str, dict[str, Any]] = {}
            self.running = False
            self.cover_photo = None
            self.help_window = None
            self.settings = self._load_settings()
            self.folder_var = tk.StringVar()
            self.recursive_var = tk.BooleanVar(
                value=bool(self.settings.get("recursive", False))
            )
            self.auto_online_var = tk.BooleanVar(
                value=bool(self.settings.get("auto_online_preview", False))
            )
            self.summary_var = tk.StringVar(value="尚未扫描")
            self.stage_var = tk.StringVar(value="准备就绪")
            self.current_var = tk.StringVar(value="")
            self.count_var = tk.StringVar(value="0/0")
            self.log_visible = bool(self.settings.get("log_visible", False))
            self.window.title("音乐信息与专辑封面一键整理 2.9")
            geometry = str(self.settings.get("geometry") or "1460x880")
            try:
                self.window.geometry(geometry)
            except tk.TclError:
                self.window.geometry("1460x880")
            self.window.minsize(1120, 700)
            self._build_checkbox_images()
            self._build_ui()
            self.window.protocol("WM_DELETE_WINDOW", self._on_close)
            self.window.after(100, self._poll_events)
            self.window.after(250, self._startup_scan)

        def _load_settings(self) -> dict[str, Any]:
            try:
                if SETTINGS_PATH.exists():
                    payload = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
                    return payload if isinstance(payload, dict) else {}
            except Exception:
                pass
            return {}

        def _save_settings(self) -> None:
            try:
                APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
                payload = {
                    "version": 3,
                    "last_folder": self.folder_var.get().strip(),
                    "recursive": bool(self.recursive_var.get()),
                    "auto_online_preview": bool(self.auto_online_var.get()),
                    "geometry": self.window.geometry(),
                    "log_visible": self.log_visible,
                    "checked_paths": [
                        path for path, value in self.checked.items() if value
                    ],
                }
                json_write(SETTINGS_PATH, payload)
            except Exception as exc:
                self._append_log(f"保存设置失败：{exc}")

        def _build_checkbox_images(self) -> None:
            self.checkbox_off = tk.PhotoImage(width=18, height=18)
            self.checkbox_on = tk.PhotoImage(width=18, height=18)
            for image in (self.checkbox_off, self.checkbox_on):
                image.put("#ffffff", to=(0, 0, 18, 18))
                image.put("#6b7280", to=(3, 3, 15, 15))
                image.put("#ffffff", to=(5, 5, 13, 13))
            self.checkbox_on.put("#1976d2", to=(3, 3, 15, 15))
            self.checkbox_on.put("#ffffff", to=(5, 9, 7, 11))
            self.checkbox_on.put("#ffffff", to=(7, 11, 9, 13))
            self.checkbox_on.put("#ffffff", to=(9, 8, 11, 12))
            self.checkbox_on.put("#ffffff", to=(11, 6, 13, 10))

        def _build_ui(self) -> None:
            style = ttk.Style(self.window)
            try:
                style.theme_use("vista")
            except tk.TclError:
                pass
            style.configure("Treeview", rowheight=29)
            style.configure("TButton", padding=(8, 4))
            style.configure("Title.TLabel", font=("Microsoft YaHei UI", 17, "bold"))
            style.configure("Sub.TLabel", foreground="#555555")
            style.configure("Stage.TLabel", font=("Microsoft YaHei UI", 10, "bold"))
            style.configure(
                "Primary.TButton",
                font=("Microsoft YaHei UI", 11, "bold"),
                padding=(14, 8),
            )
            self.window.columnconfigure(0, weight=1)
            self.window.rowconfigure(4, weight=1, minsize=140)

            top = ttk.Frame(self.window, padding=(16, 13, 16, 6))
            top.grid(row=0, column=0, sticky="ew")
            ttk.Label(top, text="音乐信息与专辑封面一键整理", style="Title.TLabel").pack(anchor="w")
            ttk.Label(
                top,
                text="启动即读取当前文件夹；逐曲勾选、先看现有信息、再联网核对，最后安全写入。",
                style="Sub.TLabel",
            ).pack(anchor="w", pady=(2, 0))

            folder_row = ttk.Frame(self.window, padding=(16, 4, 16, 5))
            folder_row.grid(row=1, column=0, sticky="ew")
            ttk.Label(folder_row, text="当前文件夹：").pack(side="left")
            self.folder_entry = ttk.Entry(
                folder_row, textvariable=self.folder_var, state="readonly"
            )
            self.folder_entry.pack(side="left", fill="x", expand=True, padx=(5, 8))
            self.folder_button = ttk.Button(
                folder_row, text="选择文件夹…", command=self._choose_folder
            )
            self.folder_button.pack(side="left")
            self.rescan_button = ttk.Button(
                folder_row, text="重新扫描", command=self._rescan_current
            )
            self.rescan_button.pack(side="left", padx=(8, 0))
            self.add_files_button = ttk.Button(
                folder_row, text="追加音乐…", command=self._add_files
            )
            self.add_files_button.pack(side="left", padx=(8, 0))

            options = ttk.Frame(self.window, padding=(16, 1, 16, 7))
            options.grid(row=2, column=0, sticky="ew")
            self.recursive_check = ttk.Checkbutton(
                options,
                text="包含子文件夹",
                variable=self.recursive_var,
                command=self._option_changed,
            )
            self.recursive_check.pack(side="left")
            self.auto_online_check = ttk.Checkbutton(
                options,
                text="本地扫描后自动联网预览",
                variable=self.auto_online_var,
                command=self._save_settings,
            )
            self.auto_online_check.pack(side="left", padx=(18, 0))
            ttk.Label(
                options,
                text="已有封面始终保留；找不到可靠正式封面就留空。",
                style="Sub.TLabel",
            ).pack(side="left", padx=(22, 0))

            self.toolbar = ttk.Frame(self.window, padding=(16, 0, 16, 7), height=55)
            self.toolbar.grid(row=3, column=0, sticky="ew")
            self.toolbar.pack_propagate(False)
            self.all_button = ttk.Button(self.toolbar, text="全选", command=self._check_all)
            self.all_button.pack(side="left")
            self.none_button = ttk.Button(self.toolbar, text="全不选", command=self._check_none)
            self.none_button.pack(side="left", padx=(6, 0))
            self.invert_button = ttk.Button(self.toolbar, text="反选", command=self._check_invert)
            self.invert_button.pack(side="left", padx=(6, 0))
            self.missing_metadata_button = ttk.Button(
                self.toolbar, text="勾选信息不完整", command=self._check_missing_metadata
            )
            self.missing_metadata_button.pack(side="left", padx=(18, 0))
            self.missing_cover_button = ttk.Button(
                self.toolbar, text="勾选缺失封面", command=self._check_missing_cover
            )
            self.missing_cover_button.pack(side="left", padx=(6, 0))
            self.remove_button = ttk.Button(
                self.toolbar, text="从列表移除", command=self._remove_highlighted
            )
            self.remove_button.pack(side="left", padx=(18, 0))
            self.preview_button = ttk.Button(
                self.toolbar,
                text="① 联网核对勾选项",
                command=self._preview,
                style="Primary.TButton",
            )
            self.preview_button.place(rely=0.5, anchor="e")

            content = ttk.Panedwindow(self.window, orient="horizontal")
            content.grid(row=4, column=0, sticky="nsew", padx=16)
            self.table_frame = ttk.Frame(content)
            detail_frame = ttk.Frame(content, padding=(12, 7))
            content.add(self.table_frame, weight=7)
            content.add(detail_frame, weight=2)

            columns = (
                "file", "type", "title", "artist", "album", "year", "date",
                "metadata", "cover", "result"
            )
            self.tree = ttk.Treeview(
                self.table_frame,
                columns=columns,
                show="tree headings",
                selectmode="extended",
            )
            self.tree.heading("#0", text="选择")
            headings = {
                "file": "文件",
                "type": "格式",
                "title": "当前标题",
                "artist": "当前艺术家",
                "album": "当前唱片集",
                "year": "年份",
                "date": "日期",
                "metadata": "信息完整",
                "cover": "当前封面",
                "result": "联网预览结果",
            }
            for column, title in headings.items():
                self.tree.heading(column, text=title)
            self.tree.column("#0", width=48, minwidth=48, stretch=False, anchor="center")
            self.tree.column("file", width=190, minwidth=140)
            self.tree.column("type", width=50, stretch=False, anchor="center")
            self.tree.column("title", width=130)
            self.tree.column("artist", width=100)
            self.tree.column("album", width=180)
            self.tree.column("year", width=50, stretch=False, anchor="center")
            self.tree.column("date", width=78, stretch=False, anchor="center")
            self.tree.column("metadata", width=70, stretch=False, anchor="center")
            self.tree.column("cover", width=66, stretch=False, anchor="center")
            self.tree.column("result", width=120)
            yscroll = ttk.Scrollbar(self.table_frame, orient="vertical", command=self.tree.yview)
            xscroll = ttk.Scrollbar(self.table_frame, orient="horizontal", command=self.tree.xview)
            self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
            self.tree.grid(row=0, column=0, sticky="nsew")
            yscroll.grid(row=0, column=1, sticky="ns")
            xscroll.grid(row=1, column=0, sticky="ew")
            self.table_frame.rowconfigure(0, weight=1)
            self.table_frame.columnconfigure(0, weight=1)
            self.tree.bind("<Button-1>", self._tree_click, add="+")
            self.tree.bind("<space>", self._toggle_highlighted)
            self.tree.bind("<<TreeviewSelect>>", self._show_detail)

            ttk.Label(
                detail_frame,
                text="曲目信息",
                font=("Microsoft YaHei UI", 11, "bold"),
            ).pack(anchor="w")
            detail_content = ttk.Frame(detail_frame)
            detail_content.pack(fill="both", expand=True, pady=(10, 0))
            self.detail_text = tk.Text(
                detail_content,
                width=32,
                height=20,
                wrap="word",
                relief="flat",
                background="#f5f5f5",
                font=("Microsoft YaHei UI", 9),
                padx=10,
                pady=8,
            )
            detail_scrollbar = ttk.Scrollbar(
                detail_content, orient="vertical", command=self.detail_text.yview
            )
            self.detail_text.configure(yscrollcommand=detail_scrollbar.set)
            self.detail_text.grid(row=0, column=0, sticky="nsew")
            detail_scrollbar.grid(row=0, column=1, sticky="ns")
            detail_content.rowconfigure(0, weight=1)
            detail_content.columnconfigure(0, weight=1)
            self.detail_text.insert("1.0", "请选择一首曲目查看封面和信息。")
            self.detail_text.configure(state="disabled")

            lower = ttk.Frame(self.window, padding=(16, 8, 16, 12))
            lower.grid(row=5, column=0, sticky="ew")
            self.summary_row = ttk.Frame(lower, height=49)
            self.summary_row.pack(fill="x")
            self.summary_row.pack_propagate(False)
            ttk.Label(self.summary_row, textvariable=self.summary_var, style="Stage.TLabel").pack(side="left")
            ttk.Label(self.summary_row, textvariable=self.count_var).pack(side="right")
            self.apply_button = ttk.Button(
                self.summary_row,
                text="② 写入勾选项",
                command=self._apply,
                state="disabled",
                style="Primary.TButton",
            )
            self.apply_button.place(rely=0.5, anchor="e")
            self.progress_text = ttk.Frame(lower, height=49)
            self.progress_text.pack(fill="x", pady=(5, 2))
            self.progress_text.pack_propagate(False)
            ttk.Label(self.progress_text, textvariable=self.stage_var, style="Stage.TLabel").pack(side="left")
            ttk.Label(self.progress_text, textvariable=self.current_var, style="Sub.TLabel").pack(side="left", padx=(12, 0))
            self.progress = ttk.Progressbar(lower, mode="determinate", maximum=1, value=0)
            self.progress.pack(fill="x", pady=(2, 6))
            self.actions = ttk.Frame(lower, height=49)
            self.actions.pack(fill="x")
            self.actions.pack_propagate(False)
            self.log_button = ttk.Button(self.actions, text="查看日志", command=self._toggle_log)
            self.log_button.pack(side="left")
            self.data_button = ttk.Button(self.actions, text="打开记录文件夹", command=self._open_data_folder)
            self.data_button.pack(side="left", padx=(7, 0))
            self.cleanup_button = ttk.Button(
                self.actions, text="清理日志与缓存", command=self._clean_logs_and_cache
            )
            self.cleanup_button.pack(side="left", padx=(7, 0))
            self.help_button = ttk.Button(
                self.actions, text="使用说明", command=self._show_help
            )
            self.help_button.pack(side="left", padx=(7, 0))
            self.log_frame = ttk.Frame(self.window, padding=(16, 0, 16, 10))
            self.log_text = tk.Text(
                self.log_frame,
                height=7,
                wrap="word",
                background="#181818",
                foreground="#e8e8e8",
                insertbackground="white",
                font=("Consolas", 9),
            )
            self.log_text.pack(fill="both", expand=True)
            self.log_frame.grid(row=6, column=0, sticky="nsew")
            if self.log_visible:
                self.log_button.configure(text="隐藏日志")
            else:
                self.log_frame.grid_remove()
            self.table_frame.bind("<Configure>", self._schedule_primary_button_sync)
            self.toolbar.bind("<Configure>", self._schedule_primary_button_sync)
            self.summary_row.bind("<Configure>", self._schedule_primary_button_sync)
            self.window.after_idle(self._sync_primary_buttons)

        def _schedule_primary_button_sync(self, _event: Any = None) -> None:
            self.window.after_idle(self._sync_primary_buttons)

        def _sync_primary_buttons(self) -> None:
            if not all(
                widget.winfo_exists()
                for widget in (
                    self.table_frame,
                    self.toolbar,
                    self.summary_row,
                    self.preview_button,
                    self.apply_button,
                )
            ):
                return
            table_right = self.tree.winfo_rootx() + self.tree.winfo_width()
            for container, button in (
                (self.toolbar, self.preview_button),
                (self.summary_row, self.apply_button),
            ):
                right = table_right - container.winfo_rootx()
                if container is self.toolbar:
                    right -= 16
                right = max(button.winfo_reqwidth(), min(right, container.winfo_width()))
                button.place_configure(x=right, rely=0.5, anchor="e")

        def _iid(self, path: Path) -> str:
            return hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()

        def _progress_callback(self, phase: str, done: int, total: int, name: str) -> None:
            self.events.put(("progress", (phase, done, total, name)))

        def _append_log(self, text: str) -> None:
            self.log_text.insert("end", text.rstrip() + "\n")
            self.log_text.see("end")

        def _startup_scan(self) -> None:
            override = os.environ.get("MUSIC_ORGANIZER_TEST_FOLDER")
            saved = str(self.settings.get("last_folder") or "").strip()
            candidates = [Path(override)] if override else []
            if saved:
                candidates.append(Path(saved))
            candidates.extend([SCRIPT_DIR, DEFAULT_MUSIC_DIR])
            folder = next((item for item in candidates if item.is_dir()), SCRIPT_DIR)
            self._load_folder(folder, restore_checks=True)

        def _folder_paths(self, folder: Path) -> list[Path]:
            iterator = folder.rglob("*") if self.recursive_var.get() else folder.iterdir()
            return sorted(
                [
                    path
                    for path in iterator
                    if path.is_file() and path.suffix.casefold() in self.AUDIO_SUFFIXES
                ],
                key=lambda item: str(item).casefold(),
            )

        def _load_folder(self, folder: Path, restore_checks: bool = False) -> None:
            if self.running:
                return
            folder = folder.resolve()
            self.folder_var.set(str(folder))
            remembered = (
                set(self.settings.get("checked_paths") or [])
                if restore_checks
                and int(self.settings.get("version") or 0) >= 3
                and "checked_paths" in self.settings
                else None
            )
            paths = self._folder_paths(folder)
            self._replace_paths(paths, remembered)
            self._save_settings()
            self._start_local_scan()

        def _replace_paths(self, paths: list[Path], remembered: set[str] | None = None) -> None:
            self.tree.delete(*self.tree.get_children())
            self.paths.clear()
            self.checked.clear()
            self.local_tracks.clear()
            self._invalidate_preview()
            remembered = remembered or set()
            for path in paths:
                text = str(path.resolve())
                checked = text in remembered
                self.paths[text] = path.resolve()
                self.checked[text] = checked
                self.tree.insert(
                    "",
                    "end",
                    iid=self._iid(path),
                    text="",
                    image=self.checkbox_on if checked else self.checkbox_off,
                    values=(path.name, path.suffix.lstrip(".").upper(), "", "", "", "", "", "读取中", "读取中", "尚未联网"),
                )
            self._update_summary("等待读取当前信息")

        def _add_files(self) -> None:
            initial = self.folder_var.get() or str(SCRIPT_DIR)
            selected = filedialog.askopenfilenames(
                title="追加一个或多个音乐文件",
                initialdir=initial,
                filetypes=[("支持的音乐", "*.flac *.mp3 *.aac *.m4a *.mp4 *.wma"), ("所有文件", "*.*")],
            )
            if not selected:
                return
            for raw in selected:
                path = Path(raw).resolve()
                text = str(path)
                if path.suffix.casefold() not in self.AUDIO_SUFFIXES or text in self.paths:
                    continue
                self.paths[text] = path
                self.checked[text] = False
                self.tree.insert(
                    "", "end", iid=self._iid(path), image=self.checkbox_off,
                    values=(path.name, path.suffix.lstrip(".").upper(), "", "", "", "", "", "读取中", "读取中", "尚未联网"),
                )
            self._invalidate_preview()
            self._start_local_scan()

        def _choose_folder(self) -> None:
            selected = filedialog.askdirectory(
                title="选择音乐文件夹",
                initialdir=self.folder_var.get() or str(SCRIPT_DIR),
            )
            if selected:
                self._load_folder(Path(selected), restore_checks=False)

        def _rescan_current(self) -> None:
            folder = Path(self.folder_var.get())
            if folder.is_dir():
                current_checked = {key for key, value in self.checked.items() if value}
                paths = self._folder_paths(folder)
                self._replace_paths(paths, current_checked)
                self._start_local_scan()

        def _option_changed(self) -> None:
            self._save_settings()
            self._rescan_current()

        def _start_local_scan(self) -> None:
            paths = list(self.paths.values())
            if not paths:
                self.stage_var.set("文件夹内没有支持的音乐")
                self.current_var.set("")
                self._set_busy(False)
                return
            self._set_busy(True)
            self.stage_var.set("读取当前信息")
            self.current_var.set("")

            def worker() -> None:
                try:
                    tracks, errors = scan_paths(
                        paths, progress=self._progress_callback, phase="scan"
                    )
                    self.events.put(("local_scan_done", (tracks, errors)))
                except Exception:
                    self.events.put(("error", traceback.format_exc()))

            threading.Thread(target=worker, daemon=True).start()

        def _set_busy(self, busy: bool) -> None:
            self.running = busy
            state = "disabled" if busy else "normal"
            for widget in (
                self.folder_button, self.rescan_button, self.add_files_button,
                self.recursive_check, self.auto_online_check, self.all_button,
                self.none_button, self.invert_button, self.missing_metadata_button,
                self.missing_cover_button, self.remove_button,
                self.preview_button, self.cleanup_button,
            ):
                widget.configure(state=state)
            writable = bool(
                self.prepared
                and any(plan["action"] == "write" for plan in self.prepared["plans"])
            )
            self.apply_button.configure(
                state="normal" if not busy and writable else "disabled"
            )

        def _update_summary(self, suffix: str = "") -> None:
            total = len(self.paths)
            checked = sum(self.checked.values())
            metadata_missing = sum(
                not metadata_valid(track["metadata"])
                for track in self.local_tracks.values()
            )
            cover_missing = sum(
                not track["cover_count"] for track in self.local_tracks.values()
            )
            fully_complete = sum(
                metadata_valid(track["metadata"]) and bool(track["cover_count"])
                for track in self.local_tracks.values()
            )
            base = f"共 {total} 首｜已勾选 {checked} 首"
            if self.local_tracks:
                base += (
                    f"｜其中信息封面完整 {fully_complete} 首"
                    f"｜信息缺失 {metadata_missing} 首"
                    f"｜封面缺失 {cover_missing} 首"
                )
            self.summary_var.set(base + (f"｜{suffix}" if suffix else ""))

        def _invalidate_preview(self) -> None:
            self.prepared = None
            self.plan_by_path.clear()
            if hasattr(self, "apply_button"):
                self.apply_button.configure(state="disabled")
            for text, path in self.paths.items():
                iid = self._iid(path)
                if self.tree.exists(iid):
                    values = list(self.tree.item(iid, "values"))
                    if values:
                        values[-1] = "尚未联网"
                        self.tree.item(iid, values=values)

        def _set_checked(self, path_text: str, value: bool, invalidate: bool = True) -> None:
            if path_text not in self.paths:
                return
            self.checked[path_text] = value
            iid = self._iid(self.paths[path_text])
            if self.tree.exists(iid):
                self.tree.item(iid, image=self.checkbox_on if value else self.checkbox_off)
            if invalidate:
                self._invalidate_preview()

        def _tree_click(self, event: Any) -> None:
            if self.running:
                return
            if self.tree.identify_region(event.x, event.y) != "tree":
                return
            iid = self.tree.identify_row(event.y)
            if not iid:
                return
            path_text = next(
                (text for text, path in self.paths.items() if self._iid(path) == iid), None
            )
            if path_text:
                self._set_checked(path_text, not self.checked.get(path_text, False))
                self._update_summary()

        def _toggle_highlighted(self, _event: Any = None) -> str:
            for iid in self.tree.selection():
                path_text = next(
                    (text for text, path in self.paths.items() if self._iid(path) == iid), None
                )
                if path_text:
                    self._set_checked(path_text, not self.checked.get(path_text, False), invalidate=False)
            self._invalidate_preview()
            self._update_summary()
            return "break"

        def _check_all(self) -> None:
            for text in self.paths:
                self._set_checked(text, True, invalidate=False)
            self._invalidate_preview()
            self._update_summary()

        def _check_none(self) -> None:
            for text in self.paths:
                self._set_checked(text, False, invalidate=False)
            self._invalidate_preview()
            self._update_summary()

        def _check_invert(self) -> None:
            for text in self.paths:
                self._set_checked(text, not self.checked[text], invalidate=False)
            self._invalidate_preview()
            self._update_summary()

        def _apply_smart_selection(self, matches: set[str], label: str) -> None:
            current = {text for text, value in self.checked.items() if value}
            excluded = current - matches
            if excluded:
                answer = messagebox.askyesnocancel(
                    "是否保留原勾选",
                    f"“{label}”找到 {len(matches)} 首，原来勾选的曲目中有 "
                    f"{len(excluded)} 首不在结果内。\n\n"
                    "选择“是”：保留原勾选，并加入筛选结果。\n"
                    "选择“否”：只勾选筛选结果。\n"
                    "选择“取消”：保持当前勾选不变。",
                )
                if answer is None:
                    return
                if answer:
                    matches |= current
            for text in self.paths:
                self._set_checked(text, text in matches, invalidate=False)
            self._invalidate_preview()
            self._update_summary(f"{label} {len(matches)} 首")

        def _check_missing_metadata(self) -> None:
            matches = {
                text
                for text, track in self.local_tracks.items()
                if not metadata_valid(track["metadata"])
            }
            self._apply_smart_selection(matches, "信息不完整")

        def _check_missing_cover(self) -> None:
            matches = {
                text
                for text, track in self.local_tracks.items()
                if not track["cover_count"]
            }
            self._apply_smart_selection(matches, "缺失封面")

        def _remove_highlighted(self) -> None:
            for iid in self.tree.selection():
                path_text = next(
                    (text for text, path in self.paths.items() if self._iid(path) == iid), None
                )
                if path_text:
                    self.paths.pop(path_text, None)
                    self.checked.pop(path_text, None)
                    self.local_tracks.pop(path_text, None)
                self.tree.delete(iid)
            self._invalidate_preview()
            self._update_summary()

        def _preview(self) -> None:
            paths = [path for text, path in self.paths.items() if self.checked.get(text)]
            if not paths:
                messagebox.showinfo("没有勾选", "请先勾选至少一首音乐。")
                return
            self._set_busy(True)
            self.stage_var.set("准备联网核对")
            self.current_var.set("")

            def worker() -> None:
                writer = QueueWriter(self.events)
                try:
                    with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                        prepared = prepare_selected_files(
                            paths, workers=4, progress=self._progress_callback
                        )
                    writer.flush()
                    self.events.put(("preview_done", prepared))
                except Exception:
                    writer.flush()
                    self.events.put(("error", traceback.format_exc()))

            threading.Thread(target=worker, daemon=True).start()

        def _apply(self) -> None:
            if self.prepared is None:
                messagebox.showinfo("请先预览", "勾选状态发生变化后，需要重新联网核对。")
                return
            selected = {
                text
                for text, value in self.checked.items()
                if value and text in self.plan_by_path
            }
            plans = [
                plan for plan in self.prepared["plans"]
                if str(plan["path"]) in selected and plan["action"] == "write"
            ]
            if not plans:
                messagebox.showinfo("无需处理", "当前勾选项没有可安全写入的内容。")
                return
            if not messagebox.askyesno(
                "确认写入",
                f"将安全处理 {len(plans)} 首音乐。\n\n"
                "每首先写同目录临时副本，复读验证通过后再替换原文件；"
                "同时保存回滚记录。是否继续？",
            ):
                return
            if not self._confirm_artist_photos(plans):
                return
            plans = [plan for plan in plans if plan["action"] == "write"]
            if not plans:
                messagebox.showinfo(
                    "无需处理",
                    "已选择不使用歌手照片，其余曲目没有需要写入的信息。",
                )
                return
            self._set_busy(True)
            self.stage_var.set("准备安全写入")

            def worker() -> None:
                writer = QueueWriter(self.events)
                try:
                    with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                        result, verification = execute_prepared_files(
                            self.prepared,
                            selected,
                            progress=self._progress_callback,
                        )
                    writer.flush()
                    self.events.put(("apply_done", (result, verification)))
                except Exception:
                    writer.flush()
                    self.events.put(("error", traceback.format_exc()))

            threading.Thread(target=worker, daemon=True).start()

        def _confirm_artist_photos(self, plans: list[dict[str, Any]]) -> bool:
            photo_plans = [plan for plan in plans if plan.get("artist_photo_pending")]
            if not photo_plans:
                return True
            answer = self._ask_artist_photo_confirmation(photo_plans)
            if answer is None:
                return False
            for plan in photo_plans:
                plan["artist_photo_approved"] = bool(answer)
                if not answer and not plan["metadata_changes"]:
                    plan["action"] = "skip"
                    plan["skip_reason"] = "artist photo was not approved"
            self._show_detail()
            return True

        def _ask_artist_photo_confirmation(
            self, photo_plans: list[dict[str, Any]]
        ) -> bool | None:
            names = [
                f"• {plan['filename']}  ←  {plan['target_metadata']['ARTIST']}"
                for plan in photo_plans[:12]
            ]
            if len(photo_plans) > 12:
                names.append(f"……另有 {len(photo_plans) - 12} 首")
            selected_plan = self._selected_plan()
            current_plan = next(
                (plan for plan in photo_plans if plan is selected_plan), photo_plans[0]
            )
            message = (
                "以下曲目没有找到可靠的正式唱片封面，程序找到了歌手照片：\n\n"
                + "\n".join(names)
                + "\n\n选择“是”：将预览中的歌手照片写作封面。"
                "\n选择“否”：不写歌手照片，仍继续写入可补充的曲目信息。"
                "\n选择“取消”：终止本次写入。"
            )

            dialog = tk.Toplevel(self.window)
            dialog.title("二次确认：使用歌手照片")
            dialog.transient(self.window)
            dialog.resizable(False, False)
            result: dict[str, Any] = {"answer": None, "photo": None}

            content = ttk.Frame(dialog, padding=(18, 16, 18, 14))
            content.grid(row=0, column=0, sticky="nsew")

            preview = ttk.Frame(content)
            preview.grid(row=0, column=0, sticky="ew")
            image_box = tk.Frame(
                preview,
                width=300,
                height=300,
                background="#f3f4f6",
                highlightbackground="#c8ccd2",
                highlightthickness=1,
            )
            image_box.grid(row=0, column=0, sticky="nw")
            image_box.grid_propagate(False)
            image_label = tk.Label(
                image_box,
                text="照片预览不可用",
                background="#f3f4f6",
                foreground="#666666",
                font=("Microsoft YaHei UI", 10),
            )
            image_label.place(relx=0.5, rely=0.5, anchor="center")

            preview_side = ttk.Frame(preview, padding=(16, 3, 0, 0), width=250)
            preview_side.grid(row=0, column=1, sticky="nsew")
            preview_side.grid_propagate(False)
            ttk.Label(
                preview_side,
                text="当前歌曲",
                font=("Microsoft YaHei UI", 11, "bold"),
            ).pack(anchor="w")
            song_var = tk.StringVar()
            position_var = tk.StringVar()
            source_var = tk.StringVar()
            ttk.Label(
                preview_side,
                textvariable=song_var,
                wraplength=225,
                justify="left",
            ).pack(anchor="w", pady=(7, 12))
            ttk.Label(
                preview_side,
                textvariable=position_var,
                foreground="#555555",
            ).pack(anchor="w")
            ttk.Label(
                preview_side,
                textvariable=source_var,
                wraplength=225,
                foreground="#555555",
            ).pack(anchor="w", pady=(4, 12))
            photo_buttons = ttk.Frame(preview_side)
            photo_buttons.pack(anchor="w")
            previous_button = ttk.Button(photo_buttons, text="上一张")
            previous_button.pack(side="left")
            next_button = ttk.Button(photo_buttons, text="下一张")
            next_button.pack(side="left", padx=(8, 0))

            ttk.Separator(content, orient="horizontal").grid(
                row=1, column=0, sticky="ew", pady=(15, 13)
            )
            message_row = ttk.Frame(content)
            message_row.grid(row=2, column=0, sticky="ew")
            ttk.Label(
                message_row,
                text="⚠",
                font=("Segoe UI Symbol", 25),
                foreground="#d88700",
            ).grid(row=0, column=0, sticky="n", padx=(0, 12), pady=(0, 0))
            ttk.Label(
                message_row,
                text=message,
                wraplength=510,
                justify="left",
            ).grid(row=0, column=1, sticky="w")

            button_row = ttk.Frame(content)
            button_row.grid(row=3, column=0, sticky="e", pady=(15, 0))

            def finish(answer: bool | None) -> None:
                result["answer"] = answer
                if dialog.winfo_exists():
                    try:
                        dialog.grab_release()
                    except tk.TclError:
                        pass
                    dialog.destroy()

            yes_button = ttk.Button(button_row, text="是", command=lambda: finish(True))
            no_button = ttk.Button(button_row, text="否", command=lambda: finish(False))
            cancel_button = ttk.Button(
                button_row, text="取消", command=lambda: finish(None)
            )
            yes_button.pack(side="left")
            no_button.pack(side="left", padx=(8, 0))
            cancel_button.pack(side="left", padx=(8, 0))

            def refresh_preview() -> None:
                choices = current_plan.get("artist_photo_choices") or []
                index = int(current_plan.get("artist_photo_index") or 0)
                if choices:
                    index %= len(choices)
                    current_plan["artist_photo_index"] = index
                song_var.set(
                    f"{current_plan['filename']}\n"
                    f"艺术家：{current_plan['target_metadata']['ARTIST']}"
                )
                position_var.set(
                    f"第 {index + 1} 张 / 共 {len(choices)} 张"
                    if choices
                    else "当前仅有 1 张"
                )
                source_var.set(
                    f"来源：{current_plan.get('cover_source') or '未记录'}"
                )
                image_data = None
                if current_plan.get("cover_path"):
                    try:
                        image_data = Path(current_plan["cover_path"]).read_bytes()
                    except OSError:
                        pass
                if image_data is None:
                    image_data = current_plan.get("cover_data")
                try:
                    if not image_data:
                        raise ValueError("missing image")
                    with Image.open(io.BytesIO(image_data)) as image:
                        image = ImageOps.contain(
                            image.convert("RGB"),
                            (284, 284),
                            Image.Resampling.LANCZOS,
                        )
                        result["photo"] = ImageTk.PhotoImage(image.copy())
                    image_label.configure(image=result["photo"], text="")
                except Exception:
                    result["photo"] = None
                    image_label.configure(image="", text="照片预览不可用")
                button_state = "normal" if len(choices) > 1 else "disabled"
                previous_button.configure(state=button_state)
                next_button.configure(state=button_state)

            def change_photo(step: int) -> None:
                choices = current_plan.get("artist_photo_choices") or []
                if len(choices) < 2:
                    return
                self._select_artist_photo_choice(
                    current_plan,
                    int(current_plan.get("artist_photo_index") or 0) + step,
                )
                refresh_preview()
                self._show_detail()

            previous_button.configure(command=lambda: change_photo(-1))
            next_button.configure(command=lambda: change_photo(1))
            dialog.protocol("WM_DELETE_WINDOW", lambda: finish(None))
            dialog.bind("<Escape>", lambda _event: finish(None))
            dialog.bind("<Return>", lambda _event: finish(True))
            refresh_preview()
            dialog.update_idletasks()
            width = max(620, dialog.winfo_reqwidth())
            height = dialog.winfo_reqheight()
            x = self.window.winfo_rootx() + max(
                0, (self.window.winfo_width() - width) // 2
            )
            y = self.window.winfo_rooty() + max(
                0, (self.window.winfo_height() - height) // 2
            )
            dialog.geometry(f"{width}x{height}+{x}+{y}")
            dialog.grab_set()
            yes_button.focus_set()

            test_answer = getattr(self, "_artist_photo_dialog_test_answer", "unset")
            if test_answer != "unset":
                dialog.after(180, lambda: finish(test_answer))
            screenshot_path = os.environ.get("MUSIC_ORGANIZER_UI_TEST_SCREENSHOT")
            if screenshot_path:
                def capture_dialog() -> None:
                    from PIL import ImageGrab

                    if os.environ.get("MUSIC_ORGANIZER_UI_TEST_SWITCH_PHOTO") == "1":
                        change_photo(1)
                    dialog.update_idletasks()
                    dialog.update()
                    screenshot = ImageGrab.grab(
                        window=dialog.winfo_id(), include_layered_windows=True
                    )
                    screenshot.save(screenshot_path)
                    json_write(
                        Path(screenshot_path).with_suffix(".json"),
                        {
                            "title": dialog.title(),
                            "size": [dialog.winfo_width(), dialog.winfo_height()],
                            "candidate_index": int(
                                current_plan.get("artist_photo_index") or 0
                            ),
                            "candidate_count": len(
                                current_plan.get("artist_photo_choices") or []
                            ),
                            "buttons": [
                                previous_button.cget("text"),
                                next_button.cget("text"),
                                yes_button.cget("text"),
                                no_button.cget("text"),
                                cancel_button.cget("text"),
                            ],
                        },
                    )
                    finish(None)

                dialog.after(650, capture_dialog)
            self.window.wait_window(dialog)
            return result["answer"]

        def _refresh_local_rows(self) -> None:
            for text, path in self.paths.items():
                track = self.local_tracks.get(text)
                iid = self._iid(path)
                if not track or not self.tree.exists(iid):
                    continue
                meta = track["metadata"]
                self.tree.item(
                    iid,
                    values=(
                        path.name,
                        track["type"],
                        meta["TITLE"] or "（空）",
                        meta["ARTIST"] or "（空）",
                        meta["ALBUM"] or "（空）",
                        meta["YEAR"] or "—",
                        meta["DATE"] or "—",
                        "完整" if metadata_valid(meta) else "缺失",
                        "已有" if track["cover_count"] else "无",
                        "尚未联网",
                    ),
                )

        def _refresh_preview_rows(self) -> None:
            assert self.prepared is not None
            self.plan_by_path = {
                str(plan["path"]): plan for plan in self.prepared["plans"]
            }
            for text, plan in self.plan_by_path.items():
                path = self.paths.get(text)
                if path is None:
                    continue
                iid = self._iid(path)
                if not self.tree.exists(iid):
                    continue
                changes = len(plan["metadata_changes"])
                cover = "保留原封面" if plan["cover_data"] else (
                    "歌手照片（待二次确认）"
                    if plan.get("artist_photo_pending")
                    else "找到正式封面" if plan["cover_ready"] else "无可靠封面"
                )
                result = (
                    f"改 {changes} 项；{cover}"
                    if plan["action"] == "write"
                    else plan.get("skip_reason") or "无需处理"
                )
                values = list(self.tree.item(iid, "values"))
                values[-1] = result
                self.tree.item(iid, values=values)

        def _show_detail(self, _event: Any = None) -> None:
            selection = self.tree.selection()
            if not selection:
                return
            iid = selection[0]
            path = next((path for path in self.paths.values() if self._iid(path) == iid), None)
            if path is None:
                return
            track = self.local_tracks.get(str(path))
            lines = [f"文件：{path.name}", f"路径：{path}", ""]
            image_data = None
            if track:
                meta = track["metadata"]
                lines.extend([
                    "【当前信息】",
                    f"标题：{meta['TITLE'] or '（空）'}",
                    f"艺术家：{meta['ARTIST'] or '（空）'}",
                    f"唱片集：{meta['ALBUM'] or '（空）'}",
                    f"唱片集艺术家：{meta['ALBUMARTIST'] or '（空）'}",
                    f"年份：{meta['YEAR'] or '（空）'}",
                    f"日期：{meta['DATE'] or '（空）'}",
                    f"封面：{'已有' if track['cover_count'] else '无'}",
                ])
                image_data = track.get("cover_data")
            self.detail_text.configure(state="normal")
            self.detail_text.delete("1.0", "end")
            if image_data:
                try:
                    with Image.open(io.BytesIO(image_data)) as image:
                        image = image.convert("RGB")
                        image.thumbnail((225, 225), Image.Resampling.LANCZOS)
                        self.cover_photo = ImageTk.PhotoImage(image.copy())
                    self.detail_text.image_create(
                        "end", image=self.cover_photo, padx=4, pady=4
                    )
                    self.detail_text.insert("end", "\n\n")
                except Exception:
                    self.cover_photo = None
                    self.detail_text.insert("end", "封面预览失败\n\n")
            else:
                self.cover_photo = None
                self.detail_text.insert("end", "无封面\n\n")
            self.detail_text.insert("end", "\n".join(lines))
            self.detail_text.yview_moveto(0.0)
            self.detail_text.configure(state="disabled")

        def _selected_plan(self) -> dict[str, Any] | None:
            selection = self.tree.selection()
            if not selection:
                return None
            iid = selection[0]
            path = next(
                (path for path in self.paths.values() if self._iid(path) == iid), None
            )
            return self.plan_by_path.get(str(path)) if path is not None else None

        @staticmethod
        def _select_artist_photo_choice(plan: dict[str, Any], index: int) -> None:
            choices = plan.get("artist_photo_choices") or []
            if not choices:
                return
            selected_index = index % len(choices)
            selected = choices[selected_index]
            candidate = selected["candidate"]
            plan["artist_photo_index"] = selected_index
            plan["artist_photo_candidate"] = candidate
            plan["cover_path"] = selected["path"]
            plan["cover_image"] = selected["image"]
            plan["cover_source"] = candidate.get("provider") or "artist-photo"
            plan["artist_photo_approved"] = False

        def _poll_events(self) -> None:
            try:
                while True:
                    event, payload = self.events.get_nowait()
                    if event == "log":
                        self._append_log(str(payload))
                    elif event == "progress":
                        phase, done, total, name = payload
                        self.stage_var.set(phase_names.get(phase, phase))
                        self.current_var.set(name)
                        self.progress.configure(maximum=max(total, 1), value=done)
                        self.count_var.set(f"{done}/{total}")
                        if name:
                            path = next((p for p in self.paths.values() if p.name == name), None)
                            if path and self.tree.exists(self._iid(path)):
                                self.tree.see(self._iid(path))
                    elif event == "local_scan_done":
                        tracks, errors = payload
                        self.local_tracks = {str(track["path"]): track for track in tracks}
                        self._refresh_local_rows()
                        self._set_busy(False)
                        self.stage_var.set("本地扫描完成")
                        self.current_var.set("")
                        self.count_var.set(f"{len(tracks)}/{len(self.paths)}")
                        self._update_summary()
                        if errors:
                            self._append_log(f"有 {len(errors)} 个文件读取失败。")
                        if self.auto_online_var.get() and any(self.checked.values()):
                            self.window.after(150, self._preview)
                    elif event == "preview_done":
                        self.prepared = payload
                        self._refresh_preview_rows()
                        preview = payload["preview"]
                        self._set_busy(False)
                        self.stage_var.set("联网预览完成")
                        self.current_var.set("")
                        self.count_var.set(f"{preview['recognized_audio']}/{preview['recognized_audio']}")
                        self._update_summary(
                            f"可写入 {preview['will_write']}｜封面 {preview['cover_ready']}/{preview['recognized_audio']}｜信息完整 {preview['metadata_ready']}/{preview['recognized_audio']}"
                            + (f"｜歌手照片待确认 {preview['artist_photo_pending']}" if preview.get("artist_photo_pending") else "")
                        )
                    elif event == "apply_done":
                        result, verification = payload
                        self._set_busy(False)
                        self.stage_var.set("写入和独立复查完成")
                        self.current_var.set("")
                        messagebox.showinfo(
                            "处理完成",
                            f"成功：{result['succeeded']}\n失败：{result['failed']}\n"
                            f"信息完整：{verification['complete_metadata']}/{verification['total']}\n"
                            f"带封面：{verification['with_cover']}/{verification['total']}\n\n"
                            f"记录位置：\n{self.prepared['run_dir']}",
                        )
                        self.prepared = None
                        self.plan_by_path.clear()
                        self._start_local_scan()
                    elif event == "error":
                        self._append_log(str(payload))
                        self._set_busy(False)
                        self.stage_var.set("发生错误")
                        self.current_var.set("请查看日志；未验证的临时文件不会替换原音乐。")
                        messagebox.showerror("处理失败", "处理过程中发生错误，请查看日志。")
            except queue.Empty:
                pass
            self.window.after(100, self._poll_events)

        def _toggle_log(self) -> None:
            if self.log_visible:
                self.log_frame.grid_remove()
                self.log_button.configure(text="查看日志")
                self.log_visible = False
            else:
                self.log_frame.grid()
                self.log_button.configure(text="隐藏日志")
                self.log_visible = True
            self._save_settings()

        def _open_data_folder(self) -> None:
            APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
            try:
                os.startfile(APP_DATA_DIR)  # type: ignore[attr-defined]
            except Exception as exc:
                messagebox.showerror("无法打开", str(exc))

        def _show_help(self) -> None:
            if self.help_window is not None and self.help_window.winfo_exists():
                self.help_window.deiconify()
                self.help_window.lift()
                self.help_window.focus_force()
                return

            help_text = """使用方法

1. 程序启动后会自动扫描上次使用的文件夹；首次运行时扫描 EXE 所在文件夹。也可以选择其他文件夹，或一次追加多首音乐。

2. 新扫描到或新追加的音乐默认不勾选。重新扫描时，仍在扫描范围内的原有勾选会保留，新出现的音乐保持未勾选。

3. 可逐曲勾选，也可使用“全选”“全不选”“反选”“勾选信息不完整”和“勾选缺失封面”。智能勾选会排除原勾选时，程序会询问是合并、替换还是取消。

4. 点击表格中的曲目，可在右侧同一详情框查看封面与完整信息；滚轮会让封面和文字一起滚动。歌手照片存在多个有效候选时，可点击“换一张歌手照片”，最终采用当前显示的照片。

5. 点击“① 联网核对勾选项”，逐曲核对唱片集资料和正式封面。这一步只生成预览，不修改音乐。

6. 确认联网预览后，点击“② 写入勾选项”。如使用歌手照片，二次确认弹窗上半部分会再次显示当前歌曲和照片，可用“上一张”“下一张”切换候选；下方仍用“是”“否”“取消”决定是否写入照片或终止操作。底部会实时显示当前阶段、歌曲名称和完成进度。

整理范围

只补充或纠正标题、艺术家、唱片集、唱片集艺术家、发行年份和发行日期。唱片集艺术家统一写成与参与创作的艺术家相同。

已有封面默认保留。缺少封面时，优先采用经唱片集、艺术家和发行信息核对的正式封面。正式封面找不到时，可显示与公开音乐目录主歌手名或 Wikipedia 人物页标题严格匹配的歌手照片；相同画面的不同尺寸和裁切会自动合并。存在多张有效候选时可在主界面切换，也可在二次确认弹窗中使用“上一张”“下一张”；选择“是”才写入照片，拒绝后封面保持空白。程序不生成或猜测图片。

文件格式

可写入 FLAC、MP3、AAC、M4A、MP4。WMA/ASF 可以识别，但为避免兼容性风险，不自动写入封面。

安全与记录

写入时会先创建同目录临时文件，写入并复读验证成功后才替换原音乐。

程序数据保存在 EXE 同级的“音乐整理工具数据”文件夹。移动工具时，将 EXE 和该文件夹放在一起即可保留设置、勾选状态、记录和缓存。

“清理日志与缓存”只删除执行记录和已下载的封面缓存，保留设置、上次文件夹和歌曲勾选状态。"""

            dialog = tk.Toplevel(self.window)
            self.help_window = dialog
            dialog.title("使用说明")
            dialog.geometry("760x650")
            dialog.minsize(560, 420)
            dialog.transient(self.window)
            dialog.columnconfigure(0, weight=1)
            dialog.rowconfigure(0, weight=1)

            content = ttk.Frame(dialog, padding=(14, 14, 14, 8))
            content.grid(row=0, column=0, sticky="nsew")
            content.columnconfigure(0, weight=1)
            content.rowconfigure(0, weight=1)
            help_body = tk.Text(
                content,
                wrap="word",
                relief="flat",
                background="#f7f7f7",
                font=("Microsoft YaHei UI", 10),
                padx=14,
                pady=12,
                spacing1=2,
                spacing3=5,
            )
            help_scrollbar = ttk.Scrollbar(
                content, orient="vertical", command=help_body.yview
            )
            help_body.configure(yscrollcommand=help_scrollbar.set)
            help_body.grid(row=0, column=0, sticky="nsew")
            help_scrollbar.grid(row=0, column=1, sticky="ns")
            help_body.insert("1.0", help_text)
            help_body.configure(state="disabled")
            dialog.help_body = help_body

            button_row = ttk.Frame(dialog, padding=(14, 0, 14, 12))
            button_row.grid(row=1, column=0, sticky="ew")

            def close_help(_event: Any = None) -> None:
                if dialog.winfo_exists():
                    dialog.destroy()
                self.help_window = None

            ttk.Button(button_row, text="关闭", command=close_help).pack(side="right")
            dialog.protocol("WM_DELETE_WINDOW", close_help)
            dialog.bind("<Escape>", close_help)
            dialog.after_idle(help_body.focus_set)
            dialog.grab_set()

        def _clean_logs_and_cache(self) -> None:
            targets = (RECORD_DIR, CACHE_DIR)
            counts = []
            for folder in targets:
                try:
                    count = sum(1 for _ in folder.iterdir()) if folder.is_dir() else 0
                except OSError:
                    count = 0
                counts.append(count)
            if not messagebox.askyesno(
                "清理日志与缓存",
                f"将永久删除执行记录 {counts[0]} 项、封面缓存 {counts[1]} 项。\n\n"
                "设置、上次文件夹和歌曲勾选状态会保留。"
                "当前联网预览（如有）将失效。是否继续？",
            ):
                return
            self._invalidate_preview()
            removed = 0
            errors = []
            for folder in targets:
                folder.mkdir(parents=True, exist_ok=True)
                for item in list(folder.iterdir()):
                    try:
                        if item.is_symlink():
                            item.unlink()
                        elif item.is_dir():
                            shutil.rmtree(item)
                        else:
                            item.unlink()
                        removed += 1
                    except OSError as exc:
                        errors.append(f"{item.name}: {exc}")
            self.log_text.delete("1.0", "end")
            self._append_log(f"已清理日志与缓存，共删除 {removed} 项。")
            if errors:
                self._append_log("未能删除：" + "；".join(errors))
                messagebox.showwarning(
                    "部分内容未清理",
                    f"已删除 {removed} 项，另有 {len(errors)} 项无法删除。详情请查看日志。",
                )
            else:
                messagebox.showinfo(
                    "清理完成",
                    f"已删除 {removed} 项。设置和歌曲勾选状态已保留。",
                )

        def _on_close(self) -> None:
            if self.running and not messagebox.askyesno(
                "任务进行中", "任务仍在进行。现在退出可能留下可删除的临时文件，确定退出吗？"
            ):
                return
            self._save_settings()
            self.window.destroy()

    root = tk.Tk()
    app = MusicOrganizerAppV2(root)
    ui_test_report = os.environ.get("MUSIC_ORGANIZER_UI_TEST_REPORT")
    if ui_test_report:
        ui_test_action = os.environ.get("MUSIC_ORGANIZER_UI_TEST_ACTION", "snapshot")
        ui_test_started = time.monotonic()

        def finish_ui_test() -> None:
            if app.running and time.monotonic() - ui_test_started < 20:
                root.after(100, finish_ui_test)
                return
            if ui_test_action == "check-first-rescan-add" and not getattr(
                app, "_ui_rescan_started", False
            ):
                app._ui_rescan_started = True
                first_path = next(iter(app.paths))
                app._set_checked(first_path, True)
                fixture_folder = Path(app.folder_var.get())
                source_path = next(iter(app.paths.values()))
                added_path = fixture_folder / f"新增测试曲目{source_path.suffix}"
                shutil.copy2(source_path, added_path)
                app._ui_added_path = added_path
                app._rescan_current()
                root.after(100, finish_ui_test)
                return
            if ui_test_action.startswith("select-missing-metadata:"):
                app._check_all()
                answer_text = ui_test_action.partition(":")[2]
                answer = {"keep": True, "replace": False, "cancel": None}[answer_text]
                messagebox.askyesnocancel = lambda *_args, **_kwargs: answer
                app._check_missing_metadata()
            elif ui_test_action.startswith("select-missing-cover:"):
                app._check_all()
                answer_text = ui_test_action.partition(":")[2]
                answer = {"keep": True, "replace": False, "cancel": None}[answer_text]
                messagebox.askyesnocancel = lambda *_args, **_kwargs: answer
                app._check_missing_cover()
            elif ui_test_action == "minimum-with-log":
                root.geometry("1120x700")
                if not app.log_visible:
                    app._toggle_log()
            elif ui_test_action == "narrow-table-pane":
                root.update_idletasks()
                content_width = app.table_frame.master.winfo_width()
                try:
                    app.table_frame.master.sashpos(0, max(680, content_width - 430))
                except tk.TclError:
                    pass
            elif ui_test_action == "detail-scroll":
                first = next(iter(app.tree.get_children()), None)
                if first:
                    app.tree.selection_set(first)
                    app._show_detail()
                    root.update_idletasks()
                    app._ui_detail_yview_top = list(app.detail_text.yview())
                    app.detail_text.yview_moveto(1.0)
                    root.update_idletasks()
                    app._ui_detail_yview_bottom = list(app.detail_text.yview())
            elif ui_test_action == "help-dialog":
                app._show_help()
                root.update_idletasks()
            elif ui_test_action.startswith("artist-photo-confirm:"):
                answer_text = ui_test_action.partition(":")[2]
                answer = {"yes": True, "no": False, "cancel": None}[answer_text]
                app._artist_photo_dialog_test_answer = answer
                app._ui_artist_photo_plan = {
                    "filename": "测试曲目.flac",
                    "path": Path("测试曲目.flac"),
                    "target_metadata": {"ARTIST": "测试歌手"},
                    "artist_photo_pending": True,
                    "artist_photo_approved": False,
                    "metadata_changes": {},
                    "action": "write",
                    "skip_reason": None,
                }
                app._ui_artist_photo_continue = app._confirm_artist_photos(
                    [app._ui_artist_photo_plan]
                )
            elif ui_test_action == "artist-photo-confirm-real":
                target = Path(
                    os.environ["MUSIC_ORGANIZER_UI_TEST_TARGET"]
                ).resolve()
                target_text = str(target)
                if not getattr(app, "_ui_real_preview_started", False):
                    if target_text not in app.paths:
                        raise RuntimeError("实验曲目不在当前扫描范围内")
                    app._ui_real_preview_started = True
                    app._check_none()
                    app._set_checked(target_text, True)
                    app._preview()
                    root.after(100, finish_ui_test)
                    return
                plans = [
                    plan
                    for plan in (app.prepared or {}).get("plans", [])
                    if Path(plan["path"]).resolve() == target
                    and plan.get("artist_photo_pending")
                ]
                if len(plans) != 1:
                    raise RuntimeError("实验曲目没有得到唯一的歌手照片预览")
                app._ui_artist_photo_plan = plans[0]
                app._ui_artist_photo_continue = app._confirm_artist_photos(plans)
            elif ui_test_action == "cleanup-confirm":
                messagebox.askyesno = lambda *_args, **_kwargs: True
                messagebox.showinfo = lambda *_args, **_kwargs: None
                messagebox.showwarning = lambda *_args, **_kwargs: None
                app._clean_logs_and_cache()
            root.update_idletasks()
            widgets = list(root.winfo_children())
            buttons = []
            checkbuttons = []
            while widgets:
                widget = widgets.pop()
                widgets.extend(widget.winfo_children())
                if widget.winfo_class() in {"Button", "TButton"}:
                    buttons.append(str(widget.cget("text")))
                elif widget.winfo_class() in {"Checkbutton", "TCheckbutton"}:
                    checkbuttons.append(str(widget.cget("text")))
            children = list(app.tree.get_children())
            first_values = list(app.tree.item(children[0], "values")) if children else []
            if ui_test_action == "save-none":
                app._check_none()
                app._save_settings()
            json_write(
                Path(ui_test_report),
                {
                    "title": root.title(),
                    "geometry": root.geometry(),
                    "folder": app.folder_var.get(),
                    "tree_selectmode": str(app.tree.cget("selectmode")),
                    "tree_columns": list(app.tree.cget("columns")),
                    "checkbox_images": {
                        "checked": bool(app.checkbox_on),
                        "unchecked": bool(app.checkbox_off),
                    },
                    "buttons": sorted(buttons),
                    "options": sorted(checkbuttons),
                    "row_count": len(children),
                    "checked_count": sum(app.checked.values()),
                    "checked_names": sorted(
                        app.paths[text].name
                        for text, checked in app.checked.items()
                        if checked
                    ),
                    "local_track_count": len(app.local_tracks),
                    "first_row_values": first_values,
                    "stage": app.stage_var.get(),
                    "summary": app.summary_var.get(),
                    "root_height": root.winfo_height(),
                    "apply_button_bottom": app.apply_button.winfo_rooty()
                    - root.winfo_rooty()
                    + app.apply_button.winfo_height(),
                    "progress_top": app.progress.winfo_rooty() - root.winfo_rooty(),
                    "toolbar_right": app.preview_button.winfo_rootx()
                    - root.winfo_rootx()
                    + app.preview_button.winfo_width(),
                    "table_right": app.table_frame.winfo_rootx()
                    - root.winfo_rootx()
                    + app.table_frame.winfo_width(),
                    "tree_right": app.tree.winfo_rootx()
                    - root.winfo_rootx()
                    + app.tree.winfo_width(),
                    "preview_button_right": app.preview_button.winfo_rootx()
                    - root.winfo_rootx()
                    + app.preview_button.winfo_width(),
                    "apply_button_right": app.apply_button.winfo_rootx()
                    - root.winfo_rootx()
                    + app.apply_button.winfo_width(),
                    "preview_button_size": [
                        app.preview_button.winfo_width(),
                        app.preview_button.winfo_height(),
                    ],
                    "apply_button_size": [
                        app.apply_button.winfo_width(),
                        app.apply_button.winfo_height(),
                    ],
                    "detail_image_count": len(app.detail_text.image_names()),
                    "detail_has_current_metadata": "【当前信息】"
                    in app.detail_text.get("1.0", "end"),
                    "detail_yview": list(app.detail_text.yview()),
                    "detail_yview_top": getattr(app, "_ui_detail_yview_top", []),
                    "detail_yview_bottom": getattr(
                        app, "_ui_detail_yview_bottom", []
                    ),
                    "help_window_exists": bool(
                        app.help_window is not None
                        and app.help_window.winfo_exists()
                    ),
                    "help_title": app.help_window.title()
                    if app.help_window is not None
                    and app.help_window.winfo_exists()
                    else "",
                    "help_has_core_sections": all(
                        text in app.help_window.help_body.get("1.0", "end")
                        for text in ("使用方法", "整理范围", "文件格式", "安全与记录")
                    )
                    if app.help_window is not None
                    and app.help_window.winfo_exists()
                    else False,
                    "artist_photo_confirm_continue": getattr(
                        app, "_ui_artist_photo_continue", None
                    ),
                    "artist_photo_confirm_approved": getattr(
                        app, "_ui_artist_photo_plan", {}
                    ).get("artist_photo_approved"),
                    "artist_photo_confirm_action": getattr(
                        app, "_ui_artist_photo_plan", {}
                    ).get("action"),
                    "log_visible": app.log_visible,
                    "data_dir": str(APP_DATA_DIR),
                    "settings_exists": SETTINGS_PATH.is_file(),
                    "record_items": len(list(RECORD_DIR.iterdir()))
                    if RECORD_DIR.is_dir()
                    else 0,
                    "cache_items": len(list(CACHE_DIR.iterdir()))
                    if CACHE_DIR.is_dir()
                    else 0,
                },
            )
            added_path = getattr(app, "_ui_added_path", None)
            if added_path and added_path.exists():
                added_path.unlink()
            artist_photo_temp_dir = getattr(app, "_ui_artist_photo_temp_dir", None)
            if artist_photo_temp_dir and artist_photo_temp_dir.is_dir():
                shutil.rmtree(artist_photo_temp_dir)
            root.destroy()

        root.after(700, finish_ui_test)
    root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="一键核对音乐信息并嵌入唱片集封面；默认只生成预览。"
    )
    parser.add_argument("--root", default=str(DEFAULT_MUSIC_DIR))
    parser.add_argument("--apply", action="store_true", help="实际写入文件")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        raise SystemExit(f"音乐文件夹不存在：{root}")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    RECORD_DIR.mkdir(parents=True, exist_ok=True)

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = RECORD_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    print(f"扫描音乐文件夹：{root}", flush=True)
    tracks, scan_errors = scan_music(root)
    reviewed = load_reviewed_seed()
    plans = build_plans(tracks, reviewed)
    print(
        f"识别到 {len(tracks)} 首音乐，已有封面 {sum(track['cover_count'] > 0 for track in tracks)} 首。",
        flush=True,
    )

    catalogs = resolve_catalogs(plans, reviewed, args.workers)
    downloads = download_catalog_covers(catalogs, args.workers)
    finalize_plans(plans, catalogs, downloads)

    preview = {
        "created_at": now_iso(),
        "mode": "apply" if args.apply else "preview",
        "music_root": str(root),
        "total": len(plans),
        "scan_errors": scan_errors,
        "will_write": sum(plan["action"] == "write" for plan in plans),
        "will_skip": sum(plan["action"] != "write" for plan in plans),
        "cover_ready": sum(plan["cover_ready"] for plan in plans),
        "metadata_ready": sum(plan["metadata_ready"] for plan in plans),
        "plans": [safe_result_plan(plan) for plan in plans],
    }
    json_write(run_dir / "执行预览.json", preview)
    print(
        f"预览：可安全处理 {preview['will_write']} 首；"
        f"封面已匹配 {preview['cover_ready']}/{preview['total']}；"
        f"信息完整 {preview['metadata_ready']}/{preview['total']}。",
        flush=True,
    )

    if not args.apply:
        print(f"只读预览完成：{run_dir / '执行预览.json'}")
        print("确认后使用 --apply 才会写入音乐文件。")
        return

    results = apply_plans(root, plans, run_dir)
    result_payload = {
        "completed_at": now_iso(),
        "music_root": str(root),
        "attempted": len(results),
        "succeeded": sum(result.get("success") for result in results),
        "failed": sum(not result.get("success") for result in results),
        "results": results,
    }
    json_write(run_dir / "写入结果.json", result_payload)
    print("正在从目标目录独立复查……", flush=True)
    verification = independent_verify(root)
    json_write(run_dir / "独立复查.json", verification)
    print(
        f"完成：写入成功 {result_payload['succeeded']}/{result_payload['attempted']}；"
        f"当前带封面 {verification['with_cover']}/{verification['total']}；"
        f"信息完整 {verification['complete_metadata']}/{verification['total']}。",
        flush=True,
    )
    print(f"本次记录：{run_dir}")


if __name__ == "__main__":
    if len(sys.argv) == 1 or "--gui" in sys.argv:
        gui_main_v2()
    else:
        main()
