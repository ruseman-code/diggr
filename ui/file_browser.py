"""Centre panel: file list with inline rating stars and tag summary."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal, QSortFilterProxyModel, QModelIndex
from PyQt6.QtGui import QStandardItemModel, QStandardItem, QColor, QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTreeView, QLabel,
    QPushButton, QFileDialog, QHeaderView, QAbstractItemView,
    QSizePolicy,
)
import database as db

AUDIO_EXTS = {".mp3", ".wav", ".aiff", ".aif", ".flac", ".ogg"}

# Column indices
COL_NAME   = 0
COL_RATING = 1
COL_TAGS   = 2
COL_PATH   = 3  # hidden, used for data access


class FileBrowser(QWidget):
    file_selected   = pyqtSignal(str)   # emitted when user clicks a row
    file_activated  = pyqtSignal(str)   # emitted on double-click / Enter (play)
    folder_changed  = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._folder: str = ""
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
        export_btn.setObjectName("export_btn")
        export_btn.clicked.connect(self._export)

        top.addWidget(self.folder_label)
        top.addWidget(browse_btn)
        top.addWidget(export_btn)
        root.addLayout(top)

        # Count label
        self.count_label = QLabel("")
        self.count_label.setStyleSheet("color: #777; font-size: 11px;")
        root.addWidget(self.count_label)

        # Tree view
        self._model = QStandardItemModel()
        self._model.setHorizontalHeaderLabels(["Filename", "★", "Tags", "Path"])

        self._proxy = QSortFilterProxyModel()
        self._proxy.setSourceModel(self._model)
        self._proxy.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

        self.tree = QTreeView()
        self.tree.setModel(self._proxy)
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setSortingEnabled(True)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.doubleClicked.connect(self._on_double_click)
        self.tree.selectionModel().currentChanged.connect(self._on_selection_changed)

        hdr = self.tree.header()
        hdr.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(COL_RATING, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(COL_TAGS, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setColumnWidth(COL_RATING, 40)
        self.tree.setColumnHidden(COL_PATH, True)

        root.addWidget(self.tree, stretch=1)

    # ── Folder loading ────────────────────────────────────────────────────

    def _choose_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select sample folder", str(Path.home())
        )
        if folder:
            self.load_folder(folder)

    def load_folder(self, folder: str):
        self._folder = folder
        self.folder_label.setText(folder)
        self.folder_changed.emit(folder)
        self.refresh(name_query="", tags=[], min_rating=0)

    # ── Refresh (apply filters) ────────────────────────────────────────────

    def refresh(
        self,
        name_query: str = "",
        tags: Optional[list] = None,
        min_rating: int = 0,
    ):
        if not self._folder:
            return

        # Ensure every audio file in the folder exists in the DB
        self._scan_folder()

        rows = db.search_samples(
            self._folder,
            name_query=name_query,
            tags=tags or [],
            min_rating=min_rating,
        )

        self._model.removeRows(0, self._model.rowCount())

        for row in rows:
            path = row["path"]
            name = os.path.basename(path)
            rating_str = "★" * row["rating"] if row["rating"] else ""
            tags_str = row["tag_list"] or ""

            name_item   = QStandardItem(name)
            rating_item = QStandardItem(rating_str)
            tags_item   = QStandardItem(tags_str)
            path_item   = QStandardItem(path)

            rating_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            rating_item.setForeground(QColor("#f0c040"))

            self._model.appendRow([name_item, rating_item, tags_item, path_item])

        n = self._model.rowCount()
        self.count_label.setText(f"{n} file{'s' if n != 1 else ''}")

    def _scan_folder(self):
        """Walk folder and register any audio files not yet in the DB."""
        for root, _, files in os.walk(self._folder):
            for f in files:
                if Path(f).suffix.lower() in AUDIO_EXTS:
                    full = os.path.join(root, f)
                    db.upsert_sample(full)

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
        path_item = self._model.item(src.row(), COL_PATH)
        return path_item.text() if path_item else ""

    # ── Selected paths (for export) ────────────────────────────────────────

    def selected_paths(self) -> list[str]:
        paths = []
        for idx in self.tree.selectionModel().selectedRows():
            p = self._path_for_index(idx)
            if p:
                paths.append(p)
        return paths

    def all_visible_paths(self) -> list[str]:
        paths = []
        for row in range(self._model.rowCount()):
            item = self._model.item(row, COL_PATH)
            if item:
                paths.append(item.text())
        return paths

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

    # ── Public refresh trigger ─────────────────────────────────────────────

    def apply_filters(self, name_query: str, tags: list[str], min_rating: int):
        self.refresh(name_query=name_query, tags=tags, min_rating=min_rating)

    @property
    def current_folder(self) -> str:
        return self._folder
