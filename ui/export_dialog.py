"""Export Library Data dialogue – CSV or JSON output."""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QRadioButton, QButtonGroup,
    QLineEdit, QPushButton, QFileDialog, QMessageBox, QGroupBox, QDialogButtonBox,
    QFrame,
)

import activity_log
import config
import database as db


class ExportDialog(QDialog):
    """Modal dialogue for exporting the active library to CSV or JSON."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export Library Data")
        self.setMinimumWidth(480)
        self._build_ui()
        self._update_info()

    # ── Build ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(12)

        # ── Info ──────────────────────────────────────────────────────────
        lib = config.get_active_library()
        lib_name = lib["name"] if lib else "Default"
        lib_root = lib["root"] if lib else ""

        info_grp = QGroupBox("Active library")
        info_vl = QVBoxLayout(info_grp)
        self._lib_label = QLabel(f"<b>{lib_name}</b>")
        self._count_label = QLabel("")
        self._count_label.setStyleSheet("color: #888; font-size: 11px;")
        if lib_root:
            root_label = QLabel(lib_root)
            root_label.setStyleSheet("color: #888; font-size: 11px;")
            info_vl.addWidget(root_label)
        info_vl.addWidget(self._lib_label)
        info_vl.addWidget(self._count_label)
        root.addWidget(info_grp)

        # ── Format ────────────────────────────────────────────────────────
        fmt_grp = QGroupBox("Export format")
        fmt_hl = QHBoxLayout(fmt_grp)

        self._fmt_group = QButtonGroup(self)
        self._csv_radio = QRadioButton("CSV  (spreadsheet-friendly, one row per sample)")
        self._json_radio = QRadioButton("JSON  (structured, preserves tag lists)")
        self._csv_radio.setChecked(True)
        self._fmt_group.addButton(self._csv_radio)
        self._fmt_group.addButton(self._json_radio)

        fmt_vl = QVBoxLayout()
        fmt_vl.addWidget(self._csv_radio)
        fmt_vl.addWidget(self._json_radio)
        fmt_hl.addLayout(fmt_vl)
        root.addWidget(fmt_grp)

        # Format description
        self._fmt_desc = QLabel("")
        self._fmt_desc.setStyleSheet("color: #888; font-size: 11px;")
        self._fmt_desc.setWordWrap(True)
        root.addWidget(self._fmt_desc)
        self._csv_radio.toggled.connect(self._update_fmt_desc)
        self._update_fmt_desc()

        # ── Destination ───────────────────────────────────────────────────
        dest_grp = QGroupBox("Save to")
        dest_hl = QHBoxLayout(dest_grp)

        self._dest_edit = QLineEdit()
        self._dest_edit.setPlaceholderText("Choose a file location…")
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse)
        dest_hl.addWidget(self._dest_edit)
        dest_hl.addWidget(browse_btn)
        root.addWidget(dest_grp)

        # Update default filename when format changes
        self._csv_radio.toggled.connect(self._update_default_filename)
        self._json_radio.toggled.connect(self._update_default_filename)

        # ── Buttons ───────────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep)

        btn_box = QDialogButtonBox()
        self._export_btn = btn_box.addButton(
            "Export", QDialogButtonBox.ButtonRole.AcceptRole
        )
        btn_box.addButton(QDialogButtonBox.StandardButton.Cancel)
        self._export_btn.clicked.connect(self._on_export)
        btn_box.rejected.connect(self.reject)
        root.addWidget(btn_box)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _update_info(self):
        samples = db.get_all_samples_for_export()
        n = len(samples)
        self._count_label.setText(f"{n} sample{'s' if n != 1 else ''} will be exported")
        self._samples_cache = samples

    def _update_fmt_desc(self):
        if self._csv_radio.isChecked():
            self._fmt_desc.setText(
                "Columns: path, filename, rating, bpm, key, tags (semicolon-separated), notes"
            )
        else:
            self._fmt_desc.setText(
                "Top-level keys: library, root, exported_at, sample_count, samples[]. "
                "Each sample has path, filename, rating, bpm, key, tags[], notes."
            )

    def _update_default_filename(self):
        """If the user hasn't typed a path, suggest a sensible default filename."""
        current = self._dest_edit.text().strip()
        if not current:
            return
        p = Path(current)
        ext = ".csv" if self._csv_radio.isChecked() else ".json"
        if p.suffix.lower() in (".csv", ".json"):
            self._dest_edit.setText(str(p.with_suffix(ext)))

    def _browse(self):
        lib = config.get_active_library()
        lib_name = (lib["name"] if lib else "library").replace(" ", "_").lower()
        ext = "csv" if self._csv_radio.isChecked() else "json"
        default_name = f"{lib_name}_export.{ext}"
        filter_str = (
            "CSV files (*.csv)" if ext == "csv" else "JSON files (*.json)"
        )
        # Start in the user-configured default export folder if set
        start_dir = config.get_default_export_folder() or str(Path.home())
        path, _ = QFileDialog.getSaveFileName(
            self, "Export library data",
            str(Path(start_dir) / default_name),
            f"{filter_str};;All files (*)",
        )
        if path:
            self._dest_edit.setText(path)

    # ── Export ────────────────────────────────────────────────────────────

    def _on_export(self):
        dest = self._dest_edit.text().strip()
        if not dest:
            QMessageBox.warning(self, "No destination", "Please choose a save location.")
            return

        samples = getattr(self, "_samples_cache", None) or db.get_all_samples_for_export()
        lib = config.get_active_library()

        try:
            if self._csv_radio.isChecked():
                self._write_csv(dest, samples)
            else:
                self._write_json(dest, samples, lib)
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return

        n = len(samples)
        fmt = "CSV" if self._csv_radio.isChecked() else "JSON"
        activity_log.log(f"Library exported: {n} sample{'s' if n != 1 else ''} as {fmt} → {dest}")
        QMessageBox.information(
            self, "Export complete",
            f"Exported {n} sample{'s' if n != 1 else ''} to:\n{dest}",
        )
        self.accept()

    def _write_csv(self, path: str, samples: list):
        with open(path, "w", newline="", encoding="utf-8") as f:
            fieldnames = ["path", "filename", "rating", "bpm", "key", "tags", "notes"]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for s in samples:
                writer.writerow({
                    **s,
                    "bpm":  f"{s['bpm']:.2f}" if s["bpm"] is not None else "",
                    "tags": "; ".join(s["tags"]),
                })

    def _write_json(self, path: str, samples: list, lib: dict):
        # Make a copy with bpm as float or null (already correct) and tags as list.
        payload = {
            "library":      lib["name"] if lib else "Default",
            "root":         lib["root"] if lib else "",
            "exported_at":  datetime.now().isoformat(timespec="seconds"),
            "sample_count": len(samples),
            "samples": [
                {
                    "path":     s["path"],
                    "filename": s["filename"],
                    "rating":   s["rating"],
                    "bpm":      round(s["bpm"], 2) if s["bpm"] is not None else None,
                    "key":      s["key"],
                    "tags":     s["tags"],
                    "notes":    s["notes"],
                }
                for s in samples
            ],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
