"""Main application window – wires together the three panels."""

import shutil
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QStatusBar, QPushButton, QLabel, QSplitter,
)

import activity_log
import audio_player as player
import config
import database as db
from bpm_detector import BpmDetector
from ui.activity_log_dialog import ActivityLogDialog
from ui.file_browser import FileBrowser
from ui.filter_panel import FilterPanel
from ui.export_dialog import ExportDialog
from ui.health_check_dialog import HealthCheckDialog
from ui.library_toolbar import LibraryToolbar
from ui.settings_dialog import SettingsDialog
from ui.project_panel import ProjectPanel, _LIBRARY_ID
from ui.tag_panel import TagPanel
from ui.waveform_widget import WaveformWidget


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sample Organiser")
        self.resize(1200, 800)
        self._current_path = None
        self._font_size = config.get_font_size()
        self._bpm_pending = 0
        self._bpm_detector = BpmDetector(self)
        self._setup_active_library()  # migration + active DB path; must precede init_db
        db.init_db()
        self._build_ui()
        self._apply_stylesheet()
        self._connect_signals()
        self._restore_last_folder()
        # Delay the monthly health-check prompt so the window finishes painting first.
        QTimer.singleShot(800, self._maybe_prompt_health_check)

    # ── Build ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        # File menu
        file_menu = self.menuBar().addMenu("File")
        export_act = file_menu.addAction("Export Library Data…")
        export_act.setShortcut(QKeySequence("Ctrl+E"))
        export_act.triggered.connect(self._on_export_library)
        file_menu.addSeparator()
        health_act = file_menu.addAction("Library Health Check…")
        health_act.setShortcut(QKeySequence("Ctrl+Shift+H"))
        health_act.triggered.connect(self._run_health_check)

        # Library toolbar (top of window)
        self.library_toolbar = LibraryToolbar(self)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.library_toolbar)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        # Left column: project list (top) + filter panel (bottom)
        self.project_panel = ProjectPanel()
        self.filter_panel  = FilterPanel()

        left_col = QSplitter(Qt.Orientation.Vertical)
        left_col.addWidget(self.project_panel)
        left_col.addWidget(self.filter_panel)
        left_col.setSizes([220, 480])
        left_col.setMinimumWidth(160)
        left_col.setMaximumWidth(300)

        self.file_browser  = FileBrowser()
        self.tag_panel     = TagPanel()

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left_col)
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

        self._bpm_progress_label = QLabel("")
        self._bpm_progress_label.setStyleSheet("color: #6c7086; font-size: 11px;")
        self.status_bar.addWidget(self._bpm_progress_label)   # left-aligned

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
        # Cmd+/Cmd- for font size (Qt maps Cmd→Ctrl on macOS)
        QShortcut(QKeySequence("Ctrl++"), self).activated.connect(self._font_increase)
        QShortcut(QKeySequence("Ctrl+="), self).activated.connect(self._font_increase)
        QShortcut(QKeySequence("Ctrl+-"), self).activated.connect(self._font_decrease)

        # Playback-state poll timer (updates status bar)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(400)
        self._poll_timer.timeout.connect(self._poll_playback)
        self._poll_timer.start()

    # ── Signals ───────────────────────────────────────────────────────────

    def _setup_active_library(self):
        """One-time migration to the multi-library system, then point database.py
        at the correct SQLite file for the active library.

        Safe to call on every launch — is a no-op after the first run.
        """
        if config.is_libraries_migrated():
            # Already migrated: just wire up the DB path.
            lib_id = config.get_active_library_id()
            if lib_id:
                db.set_active_db(config.db_path_for_library(lib_id))
            return

        # ── First launch after the multi-library update ───────────────────
        # Read raw old config values before the new schema overwrites them.
        raw = {}
        try:
            import json
            raw = json.loads((Path.home() / ".sample_organiser" / "config.json").read_text())
        except Exception:
            pass

        old_root = raw.get("library_root", "") or raw.get("last_folder", "")
        old_last  = raw.get("last_folder", "")
        old_paths_migrated = raw.get("paths_migrated", False)

        # Create the Default library entry.
        lib_id = config.create_library("Default", old_root)
        if old_last:
            config.set_last_folder(old_last)      # writes into the library entry
        config.set_active_library(lib_id)

        # Set up the DB directory and path.
        config.LIBRARIES_DIR.mkdir(parents=True, exist_ok=True)
        new_db = config.db_path_for_library(lib_id)
        db.set_active_db(new_db)

        # Copy the old monolithic library.db into the new location.
        legacy = Path.home() / ".sample_organiser" / "library.db"
        if legacy.exists() and not new_db.exists():
            shutil.copy2(legacy, new_db)

        # Initialise schema on the new DB, then migrate paths if needed.
        db.init_db()
        if old_root and not old_paths_migrated:
            db.migrate_to_relative_paths(old_root)

        config.set_libraries_migrated()

    def _restore_last_folder(self):
        folder = config.get_last_folder()
        if folder:
            self.file_browser.load_folder(folder)

    def _connect_signals(self):
        # Library toolbar → switch active library / health check / settings / log / smart organise
        self.library_toolbar.library_switched.connect(self._on_library_switched)
        self.library_toolbar.health_check_requested.connect(self._run_health_check)
        self.library_toolbar.settings_requested.connect(self._open_settings)
        self.library_toolbar.activity_log_requested.connect(self._open_activity_log)
        self.library_toolbar.smart_organise_requested.connect(self._on_smart_organise)

        # Filter panel → file browser
        self.filter_panel.filters_changed.connect(self._apply_filters)

        # Project panel → switch project filter; file browser → refresh project counts
        self.project_panel.project_changed.connect(self._on_project_changed)
        self.file_browser.projects_modified.connect(self.project_panel.refresh)

        # Persist folder choice
        self.file_browser.folder_changed.connect(config.set_last_folder)

        # After a folder is scanned, kick off batch BPM detection
        self.file_browser.scan_complete.connect(self._on_scan_complete)

        # File browser → tag panel + play on single click
        self.file_browser.file_selected.connect(self._on_file_selected)
        self.file_browser.file_activated.connect(self._play_file)

        # Tag panel → refresh file browser row (rating/tags changed)
        self.tag_panel.tags_changed.connect(self._apply_filters)

        # Audio analyser → update DB, browser row, tag panel
        self._bpm_detector.analysis_ready.connect(self._on_analysis_ready)

    # ── Slots ──────────────────────────────────────────────────────────────

    def _on_scan_complete(self, all_paths: list):
        """Queue analysis for every file missing BPM or key."""
        activity_log.log(f"Folder scanned: {len(all_paths)} file{'s' if len(all_paths) != 1 else ''} found")
        needs = [
            p for p in all_paths
            if not (s := db.get_sample(p)) or not s["bpm"] or not s["key"]
        ]
        self._bpm_pending = len(needs)
        if needs:
            n = self._bpm_pending
            self._bpm_progress_label.setText(
                f"Analysing {n} file{'s' if n != 1 else ''}…"
            )
            activity_log.log(f"BPM/key detection started for {n} file{'s' if n != 1 else ''}")
            self._bpm_detector.detect_folder(needs)
        else:
            self._bpm_progress_label.setText("")

    def _on_file_selected(self, path: str):
        self._current_path = path
        db.upsert_sample(path)
        self.tag_panel.load_sample(path)
        self.waveform.load(path)
        if config.get_auto_play():
            self._play_file(path)

    def _on_analysis_ready(self, path: str, bpm: float, key: str):
        db.set_bpm(path, bpm)
        db.set_key(path, key)
        self.file_browser.update_file_analysis(path, bpm, key)

        # Update tag panel if this file is currently selected
        if path == self._current_path:
            if bpm > 0:
                self.tag_panel.set_bpm(bpm)
            else:
                self.tag_panel.set_bpm_failed()
            if key:
                self.tag_panel.set_key(key)
            else:
                self.tag_panel.set_key_failed()

        # Count down progress label
        self._bpm_pending = max(0, self._bpm_pending - 1)
        if self._bpm_pending > 0:
            n = self._bpm_pending
            self._bpm_progress_label.setText(
                f"Analysing {n} file{'s' if n != 1 else ''}…"
            )
        else:
            if self._bpm_progress_label.text():
                activity_log.log("BPM/key detection batch complete")
            self._bpm_progress_label.setText("")

    def _on_export_library(self):
        dlg = ExportDialog(self)
        dlg.exec()

    def _open_activity_log(self):
        dlg = ActivityLogDialog(self)
        dlg.exec()

    def _on_smart_organise(self):
        """Entry point for the Smart Organise feature."""
        from PyQt6.QtWidgets import QDialog, QMessageBox
        from ui.smart_organise_dialog import SmartOrganiseDialog
        from ui.smart_organise_preview_dialog import SmartOrganisePreviewDialog

        # Require an API key — nudge the user towards Settings if missing
        if not config.get_api_key():
            reply = QMessageBox.question(
                self,
                "API key required",
                "Smart Organise uses the Claude AI API, which requires an API key.\n\n"
                "Open Settings to add your key?\n\n"
                "Your key is stored locally in config.json and never shared.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._open_settings()
            return

        # Step 1 – API call
        api_dlg = SmartOrganiseDialog(self)
        if api_dlg.exec() != QDialog.DialogCode.Accepted:
            return

        result_data = api_dlg.result_data
        if not result_data or not result_data.get("operations"):
            QMessageBox.information(
                self,
                "No changes suggested",
                "Claude didn't suggest any file operations for this library.\n\n"
                "Your library may already be well-organised, or the samples may\n"
                "lack enough metadata (BPM, key, tags) for Claude to make\n"
                "confident suggestions.",
            )
            return

        # Step 2 – Preview + backup + apply
        preview = SmartOrganisePreviewDialog(result_data, self)
        preview.apply_complete.connect(self._on_smart_organise_applied)
        preview.exec()

    def _on_smart_organise_applied(self):
        """Reload the library root folder after Smart Organise moves files."""
        root = config.get_library_root()
        if root:
            self.file_browser.load_folder(root)
        else:
            folder = config.get_last_folder()
            if folder:
                self.file_browser.load_folder(folder)
            else:
                self.file_browser.refresh()

    def _run_health_check(self):
        dlg = HealthCheckDialog(self)
        dlg.exec()

        if dlg.files_were_removed:
            self.file_browser.refresh()
            self.project_panel.refresh()

        if dlg.bpm_paths:
            n = len(dlg.bpm_paths)
            self._bpm_pending = n
            self._bpm_progress_label.setText(
                f"Analysing {n} file{'s' if n != 1 else ''}…"
            )
            self._bpm_detector.detect_folder(dlg.bpm_paths)

    def _maybe_prompt_health_check(self):
        """Prompt once a month to run a health check — silently skips the very
        first launch so new users aren't immediately nagged."""
        last = config.get_last_health_check()
        if not last:
            # Record today so we have a baseline, but don't show a prompt.
            config.set_last_health_check()
            return
        from datetime import date
        try:
            days = (date.today() - date.fromisoformat(last)).days
        except ValueError:
            return
        if days >= 30:
            from PyQt6.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self,
                "Library health check",
                f"Your last library health check was {days} day{'s' if days != 1 else ''} ago.\n"
                "Run a health check now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._run_health_check()

    def _on_library_switched(self, lib_id: str):
        """Switch to a different library: swap DB, reload file browser."""
        lib = config.get_library(lib_id)
        lib_name = lib["name"] if lib else lib_id
        activity_log.log(f"Switched to library: {lib_name}")

        # Stop any in-flight BPM analysis.
        self._bpm_detector.cancel()
        self._bpm_pending = 0
        self._bpm_progress_label.setText("")

        # Update config and DB connection.
        config.set_active_library(lib_id)
        db.set_active_db(config.db_path_for_library(lib_id))
        db.init_db()

        # Refresh toolbar and project panel (they read from the new DB/config).
        self.library_toolbar.refresh()
        self.project_panel.refresh()

        # Clear current playback and tag panel.
        self._stop_playback()
        self.tag_panel.clear()
        self._current_path = None

        # Load the library's last folder, or clear the browser if none set.
        folder = config.get_last_folder()
        if folder:
            self.file_browser.load_folder(folder)
        else:
            self.file_browser.clear_folder()

    def _on_project_changed(self, project_id: int):
        # _LIBRARY_ID (-1) → no project filter
        self.file_browser.set_project(
            None if project_id == _LIBRARY_ID else project_id
        )

    def _apply_filters(self):
        self.file_browser.apply_filters(
            self.filter_panel.name_query,
            self.filter_panel.active_tags,
            self.filter_panel.min_rating,
            self.filter_panel.min_bpm,
            self.filter_panel.max_bpm,
            self.filter_panel.key_filter,
            self.filter_panel.smart_query,
        )
        self.filter_panel.refresh_tags()

    def _play_file(self, path: str):
        self._current_path = path
        player.play(path, normalise=config.get_normalise_playback())
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

    def _font_increase(self):
        if self._font_size < 24:
            self._font_size += 1
            config.set_font_size(self._font_size)
            self._apply_stylesheet()

    def _font_decrease(self):
        if self._font_size > 9:
            self._font_size -= 1
            config.set_font_size(self._font_size)
            self._apply_stylesheet()

    def _open_settings(self):
        dlg = SettingsDialog(self)
        dlg.font_size_changed.connect(self._on_settings_font_size)
        dlg.library_root_change_requested.connect(self.file_browser.load_folder)
        dlg.exec()

    def _on_settings_font_size(self, size: int):
        """Apply a font size change from the Settings dialog."""
        self._font_size = size
        self._apply_stylesheet()

    # ── Stylesheet ────────────────────────────────────────────────────────

    def _apply_stylesheet(self):
        fs = self._font_size
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background-color: #1e1e2e;
                color: #cdd6f4;
                font-family: "Inter", "Segoe UI", sans-serif;
                font-size: {fs}px;
            }}
            QGroupBox {{
                border: 1px solid #313244;
                border-radius: 6px;
                margin-top: 8px;
                padding-top: 8px;
                color: #89b4fa;
                font-weight: bold;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 8px;
                top: -1px;
            }}
            QTreeView {{
                background: #181825;
                alternate-background-color: #1e1e2e;
                border: 1px solid #313244;
                border-radius: 4px;
            }}
            QTreeView::item:selected {{
                background: #313244;
            }}
            QTreeView::item:hover {{
                background: #2a2a3e;
            }}
            QHeaderView::section {{
                background: #181825;
                color: #89b4fa;
                border: none;
                padding: 4px 8px;
                border-bottom: 1px solid #313244;
            }}
            QLineEdit, QTextEdit, QSpinBox {{
                background: #181825;
                border: 1px solid #313244;
                border-radius: 4px;
                padding: 4px;
                color: #cdd6f4;
            }}
            QLineEdit:focus, QTextEdit:focus {{
                border-color: #89b4fa;
            }}
            QPushButton {{
                background: #313244;
                border: 1px solid #45475a;
                border-radius: 4px;
                padding: 4px 10px;
                color: #cdd6f4;
            }}
            QPushButton:hover {{
                background: #45475a;
            }}
            QPushButton:pressed {{
                background: #585b70;
            }}
            QScrollBar:vertical {{
                background: #181825;
                width: 8px;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical {{
                background: #45475a;
                border-radius: 4px;
                min-height: 20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
            QSplitter::handle {{
                background: #313244;
                width: 1px;
            }}
            QStatusBar {{
                background: #181825;
                border-top: 1px solid #313244;
                color: #6c7086;
            }}
            QCheckBox {{
                spacing: 6px;
            }}
            QCheckBox::indicator {{
                width: 14px;
                height: 14px;
                border: 1px solid #45475a;
                border-radius: 3px;
                background: #181825;
            }}
            QCheckBox::indicator:checked {{
                background: #89b4fa;
                border-color: #89b4fa;
            }}
            QListWidget {{
                background: #181825;
                border: 1px solid #313244;
                border-radius: 4px;
                alternate-background-color: #1e1e2e;
            }}
            QListWidget::item {{
                padding: 4px 6px;
                border-radius: 3px;
            }}
            QListWidget::item:selected {{
                background: #313244;
                color: #cdd6f4;
            }}
            QListWidget::item:hover {{
                background: #2a2a3e;
            }}
            QComboBox {{
                background: #181825;
                border: 1px solid #313244;
                border-radius: 4px;
                padding: 4px 8px;
                color: #cdd6f4;
            }}
            QComboBox::drop-down {{
                border: none;
                width: 20px;
            }}
            QComboBox QAbstractItemView {{
                background: #181825;
                border: 1px solid #313244;
                selection-background-color: #313244;
            }}
            QMenu {{
                background: #181825;
                border: 1px solid #313244;
                border-radius: 4px;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 5px 20px 5px 12px;
                border-radius: 3px;
            }}
            QMenu::item:selected {{
                background: #313244;
            }}
            QMenu::separator {{
                height: 1px;
                background: #313244;
                margin: 3px 8px;
            }}
        """)
