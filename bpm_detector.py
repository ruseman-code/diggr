"""Background audio analysis (BPM + musical key) via librosa.

detect_folder(paths) processes a list of files in a single daemon thread,
loading each file once and estimating both tempo and key from the same audio
data.  Calling detect_folder() again cancels any in-progress batch first.

analysis_ready emits (path, bpm, key) where:
  bpm  == 0.0  means BPM detection failed
  key  == ""   means key detection failed
"""
from __future__ import annotations

import threading
from typing import List, Tuple

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

# ── Krumhansl–Kessler key profiles (indices 0–11 = C … B) ────────────────────
_KK_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
                       2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_KK_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
                       2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
_KK_MAJOR /= _KK_MAJOR.sum()
_KK_MINOR /= _KK_MINOR.sum()

_NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F',
               'F#', 'G', 'G#', 'A', 'A#', 'B']


def all_keys() -> List[str]:
    """All 24 key names in chromatic order, usable as filter values."""
    return [f"{n} {m}" for n in _NOTE_NAMES for m in ("major", "minor")]


class BpmDetector(QObject):
    # path, bpm (0.0 = failed), key ("" = failed)
    analysis_ready = pyqtSignal(str, float, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancel = threading.Event()

    # ── Public API ─────────────────────────────────────────────────────────

    def detect_folder(self, paths: List[str]):
        """Cancel any running batch and start a new one over *paths*."""
        self._cancel.set()
        self._cancel = threading.Event()
        threading.Thread(
            target=self._run_batch,
            args=(paths, self._cancel),
            daemon=True,
        ).start()

    def cancel(self):
        self._cancel.set()

    # ── Internals ──────────────────────────────────────────────────────────

    def _run_batch(self, paths: List[str], cancel: threading.Event):
        import librosa  # import once per thread
        for path in paths:
            if cancel.is_set():
                return
            bpm, key = self._analyse(path, librosa)
            self.analysis_ready.emit(path, bpm, key)

    def _analyse(self, path: str, librosa) -> Tuple[float, str]:
        try:
            y, sr = librosa.load(path, sr=None, mono=True)
            bpm = self._estimate_bpm(y, sr, librosa)
            key = self._estimate_key(y, sr, librosa)
            return bpm, key
        except Exception:
            return 0.0, ""

    def _estimate_bpm(self, y, sr, librosa) -> float:
        try:
            tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
            bpm = float(np.atleast_1d(tempo)[0])
            if bpm < 20 or bpm > 400:
                return 0.0
            return round(bpm, 1)
        except Exception:
            return 0.0

    def _estimate_key(self, y, sr, librosa) -> str:
        try:
            chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
            chroma_mean = chroma.mean(axis=1)
            total = chroma_mean.sum()
            if total == 0:
                return ""
            chroma_mean /= total

            best_key, best_corr = "", -np.inf
            for root in range(12):
                for mode, profile in (("major", _KK_MAJOR), ("minor", _KK_MINOR)):
                    rotated = np.roll(profile, root)
                    corr = float(np.corrcoef(chroma_mean, rotated)[0, 1])
                    if corr > best_corr:
                        best_corr = corr
                        best_key = f"{_NOTE_NAMES[root]} {mode}"
            return best_key
        except Exception:
            return ""
