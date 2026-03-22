"""Smart Organise – step 2: before/after preview, backup, and apply.

Shows all proposed operations in a two-column table with checkboxes.
The user deselects anything they don't want, picks a backup folder, and
clicks Apply.  Backup is created first, then files are moved/renamed and
the database is updated.
"""
from __future__ import annotations

import os
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

import activity_log
import config
import database as db

# Column indices
_COL_FROM = 0
_COL_TO   = 1


class SmartOrganisePreviewDialog(QDialog):
    """Preview proposed operations, back up affected files, then apply."""

    # Worker → UI thread signals
    _progress_sig = pyqtSignal(int, str)
    _done_sig     = pyqtSignal(int, int, object)   # backed_up, applied, errors

    apply_complete = pyqtSignal()   # emitted after a successful apply

    def __init__(self, result_data: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Smart Organise – Preview")
        self.setMinimumSize(920, 640)
        self.resize(1100, 720)

        self._lib_root   = config.get_library_root()
        self._all_ops    = result_data.get("operations", [])
        self._folders    = result_data.get("folders", [])
        self._conventions = result_data.get("conventions")

        self._ops = self._validate_ops(self._all_ops)

        self._build_ui()
        self._populate_table()
        self._update_apply_btn()

        self._progress_sig.connect(self._on_progress)
        self._done_sig.connect(self._on_done)

    # ── Validation ────────────────────────────────────────────────────────

    def _validate_ops(self, ops: list) -> list:
        """Filter out malformed or no-op operations.

        Also strips the library root from any paths Claude accidentally
        returned as absolute.
        """
        lib_root = self._lib_root
        valid = []
        for op in ops:
            frm = op.get("from", "").strip()
            to  = op.get("to",   "").strip()
            if not frm or not to or frm == to:
                continue

            # Claude sometimes includes the library root — strip it
            for attr_name, val in (("from", frm), ("to", to)):
                p = Path(val)
                if p.is_absolute() and lib_root:
                    try:
                        val = str(p.relative_to(lib_root))
                        if attr_name == "from":
                            frm = val
                        else:
                            to = val
                    except ValueError:
                        frm = None   # marks as invalid
                        break
                elif p.is_absolute():
                    frm = None
                    break

            if frm is None or frm == to:
                continue
            valid.append({"from": frm, "to": to})
        return valid

    # ── Build ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(12, 12, 12, 12)

        # Header row
        n_ops    = len(self._ops)
        n_skip   = len(self._all_ops) - n_ops
        n_fold   = len(self._folders)

        parts = [f"<b>{n_ops}</b> file operation{'s' if n_ops != 1 else ''}"]
        if n_fold:
            parts.append(
                f"<b>{n_fold}</b> folder{'s' if n_fold != 1 else ''} to create"
            )
        if n_skip:
            parts.append(
                f"<span style='color:#f38ba8'>"
                f"{n_skip} skipped (invalid or unchanged)</span>"
            )
        hdr = QLabel("  •  ".join(parts))
        hdr.setStyleSheet("font-size: 13px;")
        root.addWidget(hdr)

        # Toolbar (select all / filter)
        tb = QHBoxLayout()
        sel_btn   = QPushButton("Select all")
        desel_btn = QPushButton("Deselect all")
        sel_btn.clicked.connect(self._select_all)
        desel_btn.clicked.connect(self._deselect_all)
        tb.addWidget(sel_btn)
        tb.addWidget(desel_btn)
        tb.addStretch()
        tb.addWidget(QLabel("Show:"))
        self._filter_combo = QComboBox()
        self._filter_combo.addItems(["All operations", "Renames only", "Moves only"])
        self._filter_combo.currentIndexChanged.connect(
            lambda _: self._populate_table(self._filter_combo.currentText())
        )
        tb.addWidget(self._filter_combo)
        self._sel_label = QLabel("")
        self._sel_label.setStyleSheet("color: #888; font-size: 11px; margin-left: 8px;")
        tb.addWidget(self._sel_label)
        root.addLayout(tb)

        # Two-column table
        self._table = QTableWidget()
        self._table.setColumnCount(2)
        self._table.setHorizontalHeaderLabels(["Current path", "Proposed path"])
        hdr_view = self._table.horizontalHeader()
        hdr_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.itemChanged.connect(self._on_item_changed)
        root.addWidget(self._table, stretch=1)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep)

        # Backup destination
        backup_lbl = QLabel(
            "<b>Backup destination</b>  "
            "(a timestamped copy of all affected files will be made "
            "here before anything is moved)"
        )
        backup_lbl.setWordWrap(True)
        root.addWidget(backup_lbl)

        backup_row = QHBoxLayout()
        self._backup_edit = QLineEdit()
        self._backup_edit.setPlaceholderText("Choose a folder for the backup…")
        self._backup_edit.textChanged.connect(self._update_apply_btn)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_backup)
        backup_row.addWidget(self._backup_edit)
        backup_row.addWidget(browse_btn)
        root.addLayout(backup_row)

        # Progress
        self._progress = QProgressBar()
        self._progress.hide()
        root.addWidget(self._progress)
        self._progress_label = QLabel("")
        self._progress_label.setStyleSheet("color: #888; font-size: 11px;")
        self._progress_label.hide()
        root.addWidget(self._progress_label)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep2)

        # Buttons
        btn_hl = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        self._apply_btn = QPushButton("Create backup & apply…")
        self._apply_btn.setDefault(True)
        self._apply_btn.clicked.connect(self._on_apply)
        btn_hl.addStretch()
        btn_hl.addWidget(cancel_btn)
        btn_hl.addWidget(self._apply_btn)
        root.addLayout(btn_hl)

    # ── Table population ──────────────────────────────────────────────────

    def _populate_table(self, filter_mode: str = "All operations"):
        self._table.itemChanged.disconnect(self._on_item_changed)
        self._table.setRowCount(0)

        for op in self._ops:
            frm, to = op["from"], op["to"]

            is_rename = Path(frm).parent == Path(to).parent
            if filter_mode == "Renames only" and not is_rename:
                continue
            if filter_mode == "Moves only" and is_rename:
                continue

            row = self._table.rowCount()
            self._table.insertRow(row)

            from_item = QTableWidgetItem(frm)
            from_item.setCheckState(Qt.CheckState.Checked)
            from_item.setData(Qt.ItemDataRole.UserRole, op)
            from_item.setToolTip(frm)
            self._table.setItem(row, _COL_FROM, from_item)

            to_item = QTableWidgetItem(to)
            to_item.setForeground(QBrush(QColor("#a6e3a1")))
            to_item.setToolTip(to)
            self._table.setItem(row, _COL_TO, to_item)

        self._table.itemChanged.connect(self._on_item_changed)
        self._update_sel_label()

    def _select_all(self):
        self._table.itemChanged.disconnect(self._on_item_changed)
        for row in range(self._table.rowCount()):
            it = self._table.item(row, _COL_FROM)
            if it:
                it.setCheckState(Qt.CheckState.Checked)
        self._table.itemChanged.connect(self._on_item_changed)
        self._update_sel_label()
        self._update_apply_btn()

    def _deselect_all(self):
        self._table.itemChanged.disconnect(self._on_item_changed)
        for row in range(self._table.rowCount()):
            it = self._table.item(row, _COL_FROM)
            if it:
                it.setCheckState(Qt.CheckState.Unchecked)
        self._table.itemChanged.connect(self._on_item_changed)
        self._update_sel_label()
        self._update_apply_btn()

    def _on_item_changed(self, item):
        if item.column() == _COL_FROM:
            self._update_sel_label()
            self._update_apply_btn()

    def _selected_ops(self) -> list:
        ops = []
        for row in range(self._table.rowCount()):
            it = self._table.item(row, _COL_FROM)
            if it and it.checkState() == Qt.CheckState.Checked:
                ops.append(it.data(Qt.ItemDataRole.UserRole))
        return ops

    def _update_sel_label(self):
        total = self._table.rowCount()
        checked = sum(
            1 for row in range(total)
            if (it := self._table.item(row, _COL_FROM))
            and it.checkState() == Qt.CheckState.Checked
        )
        self._sel_label.setText(f"{checked} / {total} selected")

    def _update_apply_btn(self):
        ops        = self._selected_ops()
        has_ops    = bool(ops)
        has_backup = bool(self._backup_edit.text().strip())
        self._apply_btn.setEnabled(has_ops and has_backup)
        n = len(ops)
        self._apply_btn.setText(
            f"Create backup & apply {n} operation{'s' if n != 1 else ''}…"
        )

    # ── Backup browse ─────────────────────────────────────────────────────

    def _browse_backup(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Choose backup destination", str(Path.home())
        )
        if folder:
            self._backup_edit.setText(folder)

    # ── Apply ─────────────────────────────────────────────────────────────

    def _on_apply(self):
        ops = self._selected_ops()
        if not ops:
            QMessageBox.warning(self, "Nothing selected", "No operations are selected.")
            return

        backup_base = self._backup_edit.text().strip()
        if not backup_base:
            QMessageBox.warning(self, "No backup folder", "Please choose a backup destination.")
            return

        n = len(ops)
        reply = QMessageBox.question(
            self,
            "Confirm Smart Organise",
            f"This will:\n\n"
            f"  1.  Back up {n} file{'s' if n != 1 else ''} to:\n"
            f"      {backup_base}\n\n"
            f"  2.  Move / rename {n} file{'s' if n != 1 else ''} in your library\n\n"
            f"  3.  Update all affected paths in the database\n\n"
            f"The backup preserves the originals.\n\n"
            f"Proceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._apply_btn.setEnabled(False)
        self._progress.setRange(0, len(ops) * 2)
        self._progress.setValue(0)
        self._progress.show()
        self._progress_label.show()

        threading.Thread(
            target=self._apply_worker,
            args=(ops, backup_base),
            daemon=True,
        ).start()

    def _apply_worker(self, ops: list, backup_base: str):
        lib_root  = self._lib_root
        ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_root = Path(backup_base) / f"diggr_backup_{ts}"
        errors    = []
        backed_up = 0
        applied   = 0
        n         = len(ops)

        # ── Step 1: Backup ────────────────────────────────────────────────
        for i, op in enumerate(ops):
            src_abs = str(Path(lib_root) / op["from"]) if lib_root else op["from"]
            src = Path(src_abs)
            if src.exists():
                dest = backup_root / op["from"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(src, dest)
                    backed_up += 1
                except OSError as exc:
                    errors.append(f"Backup failed: {op['from']}: {exc}")
            self._progress_sig.emit(i + 1, f"Backing up… {i + 1}/{n}")

        # ── Step 2: Move + rename + update DB ────────────────────────────
        for i, op in enumerate(ops):
            src_abs = str(Path(lib_root) / op["from"]) if lib_root else op["from"]
            dst_abs = str(Path(lib_root) / op["to"])   if lib_root else op["to"]
            src = Path(src_abs)

            if not src.exists():
                errors.append(f"Source missing, skipped: {op['from']}")
                self._progress_sig.emit(n + i + 1, f"Applying… {i + 1}/{n}")
                continue

            try:
                Path(dst_abs).parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), dst_abs)
                db.rename_sample_path(src_abs, dst_abs)
                applied += 1
            except OSError as exc:
                errors.append(f"Move failed: {op['from']} → {op['to']}: {exc}")

            self._progress_sig.emit(n + i + 1, f"Applying… {i + 1}/{n}")

        # ── Save conventions if this was a successful run ─────────────────
        if self._conventions and applied > 0:
            config.set_smart_organise_conventions(self._conventions)

        self._done_sig.emit(backed_up, applied, errors)

    # ── Worker callbacks (main thread) ────────────────────────────────────

    def _on_progress(self, val: int, msg: str):
        self._progress.setValue(val)
        self._progress_label.setText(msg)

    def _on_done(self, backed_up: int, applied: int, errors: object):
        self._progress.hide()
        self._progress_label.hide()

        activity_log.log(
            f"Smart Organise applied: {applied} file{'s' if applied != 1 else ''} "
            f"moved/renamed, {backed_up} backed up"
            + (f", {len(errors)} error(s)" if errors else "")
        )

        if errors:
            err_text = "\n".join(errors[:12])
            if len(errors) > 12:
                err_text += f"\n…and {len(errors) - 12} more"
            QMessageBox.warning(
                self,
                "Completed with errors",
                f"Applied {applied} operation(s) with {len(errors)} error(s):\n\n"
                f"{err_text}",
            )
        else:
            QMessageBox.information(
                self,
                "Smart Organise complete",
                f"Successfully moved/renamed {applied} "
                f"file{'s' if applied != 1 else ''}.\n\n"
                f"Backup created at:\n{self._backup_edit.text().strip()}",
            )

        self.apply_complete.emit()
        self.accept()
