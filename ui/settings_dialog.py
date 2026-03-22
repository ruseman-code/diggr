"""Unified settings dialogue for Diggr.

All settings are written to config.json immediately when changed; there is
no Apply / Cancel step.  Settings that affect the live UI (font size,
library root) are communicated back to the main window via signals that the
caller connects before calling exec().
"""
from __future__ import annotations

import threading
import urllib.error
import urllib.request
import json as _json
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QLineEdit,
    QPushButton, QCheckBox, QFileDialog, QDialogButtonBox, QFrame,
    QScrollArea, QWidget, QSpinBox,
)

import activity_log
import config


class SettingsDialog(QDialog):
    """Modal settings panel.  Connect signals before calling exec():

        font_size_changed(int)              – apply new font size immediately
        library_root_change_requested(str)  – forward to file_browser.load_folder
    """

    font_size_changed = pyqtSignal(int)
    library_root_change_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(520)
        self._build_ui()

    # ── Build ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setSpacing(0)
        outer.setContentsMargins(0, 0, 0, 0)

        # Scroll area so the dialog works at any window height
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        inner_widget = QWidget()
        inner = QVBoxLayout(inner_widget)
        inner.setContentsMargins(16, 16, 16, 16)
        inner.setSpacing(14)

        inner.addWidget(self._build_api_group())
        inner.addWidget(self._build_playback_group())
        inner.addWidget(self._build_export_group())
        inner.addWidget(self._build_display_group())
        inner.addWidget(self._build_library_group())
        inner.addStretch()

        scroll.setWidget(inner_widget)
        outer.addWidget(scroll, stretch=1)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        outer.addWidget(sep)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(self.accept)
        outer.addWidget(btn_box)

    # ── Section: Claude API ───────────────────────────────────────────────

    def _build_api_group(self) -> QGroupBox:
        grp = QGroupBox("Claude API")
        vl = QVBoxLayout(grp)

        vl.addWidget(QLabel("API key:"))

        key_row = QHBoxLayout()
        self._api_edit = QLineEdit()
        self._api_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_edit.setPlaceholderText("sk-ant-…")
        self._api_edit.setText(config.get_api_key())
        self._api_edit.textChanged.connect(self._on_api_key_changed)
        key_row.addWidget(self._api_edit)

        self._show_key_btn = QPushButton("Show")
        self._show_key_btn.setFixedWidth(52)
        self._show_key_btn.setCheckable(True)
        self._show_key_btn.toggled.connect(self._toggle_key_visibility)
        key_row.addWidget(self._show_key_btn)

        self._test_btn = QPushButton("Test connection")
        self._test_btn.clicked.connect(self._test_api_connection)
        key_row.addWidget(self._test_btn)

        vl.addLayout(key_row)

        self._api_status = QLabel("")
        self._api_status.setStyleSheet("font-size: 11px;")
        vl.addWidget(self._api_status)

        return grp

    # ── Section: Playback ─────────────────────────────────────────────────

    def _build_playback_group(self) -> QGroupBox:
        grp = QGroupBox("Playback")
        vl = QVBoxLayout(grp)

        self._auto_play_cb = QCheckBox("Auto-play on file selection")
        self._auto_play_cb.setChecked(config.get_auto_play())
        self._auto_play_cb.toggled.connect(config.set_auto_play)
        vl.addWidget(self._auto_play_cb)

        self._normalise_cb = QCheckBox("Normalise preview volume  (-20 dBFS target)")
        self._normalise_cb.setChecked(config.get_normalise_playback())
        self._normalise_cb.setToolTip(
            "Adjusts playback volume so every sample sounds equally loud.\n"
            "Skipped for files larger than 50 MB."
        )
        self._normalise_cb.toggled.connect(config.set_normalise_playback)
        vl.addWidget(self._normalise_cb)

        return grp

    # ── Section: Export ───────────────────────────────────────────────────

    def _build_export_group(self) -> QGroupBox:
        grp = QGroupBox("Export")
        vl = QVBoxLayout(grp)

        vl.addWidget(QLabel("Default export folder:"))

        row = QHBoxLayout()
        self._export_folder_edit = QLineEdit()
        self._export_folder_edit.setPlaceholderText("Not set — you will be prompted each time")
        self._export_folder_edit.setReadOnly(True)
        self._export_folder_edit.setText(config.get_default_export_folder())
        row.addWidget(self._export_folder_edit)

        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_export_folder)
        row.addWidget(browse_btn)

        clear_btn = QPushButton("Clear")
        clear_btn.setFixedWidth(52)
        clear_btn.clicked.connect(self._clear_export_folder)
        row.addWidget(clear_btn)

        vl.addLayout(row)
        return grp

    # ── Section: Display ──────────────────────────────────────────────────

    def _build_display_group(self) -> QGroupBox:
        grp = QGroupBox("Display")
        vl = QVBoxLayout(grp)

        vl.addWidget(QLabel("Interface font size:"))

        row = QHBoxLayout()
        self._font_dec = QPushButton("A−")
        self._font_dec.setFixedWidth(44)
        self._font_dec.setToolTip("Decrease font size  (⌘−)")
        self._font_dec.clicked.connect(self._font_decrease)

        self._font_label = QLabel(f"{config.get_font_size()}px")
        self._font_label.setMinimumWidth(42)
        self._font_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._font_inc = QPushButton("A+")
        self._font_inc.setFixedWidth(44)
        self._font_inc.setToolTip("Increase font size  (⌘+)")
        self._font_inc.clicked.connect(self._font_increase)

        row.addWidget(self._font_dec)
        row.addWidget(self._font_label)
        row.addWidget(self._font_inc)
        row.addStretch()
        vl.addLayout(row)
        return grp

    # ── Section: Library ──────────────────────────────────────────────────

    def _build_library_group(self) -> QGroupBox:
        grp = QGroupBox("Library")
        vl = QVBoxLayout(grp)

        # Library root (read-only display + change button)
        vl.addWidget(QLabel("Library root folder:"))

        root_row = QHBoxLayout()
        self._root_edit = QLineEdit()
        self._root_edit.setReadOnly(True)
        self._root_edit.setPlaceholderText("No library root set")
        self._root_edit.setText(config.get_library_root())
        root_row.addWidget(self._root_edit)

        change_root_btn = QPushButton("Change…")
        change_root_btn.clicked.connect(self._change_library_root)
        root_row.addWidget(change_root_btn)
        vl.addLayout(root_row)

        hint = QLabel(
            "Changing the root folder will re-index all stored paths relative to the new location."
        )
        hint.setStyleSheet("color: #888; font-size: 11px;")
        hint.setWordWrap(True)
        vl.addWidget(hint)

        # Smart Organise conventions reset
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        vl.addWidget(sep)

        conv_row = QHBoxLayout()
        conv_row.addWidget(QLabel("Smart Organise conventions:"))
        reset_btn = QPushButton("Reset conventions")
        reset_btn.setToolTip(
            "Clears any saved Smart Organise naming/sorting rules from config.json.\n"
            "The next Smart Organise run will perform a full re-organise."
        )
        saved = config.get_smart_organise_conventions()
        status_text = "Conventions saved" if saved else "No conventions saved"
        self._conv_status = QLabel(status_text)
        self._conv_status.setStyleSheet(
            "color: #a6e3a1; font-size: 11px;" if saved
            else "color: #6c7086; font-size: 11px;"
        )
        reset_btn.clicked.connect(self._reset_conventions)
        conv_row.addWidget(reset_btn)
        conv_row.addWidget(self._conv_status)
        conv_row.addStretch()
        vl.addLayout(conv_row)

        return grp

    # ── Slots: API ────────────────────────────────────────────────────────

    def _on_api_key_changed(self, text: str):
        config.set_api_key(text.strip())
        self._api_status.setText("")

    def _toggle_key_visibility(self, visible: bool):
        mode = QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        self._api_edit.setEchoMode(mode)
        self._show_key_btn.setText("Hide" if visible else "Show")

    def _test_api_connection(self):
        key = self._api_edit.text().strip()
        if not key:
            self._api_status.setText("Enter an API key first.")
            self._api_status.setStyleSheet("color: #f0c040; font-size: 11px;")
            return

        self._test_btn.setEnabled(False)
        self._api_status.setText("Testing…")
        self._api_status.setStyleSheet("color: #888; font-size: 11px;")

        # Run the HTTP request in a thread so the UI stays responsive.
        def _run():
            result = _check_api_key(key)
            # Update UI on the main thread via a queued signal; simplest way
            # is to use a lambda and QTimer from within the dialog.
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self._on_test_result(result))

        threading.Thread(target=_run, daemon=True).start()

    def _on_test_result(self, ok: bool | str):
        self._test_btn.setEnabled(True)
        if ok is True:
            self._api_status.setText("✓  Connected")
            self._api_status.setStyleSheet("color: #a6e3a1; font-size: 11px;")
        else:
            self._api_status.setText(f"✗  {ok}")
            self._api_status.setStyleSheet("color: #f38ba8; font-size: 11px;")

    # ── Slots: Export ─────────────────────────────────────────────────────

    def _browse_export_folder(self):
        start = config.get_default_export_folder() or str(Path.home())
        folder = QFileDialog.getExistingDirectory(
            self, "Default export folder", start
        )
        if folder:
            config.set_default_export_folder(folder)
            self._export_folder_edit.setText(folder)

    def _clear_export_folder(self):
        config.set_default_export_folder("")
        self._export_folder_edit.setText("")

    # ── Slots: Display ────────────────────────────────────────────────────

    def _font_increase(self):
        size = config.get_font_size()
        if size < 24:
            size += 1
            config.set_font_size(size)
            self._font_label.setText(f"{size}px")
            self.font_size_changed.emit(size)

    def _font_decrease(self):
        size = config.get_font_size()
        if size > 9:
            size -= 1
            config.set_font_size(size)
            self._font_label.setText(f"{size}px")
            self.font_size_changed.emit(size)

    # ── Slots: Library ────────────────────────────────────────────────────

    def _change_library_root(self):
        start = config.get_library_root() or str(Path.home())
        folder = QFileDialog.getExistingDirectory(
            self, "Select new library root", start
        )
        if not folder or folder == config.get_library_root():
            return
        # Delegate to the main window / file browser via signal (it handles
        # the common-ancestor prompt and path re-relativisation).
        self.library_root_change_requested.emit(folder)
        # Refresh display after the signal handler has run.
        self._root_edit.setText(config.get_library_root())

    def _reset_conventions(self):
        config.clear_smart_organise_conventions()
        activity_log.log("Smart Organise conventions reset by user")
        self._conv_status.setText("Cleared — next run will do a full re-organise")
        self._conv_status.setStyleSheet("color: #f9e2af; font-size: 11px;")


# ── API key check (runs in a background thread) ───────────────────────────────

def _check_api_key(key: str):
    """Return True on success, or an error string on failure."""
    try:
        payload = _json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "hi"}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10):
            return True
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return "Invalid API key"
        return f"HTTP {exc.code}"
    except Exception as exc:
        return f"Connection failed: {exc}"
