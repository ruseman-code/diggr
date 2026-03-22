"""Waveform display widget with real-time playhead."""
from __future__ import annotations

import threading
from typing import Optional

import numpy as np
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen, QImage, QPixmap
from PyQt6.QtWidgets import QWidget, QSizePolicy

import audio_player as player


def _read_audio(path: str):
    """Return (mono_normalised_float32_array, duration_ms). Raises on failure."""
    import soundfile as sf
    data, sr = sf.read(path, always_2d=True, dtype="float32")
    mono = data.mean(axis=1)
    dur_ms = len(mono) / sr * 1000.0
    peak = float(np.abs(mono).max())
    if peak > 0:
        mono = mono / peak
    return mono, dur_ms


class WaveformWidget(QWidget):
    HEIGHT = 115

    # Colours
    _BG         = QColor("#0d0d1a")
    _WAVE_MID   = QColor("#2a5a9a")   # unplayed portion
    _WAVE_PAST  = QColor("#5aa0e8")   # played portion (brighter)
    _CENTRE     = QColor("#1a2a4a")   # centre-line tint
    _HEAD       = QColor("#ffffff")   # playhead
    _TEXT       = QColor("#44546a")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._samples: Optional[np.ndarray] = None
        self._duration_ms: float = 0.0
        self._loading = False
        self._error: Optional[str] = None
        self._current_path: Optional[str] = None

        # Cache the rendered waveform pixmap so we don't recompute every frame
        self._wave_pixmap: Optional[QPixmap] = None
        self._wave_pixmap_width: int = 0

        # Redraw timer drives the playhead animation
        self._timer = QTimer(self)
        self._timer.setInterval(33)   # ~30 fps
        self._timer.timeout.connect(self.update)
        self._timer.start()

    # ── Public API ─────────────────────────────────────────────────────────

    def load(self, path: str):
        self._current_path = path
        self._samples = None
        self._wave_pixmap = None
        self._error = None
        self._loading = True
        self.update()
        threading.Thread(target=self._bg_load, args=(path,), daemon=True).start()

    def clear(self):
        self._current_path = None
        self._samples = None
        self._wave_pixmap = None
        self._error = None
        self._loading = False
        self.update()

    def duration_ms(self) -> float:
        return self._duration_ms

    # ── Background loader ──────────────────────────────────────────────────

    def _bg_load(self, path: str):
        try:
            samples, dur_ms = _read_audio(path)
            if self._current_path != path:
                return
            self._samples = samples
            self._duration_ms = dur_ms
            self._wave_pixmap = None   # invalidate cache
            self._loading = False
        except Exception as e:
            if self._current_path == path:
                self._error = str(e)
                self._loading = False
        self.update()

    # ── Waveform pixmap (cached) ───────────────────────────────────────────

    def _build_wave_pixmap(self, w: int, h: int) -> QPixmap:
        """Render waveform into a QPixmap once; reuse until resized or file changes."""
        img = QImage(w, h, QImage.Format.Format_RGB32)
        img.fill(self._BG)

        painter = QPainter(img)
        samples = self._samples
        n = len(samples)
        mid = h // 2

        # Subtle centre line
        painter.setPen(QPen(self._CENTRE, 1))
        painter.drawLine(0, mid, w, mid)

        # Waveform: one vertical bar per pixel
        pen = QPen(self._WAVE_MID, 1)
        painter.setPen(pen)
        for px in range(w):
            i0 = int(px / w * n)
            i1 = max(i0 + 1, int((px + 1) / w * n))
            i1 = min(i1, n)
            chunk = samples[i0:i1]
            amp = float(np.abs(chunk).max()) if len(chunk) else 0.0
            bar_h = max(1, int(amp * (mid - 6)))
            painter.drawLine(px, mid - bar_h, px, mid + bar_h)

        painter.end()
        return QPixmap.fromImage(img)

    # ── Paint ──────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        w, h = self.width(), self.height()
        painter = QPainter(self)

        if self._loading:
            painter.fillRect(0, 0, w, h, self._BG)
            painter.setPen(self._TEXT)
            painter.drawText(0, 0, w, h, Qt.AlignmentFlag.AlignCenter,
                             "Loading waveform…")
            painter.end()
            return

        if self._samples is None:
            painter.fillRect(0, 0, w, h, self._BG)
            painter.setPen(self._TEXT)
            msg = ("Waveform unavailable – MP3 not supported by decoder"
                   if self._error else "No file selected")
            painter.drawText(0, 0, w, h, Qt.AlignmentFlag.AlignCenter, msg)
            painter.end()
            return

        # Rebuild pixmap if needed
        if self._wave_pixmap is None or self._wave_pixmap_width != w:
            self._wave_pixmap = self._build_wave_pixmap(w, h)
            self._wave_pixmap_width = w

        # Draw cached waveform
        painter.drawPixmap(0, 0, self._wave_pixmap)

        # Overlay played portion in a brighter colour
        pos_ms = player.get_pos_ms()
        is_playing = player.is_playing()

        if is_playing and pos_ms >= 0 and self._duration_ms > 0:
            frac = min(1.0, pos_ms / self._duration_ms)
            head_px = int(frac * w)

            # Re-paint the played slice brighter
            if head_px > 0:
                mid = h // 2
                n = len(self._samples)
                bright_pen = QPen(self._WAVE_PAST, 1)
                painter.setPen(bright_pen)
                for px in range(head_px):
                    i0 = int(px / w * n)
                    i1 = max(i0 + 1, int((px + 1) / w * n))
                    i1 = min(i1, n)
                    chunk = self._samples[i0:i1]
                    amp = float(np.abs(chunk).max()) if len(chunk) else 0.0
                    bar_h = max(1, int(amp * (mid - 6)))
                    painter.drawLine(px, mid - bar_h, px, mid + bar_h)

            # Playhead line
            painter.setPen(QPen(self._HEAD, 2))
            painter.drawLine(head_px, 0, head_px, h)

        painter.end()

    def resizeEvent(self, event):
        self._wave_pixmap = None   # force rebuild at new width
        super().resizeEvent(event)
