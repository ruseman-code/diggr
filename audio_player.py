"""Thin wrapper around pygame.mixer for non-blocking audio preview."""

import pygame
from pathlib import Path

_initialised = False


def _ensure_init():
    global _initialised
    if not _initialised:
        pygame.mixer.pre_init(frequency=44100, size=-16, channels=2, buffer=512)
        pygame.mixer.init()
        _initialised = True


def play(path: str):
    """Play *path*, stopping whatever is currently playing."""
    _ensure_init()
    p = Path(path)
    if not p.exists():
        return
    try:
        pygame.mixer.music.load(str(p))
        pygame.mixer.music.play()
    except pygame.error:
        pass


def stop():
    if _initialised:
        pygame.mixer.music.stop()


def is_playing() -> bool:
    if not _initialised:
        return False
    return pygame.mixer.music.get_busy()


def get_pos_ms() -> int:
    """Milliseconds into the current track, or -1 if not playing."""
    if not _initialised:
        return -1
    return pygame.mixer.music.get_pos()


def toggle(path: str):
    if is_playing():
        stop()
    else:
        play(path)
