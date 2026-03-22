"""Activity Log viewer dialog."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QMessageBox, QDialogButtonBox, QFrame,
)

import activity_log


class ActivityLogDialog(QDialog):
    """Modal dialog showing the persistent activity log."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Activity Log")
        self.setMinimumSize(700, 480)
        self._build_ui()
        self._load()

    # ── Build ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # Info bar
        info_hl = QHBoxLayout()
        self._info_label = QLabel()
        self._info_label.setStyleSheet("color: #888; font-size: 11px;")
        info_hl.addWidget(self._info_label)
        info_hl.addStretch()
        root.addLayout(info_hl)

        # Log text area
        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        mono = QFont("Menlo")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(11)
        self._text.setFont(mono)
        self._text.setStyleSheet(
            "background: #1e1e2e; color: #cdd6f4; border: 1px solid #45475a;"
        )
        root.addWidget(self._text)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep)

        # Button row
        btn_hl = QHBoxLayout()

        self._clear_btn = QPushButton("Clear Log")
        self._clear_btn.setToolTip("Erase the entire activity log")
        self._clear_btn.clicked.connect(self._on_clear)
        btn_hl.addWidget(self._clear_btn)

        btn_hl.addStretch()

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._load)
        btn_hl.addWidget(refresh_btn)

        close_btn = QPushButton("Close")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        btn_hl.addWidget(close_btn)

        root.addLayout(btn_hl)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _load(self):
        text = activity_log.read_log()
        self._text.setPlainText(text)
        # Scroll to bottom
        sb = self._text.verticalScrollBar()
        sb.setValue(sb.maximum())

        lines = text.count("\n") if text else 0
        log_path = Path.home() / ".sample_organiser" / "activity.log"
        self._info_label.setText(
            f"{lines} line{'s' if lines != 1 else ''}  •  {log_path}"
        )
        self._clear_btn.setEnabled(bool(text.strip()))

    def _on_clear(self):
        reply = QMessageBox.question(
            self,
            "Clear activity log",
            "Are you sure you want to erase the entire activity log?\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        activity_log.clear_log()
        activity_log.log("Activity log cleared by user")
        self._load()
