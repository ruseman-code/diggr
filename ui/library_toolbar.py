"""Toolbar for creating, switching, renaming and deleting named libraries."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QToolBar, QLabel, QComboBox, QPushButton, QInputDialog, QMessageBox,
    QWidget, QHBoxLayout, QFrame,
)

import config


class LibraryToolbar(QToolBar):
    """Top toolbar that lets the user manage and switch between libraries.

    Emits *library_switched(lib_id)* whenever the active library should
    change — including after New and Delete.  The main window connects this
    signal and performs the full switch (DB swap + file browser reload).
    """

    library_switched = pyqtSignal(str)   # lib_id
    health_check_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    activity_log_requested = pyqtSignal()
    smart_organise_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("Library", parent)
        self.setMovable(False)
        self._build()
        self.refresh()

    # ── Build ─────────────────────────────────────────────────────────────

    def _build(self):
        # Group everything in a plain widget so spacing is consistent
        container = QWidget()
        hl = QHBoxLayout(container)
        hl.setContentsMargins(6, 2, 6, 2)
        hl.setSpacing(6)

        hl.addWidget(QLabel("Library:"))

        self._combo = QComboBox()
        self._combo.setMinimumWidth(180)
        self._combo.setToolTip("Switch active library")
        self._combo.currentIndexChanged.connect(self._on_combo_changed)
        hl.addWidget(self._combo)

        self._new_btn = QPushButton("+")
        self._new_btn.setFixedWidth(28)
        self._new_btn.setToolTip("New library")
        self._new_btn.clicked.connect(self._on_new)
        hl.addWidget(self._new_btn)

        self._rename_btn = QPushButton("✎")
        self._rename_btn.setFixedWidth(28)
        self._rename_btn.setToolTip("Rename library")
        self._rename_btn.clicked.connect(self._on_rename)
        hl.addWidget(self._rename_btn)

        self._del_btn = QPushButton("🗑")
        self._del_btn.setFixedWidth(28)
        self._del_btn.setToolTip("Delete library")
        self._del_btn.clicked.connect(self._on_delete)
        hl.addWidget(self._del_btn)

        # Visual separator then health-check button
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color: #1e1e30;")
        sep.setFixedWidth(1)
        hl.addWidget(sep)

        self._health_btn = QPushButton("Health Check")
        self._health_btn.setToolTip("Scan the active library for issues")
        self._health_btn.clicked.connect(self.health_check_requested)
        hl.addWidget(self._health_btn)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setStyleSheet("color: #1e1e30;")
        sep2.setFixedWidth(1)
        hl.addWidget(sep2)

        self._settings_btn = QPushButton("⚙ Settings")
        self._settings_btn.setToolTip("Open settings")
        self._settings_btn.clicked.connect(self.settings_requested)
        hl.addWidget(self._settings_btn)

        sep_so = QFrame()
        sep_so.setFrameShape(QFrame.Shape.VLine)
        sep_so.setStyleSheet("color: #1e1e30;")
        sep_so.setFixedWidth(1)
        hl.addWidget(sep_so)

        self._smart_btn = QPushButton("✦ Smart Organise")
        self._smart_btn.setStyleSheet(
            "background: #1e1040; color: #9c6ce0; border: 1px solid #4a2a88;"
            "border-radius: 4px; padding: 4px 10px;"
        )
        self._smart_btn.setToolTip("Use Claude AI to suggest a reorganised folder structure and file renames")
        self._smart_btn.clicked.connect(self.smart_organise_requested)
        hl.addWidget(self._smart_btn)

        sep3 = QFrame()
        sep3.setFrameShape(QFrame.Shape.VLine)
        sep3.setStyleSheet("color: #1e1e30;")
        sep3.setFixedWidth(1)
        hl.addWidget(sep3)

        self._log_btn = QPushButton("Activity Log")
        self._log_btn.setToolTip("View the activity log")
        self._log_btn.clicked.connect(self.activity_log_requested)
        hl.addWidget(self._log_btn)

        self.addWidget(container)

    # ── Data ──────────────────────────────────────────────────────────────

    def refresh(self):
        """Rebuild the combo from config without triggering a switch signal."""
        self._combo.blockSignals(True)
        self._combo.clear()
        libs = config.get_libraries()
        active_id = config.get_active_library_id()
        for lib_id, lib in sorted(libs.items(), key=lambda kv: kv[1]["name"].lower()):
            self._combo.addItem(lib["name"], lib_id)
            if lib_id == active_id:
                self._combo.setCurrentIndex(self._combo.count() - 1)
        self._combo.blockSignals(False)
        self._update_buttons()

    def _update_buttons(self):
        can_delete = self._combo.count() > 1
        self._del_btn.setEnabled(can_delete)

    def _current_lib_id(self) -> str:
        return self._combo.currentData() or ""

    # ── Slots ─────────────────────────────────────────────────────────────

    def _on_combo_changed(self, index: int):
        lib_id = self._combo.itemData(index)
        if lib_id and lib_id != config.get_active_library_id():
            self.library_switched.emit(lib_id)

    def _on_new(self):
        name, ok = QInputDialog.getText(self, "New library", "Library name:")
        name = name.strip()
        if not ok or not name:
            return
        lib_id = config.create_library(name)
        self.library_switched.emit(lib_id)

    def _on_rename(self):
        lib_id = self._current_lib_id()
        if not lib_id:
            return
        lib = config.get_library(lib_id)
        old_name = lib["name"] if lib else ""
        name, ok = QInputDialog.getText(
            self, "Rename library", "New name:", text=old_name
        )
        name = name.strip()
        if not ok or not name or name == old_name:
            return
        config.rename_library(lib_id, name)
        self.refresh()

    def _on_delete(self):
        if self._combo.count() <= 1:
            QMessageBox.information(
                self, "Delete library", "Cannot delete the only library."
            )
            return
        lib_id = self._current_lib_id()
        lib = config.get_library(lib_id)
        lib_name = lib["name"] if lib else lib_id
        reply = QMessageBox.question(
            self,
            "Delete library",
            f'Delete library "{lib_name}"?\n'
            f"The database file will be permanently removed.\n"
            f"Sample files on disk are not affected.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Delete the DB file
        db_path = config.db_path_for_library(lib_id)
        if db_path.exists():
            db_path.unlink()
        config.delete_library(lib_id)

        # Switch to the first remaining library (sorted by name)
        remaining = sorted(
            config.get_libraries().items(), key=lambda kv: kv[1]["name"].lower()
        )
        if remaining:
            self.library_switched.emit(remaining[0][0])
