"""Smart Organise – step 1: collect metadata and call the Claude API.

Opens a small dialog, runs the API call in a daemon thread, and accepts
when the response has been parsed.  The caller reads `result_data` after
exec() == Accepted.
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
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QProgressBar,
)

import activity_log
import config
import database as db

_MODEL      = "claude-sonnet-4-20250514"
_MAX_TOKENS = 16000

# ── System prompts ────────────────────────────────────────────────────────────

_FULL_SYSTEM = """\
You are a music production assistant helping a drum and bass producer organise a
large sample library.  You will receive metadata for every sample and must
suggest a reorganised folder structure with clean, consistent filenames.

Rules:
- Group by instrument/category first: drums, bass, leads, pads, fx, vocals,
  one_shots, misc
- Sub-group drums by type: breaks, kicks, snares, hats, cymbals, percussion
- Sub-group bass by character: reese, sub, neuro, stab, wobble
- Include BPM sub-folders for loops/breaks where appropriate
  (e.g. 160-165, 166-170, 171-174, 175plus)
- Include musical key in sub-folder or filename where known (melodic content)
- Filenames: lowercase, underscores, descriptive; include BPM and key where
  known.  Examples:
    amen_loop_170_Dmin.wav
    reese_sub_heavy_Fmin.wav
    pad_lush_atmospheric_Cmaj.wav
    kick_punchy_01.wav
- Preserve the original file extension exactly
- Only suggest an operation when the file would genuinely benefit from the move
  or rename — do not change a file just for the sake of it
- Be consistent — apply the same rules to all files

Return ONLY valid JSON (no markdown fences, no explanation text):
{
  "folders": ["relative/folder/path", ...],
  "operations": [
    {"from": "current/relative/path.wav", "to": "proposed/relative/path.wav"},
    ...
  ],
  "conventions": {
    "hierarchy": "brief description of the folder hierarchy rule",
    "bpm_brackets": {"160-165": [160, 166], "166-170": [166, 171], "171-174": [171, 175], "175plus": [175, 999]},
    "naming_pattern": "brief description of filename convention",
    "instrument_folders": ["drums", "bass", "leads", "pads", "fx", "vocals", "one_shots"],
    "notes": "any special cases or additional decisions"
  }
}

All paths MUST be relative to the library root.  Never include the absolute
library root path.  Only list folders that need to be created and operations
where the path actually changes.
"""

_INCREMENTAL_SYSTEM = """\
You are a music production assistant applying an established organisational
scheme to new or unorganised samples in a library.

The library already has a structure defined by the conventions object provided.
Apply these SAME conventions to the samples listed — do not alter or re-evaluate
the existing organisation, only handle the files given to you.

Return ONLY valid JSON (no markdown fences, no explanation):
{
  "folders": ["relative/folder/to/create/if/needed", ...],
  "operations": [
    {"from": "current/relative/path.wav", "to": "proposed/relative/path.wav"},
    ...
  ],
  "conventions": <return the conventions object unchanged>
}

All paths MUST be relative to the library root.  Only include operations where
the path genuinely changes.
"""


# ── Dialog ────────────────────────────────────────────────────────────────────

class SmartOrganiseDialog(QDialog):
    """Step 1 dialog: shows mode, runs API call, stores result_data on accept.

    Pass *folder* (absolute path) to scope the analysis to files under that
    directory only.  All samples in the library are used if folder is empty.
    """

    _api_done = pyqtSignal(object, str)   # (result dict or None, error string)

    def __init__(self, folder: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Smart Organise")
        self.setMinimumWidth(520)
        self.result_data: Optional[dict] = None
        self._folder      = folder
        self._conventions = config.get_smart_organise_conventions()
        self._build_ui()
        self._api_done.connect(self._on_api_done)

    # ── Build ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.setContentsMargins(16, 16, 16, 16)

        is_inc   = bool(self._conventions)
        lib      = config.get_active_library()
        lib_name = lib["name"] if lib else "Default"

        # Filter samples to the chosen folder
        all_samples = db.get_all_samples_for_export()
        if self._folder:
            folder_prefix = self._folder.rstrip("/\\") + os.sep
            samples = [
                s for s in all_samples
                if s["path"] == self._folder
                or s["path"].startswith(folder_prefix)
            ]
        else:
            samples = all_samples
        n = len(samples)
        self._samples_cache = samples

        # Mode badge
        if is_inc:
            badge = QLabel(
                "◎  Incremental update  —  applying saved conventions"
            )
            badge.setStyleSheet(
                "color: #a6e3a1; background: #1e3a2a; "
                "border-radius: 4px; padding: 6px 10px;"
            )
        else:
            badge = QLabel(
                "◎  First-time organise  —  Claude will create conventions"
            )
            badge.setStyleSheet(
                "color: #89b4fa; background: #1e2a3a; "
                "border-radius: 4px; padding: 6px 10px;"
            )
        root.addWidget(badge)

        folder_display = self._folder or "(entire library)"
        info = QLabel(
            f"Library: <b>{lib_name}</b>  •  "
            f"Folder: <b>{os.path.basename(self._folder) or lib_name}</b>  •  "
            f"{n} sample{'s' if n != 1 else ''}"
        )
        info.setStyleSheet("color: #888; font-size: 12px;")
        info.setToolTip(folder_display)
        info.setWordWrap(True)
        root.addWidget(info)

        if is_inc and self._conventions:
            cv_label = QLabel(
                f"Hierarchy: {self._conventions.get('hierarchy', '—')}\n"
                f"Naming: {self._conventions.get('naming_pattern', '—')}"
            )
            cv_label.setStyleSheet(
                "color: #6c7086; font-size: 11px; margin-left: 4px;"
            )
            cv_label.setWordWrap(True)
            root.addWidget(cv_label)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep)

        self._status_label = QLabel(
            f"Claude will analyse the {n} sample{'s' if n != 1 else ''} in this "
            f"folder and suggest a reorganised structure and file renames.\n\n"
            "This usually takes 20–60 seconds."
        )
        self._status_label.setWordWrap(True)
        root.addWidget(self._status_label)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)   # indeterminate
        self._progress.hide()
        root.addWidget(self._progress)

        self._error_label = QLabel("")
        self._error_label.setStyleSheet("color: #f38ba8; font-size: 11px;")
        self._error_label.setWordWrap(True)
        self._error_label.hide()
        root.addWidget(self._error_label)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep2)

        btn_hl = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        self._start_btn = QPushButton("Analyse with Claude…")
        self._start_btn.setDefault(True)
        self._start_btn.clicked.connect(self._on_start)
        btn_hl.addStretch()
        btn_hl.addWidget(cancel_btn)
        btn_hl.addWidget(self._start_btn)
        root.addLayout(btn_hl)

    # ── Slots ─────────────────────────────────────────────────────────────

    def _on_start(self):
        self._start_btn.setEnabled(False)
        self._error_label.hide()
        self._status_label.setText("Collecting sample metadata…")
        self._progress.show()
        # Let the UI repaint before we block
        QTimer.singleShot(50, self._launch_thread)

    def _launch_thread(self):
        n = len(self._samples_cache)
        self._status_label.setText(
            f"Sending {n} sample{'s' if n != 1 else ''} to Claude…\n"
            "This may take 30–60 seconds."
        )
        threading.Thread(
            target=self._worker,
            args=(self._samples_cache, config.get_api_key(),
                  self._conventions, self._folder),
            daemon=True,
        ).start()

    def _worker(self, samples, api_key, conventions, folder):
        try:
            result = _call_claude(samples, api_key, conventions, folder)
            self._api_done.emit(result, "")
        except Exception as exc:
            self._api_done.emit(None, str(exc))

    def _on_api_done(self, result, error):
        self._progress.hide()
        if error:
            self._error_label.setText(f"Error: {error}")
            self._error_label.show()
            self._status_label.setText(
                "The API call failed. Check your API key in Settings."
            )
            self._start_btn.setEnabled(True)
            activity_log.error(f"Smart Organise API call failed: {error}")
            return

        n_ops     = len(result.get("operations", []))
        n_folders = len(result.get("folders", []))
        folder_label = os.path.basename(self._folder) if self._folder else "library"
        activity_log.log(
            f"Smart Organise ({folder_label}): Claude returned "
            f"{n_ops} operation(s), {n_folders} folder(s)"
        )
        self.result_data = result
        self.accept()


# ── API helpers ───────────────────────────────────────────────────────────────

def _build_message(samples: list, conventions: Optional[dict],
                   folder: str = "") -> str:
    lib      = config.get_active_library()
    lib_name = lib["name"] if lib else "Default"
    lib_root = lib["root"] if lib else ""

    # Describe the scope so Claude understands it's a sub-folder run
    if folder and lib_root:
        try:
            rel_folder = str(Path(folder).relative_to(lib_root))
        except ValueError:
            rel_folder = folder
        scope_line = f"Scope: files under '{rel_folder}/' only"
    else:
        scope_line = "Scope: entire library"

    lines = [
        f"Library name: {lib_name}",
        f"Library root: {lib_root}",
        scope_line,
        f"Samples in scope: {len(samples)}",
        "",
        "Sample metadata (JSON array — paths are relative to library root):",
    ]

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

    lines.append(json.dumps(records, separators=(",", ":")))

    if conventions:
        lines += [
            "",
            "Existing conventions (apply these consistently):",
            json.dumps(conventions, indent=2),
        ]

    return "\n".join(lines)


def _call_claude(
    samples: list, api_key: str, conventions: Optional[dict],
    folder: str = "",
) -> dict:
    if not api_key:
        raise RuntimeError(
            "No API key configured. Please add your Claude API key in Settings."
        )

    system   = _INCREMENTAL_SYSTEM if conventions else _FULL_SYSTEM
    user_msg = _build_message(samples, conventions, folder)

    payload = json.dumps({
        "model":      _MODEL,
        "max_tokens": _MAX_TOKENS,
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

    # Strip markdown code fences Claude occasionally adds
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.rstrip().endswith("```"):
            raw = raw.rstrip()[:-3].strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Could not parse Claude's response as JSON: {exc}\n\n"
            f"Response (first 500 chars):\n{raw[:500]}"
        )
