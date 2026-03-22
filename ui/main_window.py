"""Main application window – wires together the three panels."""

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QStatusBar, QPushButton, QLabel, QSplitter,
)

import audio_player as player
import database as db
from ui.file_browser import FileBrowser
from ui.filter_panel import FilterPanel
from ui.tag_panel import TagPanel
from ui.waveform_widget import WaveformWidget


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sample Organiser")
        self.resize(1200, 800)
        self._current_path = None
        db.init_db()
        self._build_ui()
        self._apply_stylesheet()
        self._connect_signals()

    # ── Build ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        # Three-panel splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.filter_panel  = FilterPanel()
        self.file_browser  = FileBrowser()
        self.tag_panel     = TagPanel()

        splitter.addWidget(self.filter_panel)
        splitter.addWidget(self.file_browser)
        splitter.addWidget(self.tag_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)

        root.addWidget(splitter, stretch=1)

        # Waveform bar
        self.waveform = WaveformWidget()
        root.addWidget(self.waveform)

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        self._now_playing_label = QLabel("")
        self._now_playing_label.setStyleSheet("color: #8cf; font-size: 11px;")
        self.status_bar.addPermanentWidget(self._now_playing_label)

        self._stop_btn = QPushButton("■ Stop")
        self._stop_btn.setFlat(True)
        self._stop_btn.setStyleSheet("color: #f88;")
        self._stop_btn.clicked.connect(self._stop_playback)
        self._stop_btn.hide()
        self.status_bar.addPermanentWidget(self._stop_btn)

        # Keyboard shortcuts
        QShortcut(QKeySequence("Space"), self).activated.connect(
            self._toggle_playback
        )
        QShortcut(QKeySequence("Return"), self).activated.connect(
            self._play_current
        )

        # Playback-state poll timer (updates status bar)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(400)
        self._poll_timer.timeout.connect(self._poll_playback)
        self._poll_timer.start()

    # ── Signals ───────────────────────────────────────────────────────────

    def _connect_signals(self):
        # Filter panel → file browser
        self.filter_panel.filters_changed.connect(self._apply_filters)

        # File browser → tag panel + play on single click
        self.file_browser.file_selected.connect(self._on_file_selected)
        # double-click / Enter still works but is now redundant
        self.file_browser.file_activated.connect(self._play_file)

        # Tag panel → refresh file browser row (rating/tags changed)
        self.tag_panel.tags_changed.connect(self._apply_filters)

    # ── Slots ──────────────────────────────────────────────────────────────

    def _on_file_selected(self, path: str):
        self._current_path = path
        db.upsert_sample(path)
        self.tag_panel.load_sample(path)
        self.waveform.load(path)
        self._play_file(path)

    def _apply_filters(self):
        self.file_browser.apply_filters(
            self.filter_panel.name_query,
            self.filter_panel.active_tags,
            self.filter_panel.min_rating,
        )
        self.filter_panel.refresh_tags()

    def _play_file(self, path: str):
        self._current_path = path
        player.play(path)
        import os
        self._now_playing_label.setText(f"▶  {os.path.basename(path)}")
        self._stop_btn.show()
        self.status_bar.showMessage(path, 4000)

    def _play_current(self):
        if self._current_path:
            self._play_file(self._current_path)

    def _toggle_playback(self):
        if player.is_playing():
            self._stop_playback()
        elif self._current_path:
            self._play_file(self._current_path)

    def _stop_playback(self):
        player.stop()
        self._now_playing_label.setText("")
        self._stop_btn.hide()

    def _poll_playback(self):
        if not player.is_playing() and self._stop_btn.isVisible():
            self._stop_btn.hide()
            self._now_playing_label.setText("")

    # ── Stylesheet ────────────────────────────────────────────────────────

    def _apply_stylesheet(self):
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #1e1e2e;
                color: #cdd6f4;
                font-family: "Inter", "Segoe UI", sans-serif;
                font-size: 13px;
            }
            QGroupBox {
                border: 1px solid #313244;
                border-radius: 6px;
                margin-top: 8px;
                padding-top: 8px;
                color: #89b4fa;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                top: -1px;
            }
            QTreeView {
                background: #181825;
                alternate-background-color: #1e1e2e;
                border: 1px solid #313244;
                border-radius: 4px;
            }
            QTreeView::item:selected {
                background: #313244;
            }
            QTreeView::item:hover {
                background: #2a2a3e;
            }
            QHeaderView::section {
                background: #181825;
                color: #89b4fa;
                border: none;
                padding: 4px 8px;
                border-bottom: 1px solid #313244;
            }
            QLineEdit, QTextEdit, QSpinBox {
                background: #181825;
                border: 1px solid #313244;
                border-radius: 4px;
                padding: 4px;
                color: #cdd6f4;
            }
            QLineEdit:focus, QTextEdit:focus {
                border-color: #89b4fa;
            }
            QPushButton {
                background: #313244;
                border: 1px solid #45475a;
                border-radius: 4px;
                padding: 4px 10px;
                color: #cdd6f4;
            }
            QPushButton:hover {
                background: #45475a;
            }
            QPushButton:pressed {
                background: #585b70;
            }
            QScrollBar:vertical {
                background: #181825;
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #45475a;
                border-radius: 4px;
                min-height: 20px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QSplitter::handle {
                background: #313244;
                width: 1px;
            }
            QStatusBar {
                background: #181825;
                border-top: 1px solid #313244;
                color: #6c7086;
            }
            QCheckBox {
                spacing: 6px;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
                border: 1px solid #45475a;
                border-radius: 3px;
                background: #181825;
            }
            QCheckBox::indicator:checked {
                background: #89b4fa;
                border-color: #89b4fa;
            }
        """)
