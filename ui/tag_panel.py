"""Right-hand detail panel: star rating, tag pills, notes."""
from __future__ import annotations

from typing import Optional
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTextEdit, QGroupBox, QScrollArea, QSizePolicy,
    QFrame,
)
import database as db
from ui.filter_panel import DEFAULT_TAGS


class StarRating(QWidget):
    rating_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rating = 0
        hl = QHBoxLayout(self)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(4)
        self._buttons: list[QPushButton] = []
        for i in range(1, 6):
            btn = QPushButton("★")
            btn.setFixedSize(28, 28)
            btn.setFlat(True)
            btn.setStyleSheet("font-size: 18px; color: #888;")
            btn.clicked.connect(lambda _, v=i: self._set(v))
            self._buttons.append(btn)
            hl.addWidget(btn)
        hl.addStretch()

    def _set(self, value: int):
        # clicking the same star again clears the rating
        self._rating = 0 if self._rating == value else value
        self._refresh()
        self.rating_changed.emit(self._rating)

    def _refresh(self):
        for i, btn in enumerate(self._buttons):
            filled = i < self._rating
            btn.setStyleSheet(
                f"font-size: 18px; color: {'#f0c040' if filled else '#555'};"
            )

    def set_rating(self, value: int):
        self._rating = value
        self._refresh()


class TagChip(QWidget):
    removed = pyqtSignal(str)

    def __init__(self, tag: str, removable=True, parent=None):
        super().__init__(parent)
        self.tag = tag
        hl = QHBoxLayout(self)
        hl.setContentsMargins(4, 2, 4, 2)
        hl.setSpacing(4)

        lbl = QLabel(tag)
        hl.addWidget(lbl)

        if removable:
            close_btn = QPushButton("×")
            close_btn.setFixedSize(16, 16)
            close_btn.setFlat(True)
            close_btn.clicked.connect(lambda: self.removed.emit(self.tag))
            hl.addWidget(close_btn)

        self.setStyleSheet("""
            TagChip {
                background: #2a4a6a;
                border-radius: 4px;
            }
        """)


class FlowLayout(QHBoxLayout):
    """Dead-simple wrapping row – good enough for a tag pill row."""


class TagPanel(QWidget):
    tags_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(240)
        self._current_path = None  # type: Optional[str]
        self._build_ui()
        self.setEnabled(False)

    # ── Build ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(10)

        # File label
        self.file_label = QLabel("No file selected")
        self.file_label.setWordWrap(True)
        self.file_label.setStyleSheet("font-weight: bold; color: #ccc;")
        root.addWidget(self.file_label)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep)

        # Rating
        grp_rating = QGroupBox("Rating")
        rl = QVBoxLayout(grp_rating)
        self.star_rating = StarRating()
        self.star_rating.rating_changed.connect(self._on_rating_changed)
        rl.addWidget(self.star_rating)
        root.addWidget(grp_rating)

        # Active tags
        grp_tags = QGroupBox("Tags")
        tl = QVBoxLayout(grp_tags)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        scroll.setFixedHeight(130)

        self._chip_container = QWidget()
        self._chip_flow = QVBoxLayout(self._chip_container)
        self._chip_flow.setSpacing(4)
        self._chip_flow.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self._chip_container)
        tl.addWidget(scroll)

        # Quick-add preset tags
        preset_label = QLabel("Quick-add:")
        preset_label.setStyleSheet("color: #aaa; font-size: 11px;")
        tl.addWidget(preset_label)

        preset_scroll = QScrollArea()
        preset_scroll.setWidgetResizable(True)
        preset_scroll.setFrameShape(preset_scroll.Shape.NoFrame)
        preset_scroll.setFixedHeight(160)

        preset_container = QWidget()
        preset_vbox = QVBoxLayout(preset_container)
        preset_vbox.setSpacing(3)
        preset_vbox.setAlignment(Qt.AlignmentFlag.AlignTop)

        for tag in sorted(DEFAULT_TAGS):
            btn = QPushButton(tag)
            btn.setFlat(True)
            btn.setStyleSheet(
                "text-align: left; padding: 2px 6px; color: #9bc;"
            )
            btn.clicked.connect(lambda _, t=tag: self._add_tag(t))
            preset_vbox.addWidget(btn)

        preset_scroll.setWidget(preset_container)
        tl.addWidget(preset_scroll)

        # Custom tag input
        hl = QHBoxLayout()
        self.tag_input = QLineEdit()
        self.tag_input.setPlaceholderText("Custom tag…")
        self.tag_input.returnPressed.connect(self._add_custom_tag)
        add_btn = QPushButton("+")
        add_btn.setFixedWidth(28)
        add_btn.clicked.connect(self._add_custom_tag)
        hl.addWidget(self.tag_input)
        hl.addWidget(add_btn)
        tl.addLayout(hl)

        root.addWidget(grp_tags, stretch=1)

        # Notes
        grp_notes = QGroupBox("Notes")
        nl = QVBoxLayout(grp_notes)
        self.notes_edit = QTextEdit()
        self.notes_edit.setPlaceholderText("Free-form notes…")
        self.notes_edit.setFixedHeight(80)
        self.notes_edit.focusOutEvent = self._notes_focus_out
        nl.addWidget(self.notes_edit)
        root.addWidget(grp_notes)

    # ── Public API ─────────────────────────────────────────────────────────

    def load_sample(self, path: str):
        self._current_path = path
        self.setEnabled(True)

        import os
        self.file_label.setText(os.path.basename(path))

        sample = db.get_sample(path)
        rating = sample["rating"] if sample else 0
        notes = sample["notes"] if sample else ""

        self.star_rating.set_rating(rating)
        self.notes_edit.blockSignals(True)
        self.notes_edit.setPlainText(notes)
        self.notes_edit.blockSignals(False)

        self._refresh_chips()

    def clear(self):
        self._current_path = None
        self.setEnabled(False)
        self.file_label.setText("No file selected")
        self._refresh_chips()

    # ── Internals ──────────────────────────────────────────────────────────

    def _refresh_chips(self):
        # Clear existing chips
        while self._chip_flow.count():
            item = self._chip_flow.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._current_path:
            return

        for tag in db.tags_for_sample(self._current_path):
            chip = TagChip(tag)
            chip.removed.connect(self._remove_tag)
            self._chip_flow.addWidget(chip)

    def _add_tag(self, tag: str):
        if not self._current_path:
            return
        db.add_tag(self._current_path, tag)
        self._refresh_chips()
        self.tags_changed.emit()

    def _add_custom_tag(self):
        tag = self.tag_input.text().strip().lower()
        if tag:
            self._add_tag(tag)
            self.tag_input.clear()

    def _remove_tag(self, tag: str):
        if not self._current_path:
            return
        db.remove_tag(self._current_path, tag)
        self._refresh_chips()
        self.tags_changed.emit()

    def _on_rating_changed(self, value: int):
        if self._current_path:
            db.upsert_sample(self._current_path)
            db.set_rating(self._current_path, value)
            self.tags_changed.emit()

    def _notes_focus_out(self, event):
        if self._current_path:
            db.upsert_sample(self._current_path)
            db.set_notes(self._current_path, self.notes_edit.toPlainText())
        QTextEdit.focusOutEvent(self.notes_edit, event)
