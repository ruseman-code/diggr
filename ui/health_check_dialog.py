"""Library Health Check dialogue.

Runs the health report synchronously on open, displays results in collapsible
QGroupBox sections, and offers one-click remediation where possible.

Public state read by the caller after exec():
    files_were_removed  bool  – True if any samples were deleted from the DB
    bpm_paths           list  – non-empty if user requested BPM detection
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QGroupBox, QScrollArea,
    QDialogButtonBox, QFrame, QWidget, QSizePolicy,
)
from PyQt6.QtGui import QFont, QColor

import activity_log
import config
import database as db

_MAX_LIST = 200   # maximum rows shown per section before "…and N more"


class HealthCheckDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Library Health Check")
        self.setMinimumSize(640, 540)
        self.resize(700, 620)

        # Results read by the caller after exec()
        self.files_were_removed: bool = False
        self.bpm_paths: list = []

        self._report: Optional[dict] = None
        self._build_shell()
        self._run_check()

    # ── Shell (permanent widgets) ─────────────────────────────────────────

    def _build_shell(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)

        # Header
        lib = config.get_active_library()
        lib_name = lib["name"] if lib else "Default"
        hdr = QLabel(f"<b>Library:</b> {lib_name}")
        hdr.setStyleSheet("font-size: 13px;")
        root.addWidget(hdr)

        self._summary_label = QLabel("")
        self._summary_label.setStyleSheet("font-size: 12px; padding: 4px 0;")
        self._summary_label.setWordWrap(True)
        root.addWidget(self._summary_label)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep)

        # Scrollable section area
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        self._sections_widget = QWidget()
        self._sections_layout = QVBoxLayout(self._sections_widget)
        self._sections_layout.setSpacing(8)
        self._sections_layout.addStretch()
        self._scroll.setWidget(self._sections_widget)
        root.addWidget(self._scroll, stretch=1)

        # Buttons
        btn_box = QDialogButtonBox()
        self._recheck_btn = btn_box.addButton(
            "Re-run check", QDialogButtonBox.ButtonRole.ResetRole
        )
        self._recheck_btn.clicked.connect(self._run_check)
        btn_box.addButton(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(self.reject)
        root.addWidget(btn_box)

    # ── Run ───────────────────────────────────────────────────────────────

    def _run_check(self):
        self._summary_label.setText("Scanning library…")
        self._clear_sections()
        # Allow the label to paint before we block on I/O
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()

        config.set_last_health_check()
        self._report = db.get_health_report()
        self._populate(self._report)
        r = self._report
        issue_count = (
            len(r["missing"]) + len(r["duplicates"]) + len(r["untagged"])
            + len(r["no_bpm"]) + len(r["no_rating"])
        )
        activity_log.log(
            f"Health check: {r['total']} samples, {issue_count} issue{'s' if issue_count != 1 else ''} "
            f"(missing={len(r['missing'])}, duplicates={len(r['duplicates'])}, "
            f"untagged={len(r['untagged'])}, no_bpm={len(r['no_bpm'])}, "
            f"no_rating={len(r['no_rating'])})"
        )

    def _clear_sections(self):
        layout = self._sections_layout
        while layout.count() > 1:           # keep the trailing stretch
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    # ── Populate ──────────────────────────────────────────────────────────

    def _populate(self, r: dict):
        total = r["total"]
        issue_count = (
            len(r["missing"])
            + len(r["duplicates"])
            + len(r["untagged"])
            + len(r["no_bpm"])
            + len(r["no_rating"])
        )

        if issue_count == 0:
            self._summary_label.setText(
                f"✓  All {total} sample{'s' if total != 1 else ''} are healthy."
            )
            self._summary_label.setStyleSheet(
                "font-size: 12px; color: #a6e3a1; padding: 4px 0;"
            )
            return

        self._summary_label.setStyleSheet("font-size: 12px; padding: 4px 0;")
        self._summary_label.setText(
            f"{total} sample{'s' if total != 1 else ''} scanned — "
            f"<b>{issue_count} issue{'s' if issue_count != 1 else ''} found</b>"
        )

        layout = self._sections_layout

        # Insert sections before the trailing stretch
        def add(widget):
            layout.insertWidget(layout.count() - 1, widget)

        if r["missing"]:
            add(self._section_missing(r["missing"]))

        if r["duplicates"]:
            add(self._section_duplicates(r["duplicates"]))

        if r["untagged"]:
            add(self._section_simple(
                f"Files with no tags  ({len(r['untagged'])})",
                r["untagged"],
                tip="Consider tagging these files so they appear in tag filters.",
            ))

        if r["no_bpm"]:
            add(self._section_no_bpm(r["no_bpm"]))

        if r["no_rating"]:
            add(self._section_simple(
                f"Unrated files  ({len(r['no_rating'])})",
                r["no_rating"],
                tip="Select a file in the browser and click a star to rate it.",
            ))

    # ── Section builders ──────────────────────────────────────────────────

    def _section_missing(self, paths: list) -> QGroupBox:
        n = len(paths)
        grp = QGroupBox(f"Missing files  ({n})")
        vl = QVBoxLayout(grp)
        vl.addWidget(QLabel(
            "These files are tracked in the database but no longer exist on disk."
        ))
        vl.addWidget(_file_list(paths))

        btn = QPushButton(f"Remove {n} missing file{'s' if n != 1 else ''} from database")
        btn.setStyleSheet("color: #f38ba8;")
        btn.setToolTip("Deletes these entries from the library database. "
                       "No files on disk are affected.")

        def do_remove():
            for p in paths:
                db.remove_sample(p)
            self.files_were_removed = True
            activity_log.log(f"Removed {n} missing file{'s' if n != 1 else ''} from database")
            btn.setText("✓  Done — entries removed")
            btn.setEnabled(False)
            grp.setTitle(f"Missing files  (resolved)")

        btn.clicked.connect(do_remove)
        vl.addWidget(btn)
        return grp

    def _section_duplicates(self, groups: list) -> QGroupBox:
        total_files = sum(len(g["paths"]) for g in groups)
        grp = QGroupBox(
            f"Duplicate filenames  ({len(groups)} filename{'s' if len(groups) != 1 else ''}, "
            f"{total_files} files)"
        )
        vl = QVBoxLayout(grp)
        vl.addWidget(QLabel(
            "The same filename appears in multiple locations. "
            "Review and remove duplicates manually if needed."
        ))

        lst = QListWidget()
        lst.setAlternatingRowColors(True)
        lst.setMaximumHeight(180)

        shown = 0
        for group in groups:
            if shown >= _MAX_LIST:
                break
            # Header row for this filename
            header = QListWidgetItem(f"  {group['filename']}  ({len(group['paths'])} copies)")
            f = header.font()
            f.setBold(True)
            header.setFont(f)
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            lst.addItem(header)
            shown += 1

            for p in group["paths"]:
                if shown >= _MAX_LIST:
                    break
                item = QListWidgetItem(f"      {p}")
                item.setToolTip(p)
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                lst.addItem(item)
                shown += 1

        if total_files > _MAX_LIST:
            lst.addItem(f"  … and {total_files - _MAX_LIST} more files not shown")

        vl.addWidget(lst)
        return grp

    def _section_no_bpm(self, paths: list) -> QGroupBox:
        n = len(paths)
        grp = QGroupBox(f"Files with no BPM data  ({n})")
        vl = QVBoxLayout(grp)
        vl.addWidget(QLabel("BPM has not been detected for these files."))
        vl.addWidget(_file_list(paths))

        btn = QPushButton(f"Run BPM detection on {n} file{'s' if n != 1 else ''}")
        btn.setToolTip("Analyses these files in the background — same as loading the folder.")

        def do_bpm():
            self.bpm_paths = list(paths)
            activity_log.log(f"BPM detection requested for {n} file{'s' if n != 1 else ''} (from health check)")
            btn.setText("✓  BPM detection will start when this dialog closes")
            btn.setEnabled(False)

        btn.clicked.connect(do_bpm)
        vl.addWidget(btn)
        return grp

    def _section_simple(self, title: str, paths: list, tip: str = "") -> QGroupBox:
        grp = QGroupBox(title)
        vl = QVBoxLayout(grp)
        if tip:
            lbl = QLabel(tip)
            lbl.setStyleSheet("color: #888; font-size: 11px;")
            lbl.setWordWrap(True)
            vl.addWidget(lbl)
        vl.addWidget(_file_list(paths))
        return grp


# ── Helpers ───────────────────────────────────────────────────────────────────

def _file_list(paths: list) -> QListWidget:
    lst = QListWidget()
    lst.setAlternatingRowColors(True)
    lst.setMaximumHeight(160)

    for p in paths[:_MAX_LIST]:
        item = QListWidgetItem(os.path.basename(p))
        item.setToolTip(p)
        lst.addItem(item)

    if len(paths) > _MAX_LIST:
        overflow = QListWidgetItem(f"  … and {len(paths) - _MAX_LIST} more files")
        overflow.setFlags(Qt.ItemFlag.NoItemFlags)
        overflow.setForeground(QColor("#888"))
        lst.addItem(overflow)

    return lst
