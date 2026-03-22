"""Thin wrapper around pygame.mixer for non-blocking audio preview."""

import os
import numpy as np
import pygame
from pathlib import Path

_initialised = False

# -20 dBFS target RMS for normalisation; clamp gain to [0.05, 4.0]
_TARGET_RMS = 0.1
_MAX_NORMALISE_BYTES = 50 * 1024 * 1024   # skip normalisation for files > 50 MB


def _ensure_init():
    global _initialised
    if not _initialised:
        pygame.mixer.pre_init(frequency=44100, size=-16, channels=2, buffer=512)
        pygame.mixer.init()
        _initialised = True


def _compute_volume_factor(path: str) -> float:
    """Return a gain factor that brings the file to approximately -20 dBFS RMS.

    Decodes the file once using soundfile (WAV/AIFF/FLAC) or miniaudio (MP3).
    Returns 1.0 on any error or if the file is too large to process quickly.
    """
    try:
        if os.path.getsize(path) > _MAX_NORMALISE_BYTES:
            return 1.0
        ext = Path(path).suffix.lower()
        if ext == ".mp3":
            import miniaudio
            decoded = miniaudio.decode_file(
                path, output_format=miniaudio.SampleFormat.FLOAT32
            )
            data = np.frombuffer(decoded.samples, dtype=np.float32)
        else:
            import soundfile as sf
            data, _ = sf.read(path, dtype="float32", always_2d=False)
        if data.size == 0:
            return 1.0
        rms = float(np.sqrt(np.mean(data.astype(np.float64) ** 2)))
        if rms < 1e-9:
            return 1.0
        return float(min(max(_TARGET_RMS / rms, 0.05), 4.0))
    except Exception:
        return 1.0


def play(path: str, normalise: bool = False):
    """Play *path*, stopping whatever is currently playing.

    If *normalise* is True the playback volume is adjusted so the sample
    plays at approximately -20 dBFS RMS regardless of its recorded level.
    """
    _ensure_init()
    p = Path(path)
    if not p.exists():
        return
    try:
        volume = _compute_volume_factor(path) if normalise else 1.0
        pygame.mixer.music.load(str(p))
        pygame.mixer.music.set_volume(volume)
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
