"""Left-hand filter sidebar: name search, tag checkboxes, star rating."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QCheckBox, QScrollArea, QSpinBox, QPushButton, QGroupBox, QComboBox,
)
import database as db
from bpm_detector import all_keys


DEFAULT_TAGS = [
    "rolling", "atmospheric", "bass-heavy", "vocal", "break",
    "reese", "amen", "pad", "stab", "fx", "foley", "loop", "one-shot",
]


class FilterPanel(QWidget):
    filters_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(220)
        self._tag_checkboxes: dict[str, QCheckBox] = {}
        self._build_ui()

    # ── Build ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(10)

        # Reset all filters button
        reset_all_btn = QPushButton("Reset all filters")
        reset_all_btn.setFlat(True)
        reset_all_btn.setStyleSheet("color: #e05080; text-align: right; padding: 0;")
        reset_all_btn.clicked.connect(self.reset_all)
        root.addWidget(reset_all_btn)

        # Smart search bar (primary)
        grp_smart = QGroupBox("Search")
        smart_vl = QVBoxLayout(grp_smart)
        self.smart_edit = QLineEdit()
        self.smart_edit.setPlaceholderText("filename, tag, key, BPM, notes…")
        self.smart_edit.textChanged.connect(self.filters_changed)
        smart_vl.addWidget(self.smart_edit)
        root.addWidget(grp_smart)

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

        # BPM range
        grp_bpm = QGroupBox("BPM range")
        bpm_layout = QVBoxLayout(grp_bpm)

        min_row = QHBoxLayout()
        min_row.addWidget(QLabel("Min"))
        self.bpm_min_spin = QSpinBox()
        self.bpm_min_spin.setRange(0, 400)
        self.bpm_min_spin.setSpecialValueText("—")
        self.bpm_min_spin.setToolTip("Minimum BPM (0 = no limit)")
        self.bpm_min_spin.valueChanged.connect(self.filters_changed)
        min_row.addWidget(self.bpm_min_spin)
        bpm_layout.addLayout(min_row)

        max_row = QHBoxLayout()
        max_row.addWidget(QLabel("Max"))
        self.bpm_max_spin = QSpinBox()
        self.bpm_max_spin.setRange(0, 400)
        self.bpm_max_spin.setSpecialValueText("—")
        self.bpm_max_spin.setToolTip("Maximum BPM (0 = no limit)")
        self.bpm_max_spin.valueChanged.connect(self.filters_changed)
        max_row.addWidget(self.bpm_max_spin)
        bpm_layout.addLayout(max_row)

        dnb_btn = QPushButton("DnB (170–174)")
        dnb_btn.setFlat(True)
        dnb_btn.setStyleSheet("color: #30c4c0; text-align: left; padding: 2px 0;")
        dnb_btn.setToolTip("Set range to typical drum and bass tempo")
        dnb_btn.clicked.connect(self._set_dnb_range)
        bpm_layout.addWidget(dnb_btn)

        root.addWidget(grp_bpm)

        # Key filter
        grp_key = QGroupBox("Key")
        kl = QVBoxLayout(grp_key)
        self.key_combo = QComboBox()
        self.key_combo.addItem("Any key", "")
        for k in all_keys():
            self.key_combo.addItem(k, k)
        self.key_combo.currentIndexChanged.connect(self.filters_changed)
        kl.addWidget(self.key_combo)
        root.addWidget(grp_key)

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

    def _set_dnb_range(self):
        self.bpm_min_spin.setValue(170)
        self.bpm_max_spin.setValue(174)

    def reset_all(self):
        """Clear every filter back to its default state."""
        self.smart_edit.blockSignals(True)
        self.search_edit.blockSignals(True)
        self.rating_spin.blockSignals(True)
        self.bpm_min_spin.blockSignals(True)
        self.bpm_max_spin.blockSignals(True)
        self.key_combo.blockSignals(True)

        self.smart_edit.clear()
        self.search_edit.clear()
        self.rating_spin.setValue(0)
        self.bpm_min_spin.setValue(0)
        self.bpm_max_spin.setValue(0)
        self.key_combo.setCurrentIndex(0)
        for cb in self._tag_checkboxes.values():
            cb.setChecked(False)

        self.smart_edit.blockSignals(False)
        self.search_edit.blockSignals(False)
        self.rating_spin.blockSignals(False)
        self.bpm_min_spin.blockSignals(False)
        self.bpm_max_spin.blockSignals(False)
        self.key_combo.blockSignals(False)

        self.filters_changed.emit()

    # ── Public API ─────────────────────────────────────────────────────────

    @property
    def smart_query(self) -> str:
        return self.smart_edit.text().strip()

    @property
    def name_query(self) -> str:
        return self.search_edit.text().strip()

    @property
    def active_tags(self) -> list[str]:
        return [n for n, cb in self._tag_checkboxes.items() if cb.isChecked()]

    @property
    def min_rating(self) -> int:
        return self.rating_spin.value()

    @property
    def min_bpm(self) -> int:
        return self.bpm_min_spin.value()

    @property
    def max_bpm(self) -> int:
        return self.bpm_max_spin.value()

    @property
    def key_filter(self) -> str:
        return self.key_combo.currentData() or ""
