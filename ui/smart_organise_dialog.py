"""Smart Organise – two-phase dialog.

Phase 1  Claude analyses the folder and returns three distinct approaches (fast).
Phase 2  User picks one; Claude generates the full set of file operations.

The dialog auto-starts Phase 1 on open, transitions through states using a
QStackedWidget, and accepts when Phase 2 completes.  The caller reads
result_data after exec() == Accepted and passes it to SmartOrganisePreviewDialog.
"""
from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup, QDialog, QFrame, QHBoxLayout, QLabel,
    QMessageBox, QProgressBar, QPushButton, QRadioButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

import activity_log
import config
import database as db

_MODEL         = "claude-sonnet-4-20250514"
_MAX_TOKENS_P1 = 1500    # just 3 short approach descriptions
_MAX_TOKENS_P2 = 16000   # full file operations

_PAGE_LOADING = 0
_PAGE_OPTIONS = 1

# ── System prompts ────────────────────────────────────────────────────────────

_PHASE1_SYSTEM = """\
You are a music production assistant helping a drum and bass producer organise sample files.

Analyse the provided sample metadata and propose EXACTLY THREE distinct organisational
approaches.

First, identify the instrument or sound type of each file from its filename, tags, BPM and key.
Recognise types including: kicks, snares, hats, cymbals, percussion, bass, sub bass, reese,
neuro bass, leads, pads, chords, stabs, vocals, fx, foley, atmospheres, breaks, loops,
one-shots, and risers.

Then propose 3 meaningfully different approaches based on what these particular files actually
are.  Make the approaches genuinely distinct — for example:
  - By instrument type (top-level), then BPM bracket (secondary)
  - By musical character / key
  - By function (rhythmic, harmonic, textural, fx)
  - By BPM first, then instrument type within each bracket

Each approach should be grounded in the actual content of the files, not generic templates.

Return ONLY valid JSON (no markdown fences, no explanation text):
{
  "approaches": [
    {
      "id": "A",
      "name": "4–6 word name",
      "description": "one sentence describing the logic",
      "top_level_folders": ["drums", "bass", "leads", ...],
      "secondary_logic": "brief note on secondary/tertiary grouping"
    },
    { "id": "B", ... },
    { "id": "C", ... }
  ]
}
"""

_PHASE2_SYSTEM = """\
You are a music production assistant organising drum and bass sample files.

Apply the specified organisational approach to generate the complete list of folder creation
and file move/rename operations.

Instrument identification:
  Identify sound type from filename, tags, BPM and key.  Recognise: kicks, snares, hats,
  cymbals, perc, bass, sub, reese, neuro, leads, pads, chords, stabs, vox, fx, foley,
  atmospheres, breaks, loops, one-shots, risers, and others.

Naming conventions:
  - Lowercase, underscores only (no spaces, no hyphens)
  - Include BPM where genuinely useful (loops/breaks): amen_loop_170.wav
  - Include key where genuinely useful (melodic content): pad_lush_Cmin.wav
  - Keep filenames descriptive but concise
  - Sequential numbers for similar files: kick_01.wav, kick_02.wav
  - Preserve the original file extension exactly

Return ONLY valid JSON (no markdown fences, no explanation):
{
  "folders": ["relative/folder/path", ...],
  "operations": [
    {"from": "current/relative/path.wav", "to": "proposed/relative/path.wav"},
    ...
  ],
  "conventions": {
    "style": "name of the chosen approach",
    "description": "one sentence describing the logic",
    "naming_hints": "brief note on naming decisions made in this run",
    "top_level_folders": ["list", "of", "top-level", "folders", "used"]
  }
}

All paths MUST be relative to the library root.
Only include operations where the path actually changes.
"""


# ── Option card widget ────────────────────────────────────────────────────────

class _OptionCard(QFrame):
    """Clickable card showing one organisational approach."""

    def __init__(self, approach: dict, radio: QRadioButton, parent=None):
        super().__init__(parent)
        self._radio = radio
        self.setObjectName("OptionCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_style(False)

        hl = QHBoxLayout(self)
        hl.setContentsMargins(12, 10, 12, 10)
        hl.setSpacing(12)
        hl.addWidget(radio)

        vl = QVBoxLayout()
        vl.setSpacing(4)

        name_lbl = QLabel(
            f"<b>Option {approach.get('id', '?')} — {approach.get('name', '')}</b>"
        )
        vl.addWidget(name_lbl)

        desc = approach.get("description", "")
        if desc:
            desc_lbl = QLabel(desc)
            desc_lbl.setWordWrap(True)
            desc_lbl.setStyleSheet("color: #a6adc8;")
            vl.addWidget(desc_lbl)

        detail_parts = []
        folders = approach.get("top_level_folders", [])
        if folders:
            detail_parts.append("Top-level: " + ", ".join(folders))
        sec = approach.get("secondary_logic", "")
        if sec:
            detail_parts.append("Secondary: " + sec)
        if detail_parts:
            detail_lbl = QLabel("  ·  ".join(detail_parts))
            detail_lbl.setWordWrap(True)
            detail_lbl.setStyleSheet("color: #6c7086; font-size: 11px;")
            vl.addWidget(detail_lbl)

        hl.addLayout(vl, stretch=1)

        radio.toggled.connect(self._update_style)

    def _update_style(self, selected: bool):
        if selected:
            self.setStyleSheet(
                "QFrame#OptionCard {"
                "  border: 1px solid #89b4fa;"
                "  border-radius: 6px;"
                "  background: #1e2a3a;"
                "}"
            )
        else:
            self.setStyleSheet(
                "QFrame#OptionCard {"
                "  border: 1px solid #313244;"
                "  border-radius: 6px;"
                "  background: #181825;"
                "}"
            )

    def mousePressEvent(self, event):
        self._radio.setChecked(True)
        super().mousePressEvent(event)


# ── Main dialog ───────────────────────────────────────────────────────────────

class SmartOrganiseDialog(QDialog):
    """Two-phase: generate 3 approaches → user picks → generate operations."""

    _phase1_done = pyqtSignal(object, str)   # (approaches list or None, error)
    _phase2_done = pyqtSignal(object, str)   # (result dict or None, error)

    def __init__(self, folder: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Smart Organise")
        self.setMinimumWidth(580)
        self.result_data: Optional[dict] = None
        self._folder      = folder
        self._conventions = config.get_smart_organise_conventions()
        self._radio_group = QButtonGroup(self)
        self._approaches: list = []

        self._build_ui()
        self._phase1_done.connect(self._on_phase1_done)
        self._phase2_done.connect(self._on_phase2_done)
        QTimer.singleShot(150, self._start_phase1)

    # ── Build ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(16, 16, 16, 16)

        # Persistent header
        lib      = config.get_active_library()
        lib_name = lib["name"] if lib else "Default"
        folder_name = os.path.basename(self._folder) if self._folder else lib_name
        n = self._count_samples()

        hdr = QLabel(
            f"Library: <b>{lib_name}</b>  •  "
            f"Folder: <b>{folder_name}</b>  •  "
            f"{n} sample{'s' if n != 1 else ''}"
        )
        hdr.setStyleSheet("color: #888; font-size: 12px;")
        hdr.setToolTip(self._folder or lib_name)
        root.addWidget(hdr)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep)

        # ── Stacked pages ──────────────────────────────────────────────────
        self._stack = QStackedWidget()
        root.addWidget(self._stack, stretch=1)

        # Page 0 — loading / error
        load_page = QWidget()
        lp = QVBoxLayout(load_page)
        lp.setContentsMargins(0, 12, 0, 12)
        self._loading_label = QLabel("Generating approach options…")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_label.setWordWrap(True)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._load_error_label = QLabel("")
        self._load_error_label.setStyleSheet("color: #f38ba8; font-size: 11px;")
        self._load_error_label.setWordWrap(True)
        self._load_error_label.hide()
        lp.addStretch()
        lp.addWidget(self._loading_label)
        lp.addWidget(self._progress)
        lp.addWidget(self._load_error_label)
        lp.addStretch()
        self._stack.addWidget(load_page)

        # Page 1 — option selection
        opt_page = QWidget()
        op = QVBoxLayout(opt_page)
        op.setContentsMargins(0, 4, 0, 4)
        op.setSpacing(6)
        op.addWidget(QLabel("<b>Choose an organisational approach:</b>"))
        self._cards_vl = QVBoxLayout()
        self._cards_vl.setSpacing(6)
        op.addLayout(self._cards_vl)
        self._prev_hint_lbl = QLabel("")
        self._prev_hint_lbl.setStyleSheet("color: #6c7086; font-size: 11px; margin-top: 4px;")
        self._prev_hint_lbl.setWordWrap(True)
        self._prev_hint_lbl.hide()
        op.addWidget(self._prev_hint_lbl)
        op.addStretch()
        self._stack.addWidget(opt_page)

        # ── Buttons ────────────────────────────────────────────────────────
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep2)

        btn_hl = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        self._action_btn = QPushButton("Analysing…")
        self._action_btn.setDefault(True)
        self._action_btn.setEnabled(False)
        self._action_btn.clicked.connect(self._on_action)
        btn_hl.addStretch()
        btn_hl.addWidget(cancel_btn)
        btn_hl.addWidget(self._action_btn)
        root.addLayout(btn_hl)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _count_samples(self) -> int:
        return len(self._get_samples())

    def _get_samples(self) -> list:
        all_s = db.get_all_samples_for_export()
        if not self._folder:
            return all_s
        prefix = self._folder.rstrip("/\\") + os.sep
        return [
            s for s in all_s
            if s["path"] == self._folder or s["path"].startswith(prefix)
        ]

    def _set_loading(self, text: str):
        self._loading_label.setText(text)
        self._load_error_label.hide()
        self._progress.setRange(0, 0)
        self._progress.show()
        self._stack.setCurrentIndex(_PAGE_LOADING)
        self._action_btn.setEnabled(False)
        self._action_btn.setText("Working…")
        self.setMinimumHeight(200)

    def _set_load_error(self, error: str):
        self._load_error_label.setText(f"Error: {error}")
        self._load_error_label.show()
        self._progress.hide()
        self._action_btn.setEnabled(True)
        self._action_btn.setText("Retry")

    # ── Phase 1 ───────────────────────────────────────────────────────────

    def _start_phase1(self):
        self._set_loading(
            "Analysing samples and generating approach options…\n"
            "This usually takes 10–20 seconds."
        )
        samples = self._get_samples()
        threading.Thread(
            target=self._p1_worker,
            args=(samples, config.get_api_key(), self._conventions),
            daemon=True,
        ).start()

    def _p1_worker(self, samples, api_key, conventions):
        try:
            result = _call_phase1(samples, api_key, conventions, self._folder)
            self._phase1_done.emit(result, "")
        except Exception as exc:
            self._phase1_done.emit(None, str(exc))

    def _on_phase1_done(self, result, error):
        if error:
            self._set_load_error(error)
            activity_log.error(f"Smart Organise phase 1 failed: {error}")
            return

        approaches = result.get("approaches", [])
        if not approaches:
            self._set_load_error("No approaches returned. Please try again.")
            return

        self._approaches = approaches
        self._build_cards(approaches)
        self._stack.setCurrentIndex(_PAGE_OPTIONS)
        self._action_btn.setEnabled(True)
        self._action_btn.setText("Generate preview →")
        self.setMinimumHeight(460)
        self.adjustSize()

    # ── Option cards ──────────────────────────────────────────────────────

    def _build_cards(self, approaches: list):
        # Clear previous cards and radio buttons
        while self._cards_vl.count():
            item = self._cards_vl.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for btn in self._radio_group.buttons():
            self._radio_group.removeButton(btn)

        for i, approach in enumerate(approaches):
            radio = QRadioButton()
            self._radio_group.addButton(radio, i)
            card = _OptionCard(approach, radio, self)
            self._cards_vl.addWidget(card)
            if i == 0:
                radio.setChecked(True)

        # Show previous style as a hint
        if self._conventions:
            style = self._conventions.get("style", "")
            if style:
                self._prev_hint_lbl.setText(
                    f"Last run used: \"{style}\" — you can pick the same or a different approach."
                )
                self._prev_hint_lbl.show()

    def _get_selected_approach(self) -> Optional[dict]:
        idx = self._radio_group.checkedId()
        if 0 <= idx < len(self._approaches):
            return self._approaches[idx]
        return None

    # ── Action button ─────────────────────────────────────────────────────

    def _on_action(self):
        if self._stack.currentIndex() == _PAGE_LOADING:
            # Retry after phase 1 error
            self._load_error_label.hide()
            self._start_phase1()
            return

        # On options page: start phase 2
        approach = self._get_selected_approach()
        if not approach:
            return

        self._set_loading(
            f"Generating file operations for:\n"
            f"\"{approach['name']}\"\n\n"
            "This may take 30–60 seconds."
        )
        samples = self._get_samples()
        threading.Thread(
            target=self._p2_worker,
            args=(samples, config.get_api_key(), approach, self._conventions),
            daemon=True,
        ).start()

    def _p2_worker(self, samples, api_key, approach, conventions):
        try:
            result = _call_phase2(samples, api_key, approach, conventions, self._folder)
            self._phase2_done.emit(result, "")
        except Exception as exc:
            self._phase2_done.emit(None, str(exc))

    def _on_phase2_done(self, result, error):
        if error:
            # Return to options page; show error as a dialog so it's clearly visible
            self._stack.setCurrentIndex(_PAGE_OPTIONS)
            self._progress.hide()
            self._action_btn.setEnabled(True)
            self._action_btn.setText("Generate preview →")
            QMessageBox.critical(
                self, "Generation failed",
                f"Could not generate file operations:\n\n{error}\n\n"
                "Please check your API key and try again.",
            )
            activity_log.error(f"Smart Organise phase 2 failed: {error}")
            return

        approach = self._get_selected_approach() or {}
        n_ops     = len(result.get("operations", []))
        n_folders = len(result.get("folders", []))
        folder_label = os.path.basename(self._folder) if self._folder else "library"
        activity_log.log(
            f"Smart Organise ({folder_label}): "
            f"approach \"{approach.get('name', '?')}\" → "
            f"{n_ops} operation(s), {n_folders} folder(s)"
        )
        self.result_data = result
        self.accept()


# ── API helpers ───────────────────────────────────────────────────────────────

def _samples_to_json(samples: list, lib_root: str) -> str:
    records = []
    for s in samples:
        path = s["path"]
        if lib_root:
            try:
                path = str(Path(path).relative_to(lib_root))
            except ValueError:
                pass
        rec: dict = {"path": path}
        if s["bpm"]:
            rec["bpm"] = round(s["bpm"], 1)
        if s["key"]:
            rec["key"] = s["key"]
        if s["rating"]:
            rec["rating"] = s["rating"]
        if s["tags"]:
            rec["tags"] = s["tags"]
        if s["notes"]:
            rec["notes"] = s["notes"]
        records.append(rec)
    return json.dumps(records, separators=(",", ":"))


def _call_phase1(samples: list, api_key: str,
                 conventions: Optional[dict], folder: str) -> dict:
    if not api_key:
        raise RuntimeError(
            "No API key configured. Add your Claude API key in Settings."
        )
    lib      = config.get_active_library()
    lib_root = lib["root"] if lib else ""

    scope = _scope_line(folder, lib_root, len(samples))
    lines = [scope, "", "Sample metadata:", _samples_to_json(samples, lib_root)]

    if conventions:
        style = conventions.get("style", "")
        if style:
            lines += [
                "",
                f"Note: the user previously used the approach \"{style}\". "
                "You may offer something similar as one of the three options, but also "
                "include genuinely different alternatives.",
            ]

    return _api_call(_PHASE1_SYSTEM, "\n".join(lines), api_key, _MAX_TOKENS_P1)


def _call_phase2(samples: list, api_key: str, approach: dict,
                 conventions: Optional[dict], folder: str) -> dict:
    if not api_key:
        raise RuntimeError(
            "No API key configured. Add your Claude API key in Settings."
        )
    lib      = config.get_active_library()
    lib_root = lib["root"] if lib else ""

    scope = _scope_line(folder, lib_root, len(samples))

    approach_block = (
        f"CHOSEN APPROACH: Option {approach.get('id', '?')} — {approach.get('name', '')}\n"
        f"DESCRIPTION: {approach.get('description', '')}\n"
        f"SECONDARY LOGIC: {approach.get('secondary_logic', '')}\n"
        f"TOP-LEVEL FOLDERS: {', '.join(approach.get('top_level_folders', []))}"
    )

    # Conventions are a loose style reference only — not rigid rules
    style_hint = ""
    if conventions:
        hints = conventions.get("naming_hints", "")
        if hints:
            style_hint = (
                f"\nNaming style hint from previous run (use as loose reference, "
                f"not a rigid rule): {hints}"
            )

    lines = [
        scope, "",
        approach_block,
        style_hint,
        "",
        f"Sample metadata ({len(samples)} files):",
        _samples_to_json(samples, lib_root),
    ]
    return _api_call(_PHASE2_SYSTEM, "\n".join(lines), api_key, _MAX_TOKENS_P2)


def _scope_line(folder: str, lib_root: str, n: int) -> str:
    if folder and lib_root:
        try:
            rel = str(Path(folder).relative_to(lib_root))
        except ValueError:
            rel = folder
        return f"Scope: files under '{rel}/'  ({n} samples)"
    return f"Scope: entire library  ({n} samples)"


def _api_call(system: str, user_msg: str, api_key: str, max_tokens: int) -> dict:
    payload = json.dumps({
        "model":      _MODEL,
        "max_tokens": max_tokens,
        "system":     system,
        "messages":   [{"role": "user", "content": user_msg}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key":         api_key,
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        err_text = exc.read().decode(errors="replace")
        raise RuntimeError(f"API error {exc.code}: {err_text[:400]}")

    raw = body["content"][0]["text"].strip()

    # Strip markdown fences Claude occasionally adds
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.rstrip().endswith("```"):
            raw = raw.rstrip()[:-3].strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Could not parse response as JSON: {exc}\n\n"
            f"Response (first 500 chars):\n{raw[:500]}"
        )
