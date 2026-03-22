"""Lightweight JSON config stored alongside the database."""

import json
from pathlib import Path

_CONFIG_PATH = Path.home() / ".sample_organiser" / "config.json"


def _load() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data: dict):
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(data, indent=2))


def get_last_folder() -> str:
    """Return the last opened folder, or '' if unset or folder no longer exists."""
    folder = _load().get("last_folder", "")
    if folder and Path(folder).is_dir():
        return folder
    return ""


def set_last_folder(folder: str):
    data = _load()
    data["last_folder"] = folder
    _save(data)
