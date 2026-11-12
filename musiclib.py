"""Audio upload validation.

Pure helpers, no Flask and no I/O, so the rules for "what is an acceptable
music file" can be tested without booting the app or touching the database.
"""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata

# extension -> kind understood by the PalPiano mod
EXTENSIONS = {
    ".mid": "midi",
    ".midi": "midi",
    ".wav": "audio",
    ".mp3": "audio",
    ".flac": "audio",
    ".ogg": "audio",
    ".oga": "audio",
    ".opus": "audio",
    ".m4a": "audio",
    ".aac": "audio",
    ".wma": "audio",
}

# Shown in the UI and used to build the <input accept> list.
ACCEPT = ",".join(sorted(EXTENSIONS))

MAX_NAME = 80

# ASF/WMA header GUID
_ASF = bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c")


def extension(filename: str) -> str:
    """Lower-case extension including the dot, or ""."""
    return os.path.splitext(os.path.basename(filename or ""))[1].lower()


def kind(ext: str) -> str | None:
    return EXTENSIONS.get((ext or "").lower())


def display_name(filename: str) -> str:
    """Human name for the menu: basename, no extension, printable, bounded."""
    stem = os.path.splitext(os.path.basename(filename or ""))[0]
    stem = unicodedata.normalize("NFKC", stem)
    stem = "".join(ch for ch in stem if ch.isprintable())
    stem = re.sub(r"\s+", " ", stem).strip(" ._-")
    if not stem:
        stem = "track"
    return stem[:MAX_NAME]


def identifier(sha256_hex: str) -> str:
    """Stable, filesystem-safe id derived from content."""
    return (sha256_hex or "")[:32]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sniff(ext: str, head: bytes) -> bool:
    """True when the first bytes look like the container the extension claims.

    Deliberately permissive inside each format (MP3 frame sync varies, M4A can
    start with a free box) but it rejects the common failure of renaming a
    random file to .mp3.
    """
    ext = (ext or "").lower()
    head = head or b""
    if ext in (".mid", ".midi"):
        return head[:4] == b"MThd"
    if ext == ".wav":
        return len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WAVE"
    if ext == ".mp3":
        if head[:3] == b"ID3":
            return True
        return len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0
    if ext == ".flac":
        return head[:4] == b"fLaC"
    if ext in (".ogg", ".oga", ".opus"):
        return head[:4] == b"OggS"
    if ext in (".m4a", ".aac"):
        if len(head) >= 8 and head[4:8] == b"ftyp":
            return True
        return len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xF0) == 0xF0
    if ext == ".wma":
        return head[:16] == _ASF
    return False


def validate(filename: str, data: bytes, max_bytes: int) -> dict:
    """Checks an upload. Returns {ext, name, kind, bytes, sha256, id}.

    Raises ValueError with a message safe to show the user.
    """
    if not filename:
        raise ValueError("a file name is required")
    ext = extension(filename)
    if ext not in EXTENSIONS:
        raise ValueError(f"unsupported type {ext or '(none)'}; allowed: {ACCEPT}")
    if not data:
        raise ValueError("the file is empty")
    if len(data) > max_bytes:
        raise ValueError(f"file is larger than the {max_bytes // (1024 * 1024)} MB limit")
    if not sniff(ext, data[:8192]):
        raise ValueError(f"content does not look like a {ext} file")

    digest = sha256(data)
    return {
        "ext": ext,
        "name": display_name(filename),
        "kind": EXTENSIONS[ext],
        "bytes": len(data),
        "sha256": digest,
        "id": identifier(digest),
    }
