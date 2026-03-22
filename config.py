"""Per-library config and path helpers stored in ~/.sample_organiser/config.json.

Library structure in config.json:
  {
    "active_library": "<uuid>",
    "libraries": {
      "<uuid>": {"name": "Default", "root": "/abs/path", "db_file": "<uuid>.db",
                 "last_folder": "/abs/path/subfolder"},
      ...
    },
    "libraries_migrated": true
  }

All functions that deal with "the current library root" operate on the
active library's entry so callers don't need to pass library IDs around.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Optional


_CONFIG_PATH = Path.home() / ".sample_organiser" / "config.json"
LIBRARIES_DIR = Path.home() / ".sample_organiser" / "libraries"


def _load() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data: dict):
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(data, indent=2))


# ── Library registry ──────────────────────────────────────────────────────────

def get_libraries() -> dict:
    """Return {lib_id: {name, root, db_file, last_folder}} for all libraries."""
    return _load().get("libraries", {})


def get_library(lib_id: str) -> Optional[dict]:
    return get_libraries().get(lib_id)


def get_active_library_id() -> str:
    return _load().get("active_library", "")


def get_active_library() -> Optional[dict]:
    lib_id = get_active_library_id()
    return get_library(lib_id) if lib_id else None


def set_active_library(lib_id: str):
    data = _load()
    data["active_library"] = lib_id
    _save(data)


def create_library(name: str, root: str = "") -> str:
    """Create a new library entry and return its ID (a UUID string)."""
    lib_id = str(uuid.uuid4())
    data = _load()
    data.setdefault("libraries", {})[lib_id] = {
        "name": name,
        "root": root,
        "db_file": f"{lib_id}.db",
        "last_folder": root,
    }
    _save(data)
    return lib_id


def rename_library(lib_id: str, new_name: str):
    data = _load()
    if lib_id in data.get("libraries", {}):
        data["libraries"][lib_id]["name"] = new_name
        _save(data)


def delete_library(lib_id: str):
    """Remove a library entry from config. Caller is responsible for the DB file."""
    data = _load()
    data.get("libraries", {}).pop(lib_id, None)
    _save(data)


def db_path_for_library(lib_id: str) -> Path:
    lib = get_library(lib_id)
    filename = lib["db_file"] if lib else f"{lib_id}.db"
    return LIBRARIES_DIR / filename


# ── Library root (always the active library's root) ───────────────────────────

def get_library_root() -> str:
    lib = get_active_library()
    return lib["root"] if lib else ""


def set_library_root(path: str):
    lib_id = get_active_library_id()
    if not lib_id:
        return
    data = _load()
    libs = data.setdefault("libraries", {})
    if lib_id in libs:
        libs[lib_id]["root"] = path
        _save(data)


# ── Last folder (per active library) ─────────────────────────────────────────

def get_last_folder() -> str:
    """Return the last opened folder for the active library, or ''."""
    lib = get_active_library()
    if not lib:
        return ""
    folder = lib.get("last_folder", "")
    if folder and Path(folder).is_dir():
        return folder
    return ""


def get_raw_last_folder() -> str:
    """Stored last_folder without the existence check (used during migration)."""
    lib = get_active_library()
    return lib.get("last_folder", "") if lib else ""


def set_last_folder(folder: str):
    lib_id = get_active_library_id()
    if not lib_id:
        return
    data = _load()
    libs = data.setdefault("libraries", {})
    if lib_id in libs:
        libs[lib_id]["last_folder"] = folder
        _save(data)


# ── Path helpers (use active library root) ────────────────────────────────────

def to_relative(abs_path: str) -> str:
    """Convert *abs_path* to a path relative to the active library root.

    Returns *abs_path* unchanged when the root is unset or the path falls
    outside the root (so absolute-path fallback continues to work).
    """
    root = get_library_root()
    if not root:
        return abs_path
    try:
        return str(Path(abs_path).relative_to(root))
    except ValueError:
        return abs_path


def to_absolute(path: str) -> str:
    """Resolve *path* to absolute using the active library root.

    Already-absolute paths (legacy or outside-root entries) pass through.
    """
    if Path(path).is_absolute():
        return path
    root = get_library_root()
    if not root:
        return path
    return str(Path(root) / path)


# ── Global user preferences ───────────────────────────────────────────────────
# These are stored at the top level of config.json (not per-library).

def get_api_key() -> str:
    return _load().get("api_key", "")


def set_api_key(key: str):
    data = _load()
    data["api_key"] = key
    _save(data)


def get_default_export_folder() -> str:
    folder = _load().get("default_export_folder", "")
    return folder if folder and Path(folder).is_dir() else ""


def set_default_export_folder(folder: str):
    data = _load()
    data["default_export_folder"] = folder
    _save(data)


def get_normalise_playback() -> bool:
    return bool(_load().get("normalise_playback", False))


def set_normalise_playback(enabled: bool):
    data = _load()
    data["normalise_playback"] = enabled
    _save(data)


def get_auto_play() -> bool:
    return bool(_load().get("auto_play", True))


def set_auto_play(enabled: bool):
    data = _load()
    data["auto_play"] = enabled
    _save(data)


def get_font_size() -> int:
    return max(9, min(24, int(_load().get("font_size", 13))))


def set_font_size(size: int):
    data = _load()
    data["font_size"] = size
    _save(data)


def clear_smart_organise_conventions():
    data = _load()
    data.pop("smart_organise_conventions", None)
    _save(data)


# ── Health check ─────────────────────────────────────────────────────────────

def get_last_health_check() -> str:
    """Return ISO-format date of the last health check, or ''."""
    return _load().get("last_health_check", "")


def set_last_health_check():
    from datetime import date
    data = _load()
    data["last_health_check"] = date.today().isoformat()
    _save(data)


# ── Migration flags ───────────────────────────────────────────────────────────

def is_libraries_migrated() -> bool:
    return _load().get("libraries_migrated", False)


def set_libraries_migrated():
    data = _load()
    data["libraries_migrated"] = True
    _save(data)
