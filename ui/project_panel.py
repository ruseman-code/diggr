"""Left sidebar: project list with create / rename / delete."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QGroupBox, QInputDialog, QMessageBox, QLabel,
)
import activity_log
import database as db

_LIBRARY_ID = -1   # sentinel: "show all samples, no project filter"


class ProjectPanel(QWidget):
    """Emits project_changed(-1) for Library, project_changed(id) for a project."""

    project_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self.refresh()

    # ── Build ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 4)
        root.setSpacing(6)

        grp = QGroupBox("Projects")
        gl = QVBoxLayout(grp)
        gl.setSpacing(4)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.currentItemChanged.connect(self._on_selection_changed)
        gl.addWidget(self._list)

        # Count label
        self._count_label = QLabel("")
        self._count_label.setStyleSheet("color: #6c7086; font-size: 11px;")
        gl.addWidget(self._count_label)

        # Buttons
        btn_row = QHBoxLayout()

        self._new_btn = QPushButton("+")
        self._new_btn.setFixedWidth(28)
        self._new_btn.setToolTip("New project")
        self._new_btn.clicked.connect(self._on_new)

        self._rename_btn = QPushButton("✎")
        self._rename_btn.setFixedWidth(28)
        self._rename_btn.setToolTip("Rename project")
        self._rename_btn.clicked.connect(self._on_rename)
        self._rename_btn.setEnabled(False)

        self._del_btn = QPushButton("🗑")
        self._del_btn.setFixedWidth(28)
        self._del_btn.setToolTip("Delete project")
        self._del_btn.clicked.connect(self._on_delete)
        self._del_btn.setEnabled(False)

        btn_row.addWidget(self._new_btn)
        btn_row.addWidget(self._rename_btn)
        btn_row.addWidget(self._del_btn)
        btn_row.addStretch()
        gl.addLayout(btn_row)

        root.addWidget(grp)

    # ── Data ──────────────────────────────────────────────────────────────

    def refresh(self):
        """Reload the project list from the database."""
        current_id = self.current_project_id()

        self._list.blockSignals(True)
        self._list.clear()

        # Library row
        lib_item = QListWidgetItem("Library (all samples)")
        lib_item.setData(Qt.ItemDataRole.UserRole, _LIBRARY_ID)
        f = lib_item.font()
        f.setItalic(True)
        lib_item.setFont(f)
        self._list.addItem(lib_item)

        projects = db.get_projects()
        for proj in projects:
            count = db.project_sample_count(proj["id"])
            item = QListWidgetItem(f"{proj['name']}  ({count})")
            item.setData(Qt.ItemDataRole.UserRole, proj["id"])
            item.setData(Qt.ItemDataRole.UserRole + 1, proj["name"])  # raw name
            self._list.addItem(item)

        # Restore selection
        self._list.blockSignals(False)
        self._restore_selection(current_id)
        self._update_buttons()

    def _restore_selection(self, project_id: int):
        for i in range(self._list.count()):
            if self._list.item(i).data(Qt.ItemDataRole.UserRole) == project_id:
                self._list.setCurrentRow(i)
                return
        self._list.setCurrentRow(0)   # fall back to Library

    def _update_buttons(self):
        is_project = self.current_project_id() != _LIBRARY_ID
        self._rename_btn.setEnabled(is_project)
        self._del_btn.setEnabled(is_project)

    # ── Slots ──────────────────────────────────────────────────────────────

    def _on_selection_changed(self, current, _prev):
        if current is None:
            return
        pid = current.data(Qt.ItemDataRole.UserRole)
        self._update_buttons()
        self.project_changed.emit(pid)

    def _on_new(self):
        name, ok = QInputDialog.getText(self, "New project", "Project name:")
        name = name.strip()
        if not ok or not name:
            return
        db.create_project(name)
        activity_log.log(f"Project created: '{name}'")
        self.refresh()
        # Select the new project
        for i in range(self._list.count()):
            raw = self._list.item(i).data(Qt.ItemDataRole.UserRole + 1)
            if raw == name:
                self._list.setCurrentRow(i)
                break

    def _on_rename(self):
        pid = self.current_project_id()
        if pid == _LIBRARY_ID:
            return
        old_name = self._list.currentItem().data(Qt.ItemDataRole.UserRole + 1)
        name, ok = QInputDialog.getText(
            self, "Rename project", "New name:", text=old_name
        )
        name = name.strip()
        if not ok or not name or name == old_name:
            return
        db.rename_project(pid, name)
        activity_log.log(f"Project renamed: '{old_name}' → '{name}'")
        self.refresh()

    def _on_delete(self):
        pid = self.current_project_id()
        if pid == _LIBRARY_ID:
            return
        name = self._list.currentItem().data(Qt.ItemDataRole.UserRole + 1)
        count = db.project_sample_count(pid)
        plural = "s" if count != 1 else ""
        reply = QMessageBox.question(
            self,
            "Delete project",
            f'Delete project "{name}" ({count} sample{plural})?\n'
            "Samples will remain in the library.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            db.delete_project(pid)
            activity_log.log(f"Project deleted: '{name}'")
            self.refresh()   # selection falls back to Library, which emits signal

    # ── Public API ─────────────────────────────────────────────────────────

    def current_project_id(self) -> int:
        item = self._list.currentItem()
        if item is None:
            return _LIBRARY_ID
        return item.data(Qt.ItemDataRole.UserRole)

    def select_library(self):
        self._list.setCurrentRow(0)
