"""Persistent, thread-safe activity log for Diggr.

Log entries are appended to ~/.sample_organiser/activity.log.
The file is automatically trimmed to _TRIM_TO lines whenever the total
exceeds _MAX_LINES, dropping the oldest entries first.

Public API
----------
log(message, level="INFO")  – write a timestamped line
error(message)              – shorthand for log(..., level="ERROR")
read_log() -> str           – return full log text (for the viewer dialog)
clear_log()                 – erase the log file
"""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

_LOG_PATH  = Path.home() / ".sample_organiser" / "activity.log"
_MAX_LINES = 5_000
_TRIM_TO   = 4_800   # after trimming, keep this many lines

_lock       = threading.Lock()
_line_count = -1        # -1 = not yet initialised from disk


# ── Internal helpers ──────────────────────────────────────────────────────────

def _ensure_count() -> None:
    global _line_count
    if _line_count >= 0:
        return
    try:
        with _LOG_PATH.open("r", encoding="utf-8", errors="replace") as fh:
            _line_count = sum(1 for _ in fh)
    except (FileNotFoundError, OSError):
        _line_count = 0


def _trim() -> None:
    global _line_count
    try:
        text  = _LOG_PATH.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines(keepends=True)
        kept  = lines[-_TRIM_TO:]
        _LOG_PATH.write_text("".join(kept), encoding="utf-8")
        _line_count = len(kept)
    except OSError:
        pass


# ── Public API ────────────────────────────────────────────────────────────────

def log(message: str, level: str = "INFO") -> None:
    """Append a single timestamped line to the activity log."""
    global _line_count
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} [{level:<5}] {message}\n"

    with _lock:
        _ensure_count()
        try:
            _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with _LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(line)
            _line_count += 1
            if _line_count > _MAX_LINES:
                _trim()
        except OSError:
            pass   # never crash the app over a logging failure


def error(message: str) -> None:
    log(message, level="ERROR")


def read_log() -> str:
    """Return the full contents of the log file as a string."""
    try:
        return _LOG_PATH.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, OSError):
        return ""


def clear_log() -> None:
    """Erase the log file."""
    global _line_count
    with _lock:
        try:
            _LOG_PATH.write_text("", encoding="utf-8")
            _line_count = 0
        except OSError:
            pass
