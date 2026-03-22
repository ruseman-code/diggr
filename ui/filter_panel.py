"""Left-hand filter sidebar: name search, tag checkboxes, star rating."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QCheckBox, QScrollArea, QSpinBox, QPushButton, QGroupBox,
)
import database as db


DEFAULT_TAGS = [
    "rolling", "atmospheric", "bass-heavy", "vocal", "break",
    "reese", "amen", "pad", "stab", "fx", "foley", "loop", "one-shot",
]


class FilterPanel(QWidget):
    filters_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(220)
        self._tag_checkboxes: dict[str, QCheckBox] = {}
        self._build_ui()

    # ── Build ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(10)

        # Name search
        grp_search = QGroupBox("Search filename")
        vl = QVBoxLayout(grp_search)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Type to filter…")
        self.search_edit.textChanged.connect(self.filters_changed)
        vl.addWidget(self.search_edit)
        root.addWidget(grp_search)

        # Min rating
        grp_rating = QGroupBox("Min rating")
        hl = QHBoxLayout(grp_rating)
        self.rating_spin = QSpinBox()
        self.rating_spin.setRange(0, 5)
        self.rating_spin.setSpecialValueText("Any")
        self.rating_spin.valueChanged.connect(self.filters_changed)
        hl.addWidget(self.rating_spin)
        hl.addWidget(QLabel("★"))
        hl.addStretch()
        root.addWidget(grp_rating)

        # Tags
        grp_tags = QGroupBox("Tags")
        tag_layout = QVBoxLayout(grp_tags)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)

        tag_container = QWidget()
        self._tag_vbox = QVBoxLayout(tag_container)
        self._tag_vbox.setSpacing(2)
        scroll.setWidget(tag_container)
        tag_layout.addWidget(scroll)

        btn_row = QHBoxLayout()
        clear_btn = QPushButton("Clear all")
        clear_btn.setFlat(True)
        clear_btn.clicked.connect(self._clear_tags)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        tag_layout.addLayout(btn_row)

        root.addWidget(grp_tags, stretch=1)

        self.refresh_tags()

    # ── Tag management ────────────────────────────────────────────────────

    def refresh_tags(self):
        """Rebuild tag checkboxes from DB + defaults."""
        known = set(db.all_tags()) | set(DEFAULT_TAGS)
        existing_names = set(self._tag_checkboxes.keys())

        for name in sorted(known - existing_names):
            cb = QCheckBox(name)
            cb.stateChanged.connect(self.filters_changed)
            self._tag_checkboxes[name] = cb
            self._tag_vbox.addWidget(cb)

    def _clear_tags(self):
        for cb in self._tag_checkboxes.values():
            cb.setChecked(False)

    # ── Public API ─────────────────────────────────────────────────────────

    @property
    def name_query(self) -> str:
        return self.search_edit.text().strip()

    @property
    def active_tags(self) -> list[str]:
        return [n for n, cb in self._tag_checkboxes.items() if cb.isChecked()]

    @property
    def min_rating(self) -> int:
        return self.rating_spin.value()
