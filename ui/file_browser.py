"""Centre panel: file list with rating, BPM, and tag columns."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, List

from PyQt6.QtCore import Qt, pyqtSignal, QSortFilterProxyModel, QModelIndex
from PyQt6.QtGui import QStandardItemModel, QStandardItem, QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTreeView, QLabel,
    QPushButton, QFileDialog, QHeaderView, QAbstractItemView,
    QSizePolicy, QMenu, QInputDialog,
)
from PyQt6.QtGui import QAction
import activity_log
import config
import database as db

AUDIO_EXTS = {".mp3", ".wav", ".aiff", ".aif", ".flac", ".ogg"}


def _common_ancestor(path_a: str, path_b: str) -> str:
    """Return the deepest common ancestor directory of two paths."""
    parts_a = Path(path_a).parts
    parts_b = Path(path_b).parts
    common = []
    for a, b in zip(parts_a, parts_b):
        if a == b:
            common.append(a)
        else:
            break
    return str(Path(*common)) if common else str(Path(path_a).anchor)


# Column indices
COL_NAME   = 0
COL_RATING = 1
COL_BPM    = 2
COL_KEY    = 3
COL_TAGS   = 4
COL_PATH   = 5  # hidden

# ItemDataRoles for numeric sorting
_RATING_ROLE = Qt.ItemDataRole.UserRole
_BPM_ROLE    = Qt.ItemDataRole.UserRole + 1


class _SampleSortProxy(QSortFilterProxyModel):
    """Sorts rating and BPM columns numerically."""

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        col = left.column()
        if col == COL_RATING:
            lv = self.sourceModel().itemFromIndex(left).data(_RATING_ROLE) or 0
            rv = self.sourceModel().itemFromIndex(right).data(_RATING_ROLE) or 0
            return lv < rv
        if col == COL_BPM:
            lv = self.sourceModel().itemFromIndex(left).data(_BPM_ROLE) or 0.0
            rv = self.sourceModel().itemFromIndex(right).data(_BPM_ROLE) or 0.0
            return lv < rv
        return super().lessThan(left, right)


class FileBrowser(QWidget):
    file_selected     = pyqtSignal(str)
    file_activated    = pyqtSignal(str)
    folder_changed    = pyqtSignal(str)
    scan_complete     = pyqtSignal(list)
    projects_modified = pyqtSignal()    # membership changed; project panel should refresh

    def __init__(self, parent=None):
        super().__init__(parent)
        self._folder: str = ""
        self._current_project_id: Optional[int] = None   # None = Library
        self._sort_col   = COL_NAME
        self._sort_order = Qt.SortOrder.AscendingOrder
        self._build_ui()

    # ── Build ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        # Top bar
        top = QHBoxLayout()
        self.folder_label = QLabel("No folder loaded")
        self.folder_label.setStyleSheet("color: #aaa; font-size: 12px;")
        self.folder_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        browse_btn = QPushButton("Open folder…")
        browse_btn.clicked.connect(self._choose_folder)
        export_btn = QPushButton("Export shortlist…")
        export_btn.clicked.connect(self._export)
        top.addWidget(self.folder_label)
        top.addWidget(browse_btn)
        top.addWidget(export_btn)
        root.addLayout(top)

        # Count label
        self.count_label = QLabel("")
        self.count_label.setStyleSheet("color: #777; font-size: 11px;")
        root.addWidget(self.count_label)

        # Model + proxy
        self._model = QStandardItemModel()
        self._model.setHorizontalHeaderLabels(
            ["Filename", "Rating", "BPM", "Key", "Tags", "Path"]
        )

        self._proxy = _SampleSortProxy()
        self._proxy.setSourceModel(self._model)
        self._proxy.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

        # Tree view
        self.tree = QTreeView()
        self.tree.setModel(self._proxy)
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setSortingEnabled(True)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.doubleClicked.connect(self._on_double_click)
        self.tree.selectionModel().currentChanged.connect(self._on_selection_changed)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)

        hdr = self.tree.header()
        hdr.setSectionResizeMode(COL_NAME,   QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(COL_RATING, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(COL_BPM,    QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(COL_KEY,    QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(COL_TAGS,   QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSortIndicatorShown(True)
        hdr.sortIndicatorChanged.connect(self._on_sort_changed)
        self.tree.setColumnWidth(COL_RATING, 68)
        self.tree.setColumnWidth(COL_BPM,    62)
        self.tree.setColumnWidth(COL_KEY,    80)
        self.tree.setColumnHidden(COL_PATH, True)
        self.tree.sortByColumn(COL_NAME, Qt.SortOrder.AscendingOrder)

        root.addWidget(self.tree, stretch=1)

    # ── Folder loading ────────────────────────────────────────────────────

    def _choose_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select sample folder", str(Path.home())
        )
        if folder:
            self.load_folder(folder)

    def load_folder(self, folder: str):
        """Load *folder*, updating the library root if necessary."""
        folder = str(Path(folder))  # normalise separators / trailing slashes
        library_root = config.get_library_root()

        if library_root:
            try:
                Path(folder).relative_to(library_root)
                # folder is inside the current library root — no change needed
            except ValueError:
                # folder is outside the current root; compute common ancestor
                new_root = _common_ancestor(folder, library_root)
                from PyQt6.QtWidgets import QMessageBox
                reply = QMessageBox.question(
                    self,
                    "Update library root?",
                    f"The selected folder is outside your current library root:\n"
                    f"  {library_root}\n\n"
                    f"Update the library root to:\n"
                    f"  {new_root}\n\n"
                    f"Existing sample paths will be re-indexed automatically.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                db.change_library_root(library_root, new_root)
                config.set_library_root(new_root)
        else:
            # First time — set the library root to the chosen folder
            config.set_library_root(folder)

        self._folder = folder
        self.folder_label.setText(folder)
        self.folder_changed.emit(folder)
        all_paths = self._scan_folder()
        self.scan_complete.emit(all_paths)
        self.refresh()

    # ── Scan (registers files in DB, returns all paths) ───────────────────

    def _scan_folder(self) -> List[str]:
        paths: List[str] = []
        for root_dir, _, files in os.walk(self._folder):
            for f in files:
                if Path(f).suffix.lower() in AUDIO_EXTS:
                    full = os.path.join(root_dir, f)
                    db.upsert_sample(full)
                    paths.append(full)
        return paths

    # ── Refresh (query DB, repopulate model) ──────────────────────────────

    def set_project(self, project_id: Optional[int]):
        """Switch the active project filter and refresh the list."""
        self._current_project_id = project_id
        self.refresh()

    def refresh(
        self,
        name_query: str = "",
        tags: Optional[list] = None,
        min_rating: int = 0,
        min_bpm: int = 0,
        max_bpm: int = 0,
        key_filter: str = "",
        smart_query: str = "",
    ):
        # In project mode a folder isn't required
        if not self._folder and self._current_project_id is None:
            return

        rows = db.search_samples(
            folder=self._folder,
            name_query=name_query,
            tags=tags or [],
            min_rating=min_rating,
            min_bpm=min_bpm,
            max_bpm=max_bpm,
            key_filter=key_filter,
            project_id=self._current_project_id,
            smart_query=smart_query,
        )

        self._model.removeRows(0, self._model.rowCount())

        for row in rows:
            path     = row["path"]
            name     = os.path.basename(path)
            rating   = row["rating"] or 0
            bpm_val  = row["bpm"]   # float or None
            key_val  = row["key"]   # str  or None
            tags_str = row["tag_list"] or ""

            name_item   = QStandardItem(name)
            rating_item = QStandardItem("★" * rating if rating else "")
            bpm_item    = QStandardItem(f"{bpm_val:.1f}" if bpm_val else "–")
            key_item    = QStandardItem(key_val or "–")
            tags_item   = QStandardItem(tags_str)
            path_item   = QStandardItem(path)

            rating_item.setData(rating,      _RATING_ROLE)
            rating_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            rating_item.setForeground(QColor("#f0c040"))

            bpm_item.setData(bpm_val or 0.0, _BPM_ROLE)
            bpm_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            bpm_item.setForeground(
                QColor("#89b4fa") if bpm_val else QColor("#45475a")
            )

            key_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            key_item.setForeground(
                QColor("#a6e3a1") if key_val else QColor("#45475a")
            )

            self._model.appendRow(
                [name_item, rating_item, bpm_item, key_item, tags_item, path_item]
            )

        n = self._model.rowCount()
        self.count_label.setText(f"{n} file{'s' if n != 1 else ''}")
        self._proxy.sort(self._sort_col, self._sort_order)

    # ── Live analysis update (called as detection results arrive) ─────────

    def update_file_analysis(self, path: str, bpm: float, key: str):
        """Update BPM and Key cells for *path* in place."""
        for row in range(self._model.rowCount()):
            path_item = self._model.item(row, COL_PATH)
            if not (path_item and path_item.text() == path):
                continue

            bpm_item = self._model.item(row, COL_BPM)
            if bpm_item:
                if bpm > 0:
                    bpm_item.setText(f"{bpm:.1f}")
                    bpm_item.setData(bpm, _BPM_ROLE)
                    bpm_item.setForeground(QColor("#89b4fa"))
                else:
                    bpm_item.setText("–")
                    bpm_item.setData(0.0, _BPM_ROLE)
                    bpm_item.setForeground(QColor("#45475a"))

            key_item = self._model.item(row, COL_KEY)
            if key_item:
                if key:
                    key_item.setText(key)
                    key_item.setForeground(QColor("#a6e3a1"))
                else:
                    key_item.setText("–")
                    key_item.setForeground(QColor("#45475a"))
            break

    # ── Sort state ─────────────────────────────────────────────────────────

    def _on_sort_changed(self, col: int, order: Qt.SortOrder):
        self._sort_col   = col
        self._sort_order = order

    # ── Selection / activation ────────────────────────────────────────────

    def _on_selection_changed(self, current: QModelIndex, _prev: QModelIndex):
        path = self._path_for_index(current)
        if path:
            self.file_selected.emit(path)

    def _on_double_click(self, index: QModelIndex):
        path = self._path_for_index(index)
        if path:
            self.file_activated.emit(path)

    def _path_for_index(self, index: QModelIndex) -> str:
        if not index.isValid():
            return ""
        src = self._proxy.mapToSource(index)
        item = self._model.item(src.row(), COL_PATH)
        return item.text() if item else ""

    # ── Selected / visible paths (for export) ─────────────────────────────

    def selected_paths(self) -> List[str]:
        return [
            p for idx in self.tree.selectionModel().selectedRows()
            if (p := self._path_for_index(idx))
        ]

    def all_visible_paths(self) -> List[str]:
        return [
            self._model.item(row, COL_PATH).text()
            for row in range(self._model.rowCount())
            if self._model.item(row, COL_PATH)
        ]

    # ── Export ─────────────────────────────────────────────────────────────

    def _export(self):
        paths = self.selected_paths() or self.all_visible_paths()
        if not paths:
            return
        dest = QFileDialog.getExistingDirectory(
            self, "Export destination", str(Path.home())
        )
        if not dest:
            return
        copied, skipped = db.export_samples(paths, dest)
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.information(
            self, "Export complete",
            f"Copied {copied} file(s) to:\n{dest}"
            + (f"\n({skipped} skipped – source not found)" if skipped else ""),
        )

    # ── Public filter trigger ──────────────────────────────────────────────

    def apply_filters(
        self,
        name_query: str,
        tags: list,
        min_rating: int,
        min_bpm: int = 0,
        max_bpm: int = 0,
        key_filter: str = "",
        smart_query: str = "",
    ):
        self.refresh(
            name_query=name_query,
            tags=tags,
            min_rating=min_rating,
            min_bpm=min_bpm,
            max_bpm=max_bpm,
            key_filter=key_filter,
            smart_query=smart_query,
        )

    # ── Context menu (right-click) ─────────────────────────────────────────

    def _on_context_menu(self, pos):
        paths = self.selected_paths()
        if not paths:
            return

        menu = QMenu(self)
        projects = db.get_projects()

        # "Add to project" — submenu if projects exist, direct action otherwise
        if projects:
            add_menu = menu.addMenu("Add to project")
            for proj in projects:
                act = QAction(proj["name"], self)
                pid = proj["id"]
                act.triggered.connect(
                    lambda checked, p=pid: self._add_to_project(paths, p)
                )
                add_menu.addAction(act)
            add_menu.addSeparator()
            new_act = QAction("New project…", self)
            new_act.triggered.connect(lambda: self._add_to_new_project(paths))
            add_menu.addAction(new_act)
        else:
            act = QAction("Add to new project…", self)
            act.triggered.connect(lambda: self._add_to_new_project(paths))
            menu.addAction(act)

        # "Remove from project" — only when viewing a specific project
        if self._current_project_id is not None:
            menu.addSeparator()
            rem_act = QAction("Remove from project", self)
            rem_act.triggered.connect(lambda: self._remove_from_project(paths))
            menu.addAction(rem_act)

        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _add_to_project(self, paths: List[str], project_id: int):
        for p in paths:
            db.add_to_project(p, project_id)
        proj = next((pr for pr in db.get_projects() if pr["id"] == project_id), None)
        proj_name = proj["name"] if proj else str(project_id)
        n = len(paths)
        activity_log.log(f"Added {n} file{'s' if n != 1 else ''} to project: {proj_name}")
        self.projects_modified.emit()

    def _add_to_new_project(self, paths: List[str]):
        name, ok = QInputDialog.getText(self, "New project", "Project name:")
        name = name.strip()
        if not ok or not name:
            return
        pid = db.create_project(name)
        for p in paths:
            db.add_to_project(p, pid)
        n = len(paths)
        activity_log.log(f"Project created: '{name}' with {n} file{'s' if n != 1 else ''}")
        self.projects_modified.emit()

    def _remove_from_project(self, paths: List[str]):
        if self._current_project_id is None:
            return
        for p in paths:
            db.remove_from_project(p, self._current_project_id)
        n = len(paths)
        activity_log.log(f"Removed {n} file{'s' if n != 1 else ''} from project")
        self.refresh()
        self.projects_modified.emit()

    def clear_folder(self):
        """Reset to an empty state (used when a newly created library has no folder)."""
        self._folder = ""
        self._current_project_id = None
        self.folder_label.setText("No folder loaded")
        self._model.removeRows(0, self._model.rowCount())
        self.count_label.setText("")

    @property
    def current_folder(self) -> str:
        return self._folder
