# Diggr – project guide for Claude Code

## What this app is

Diggr is a desktop sample organiser and auditioning tool built in Python with PyQt6, aimed at music producers. The primary user is a drum and bass producer working in Logic Pro with a modular synthesiser setup and a large sample library (2,000+ files). A secondary user (the developer's brother) will eventually use the app on a separate machine in London.

The app allows the user to browse folders of audio files (wav, mp3, aiff), preview them, tag and rate them, filter by tag/BPM/key/rating, and export selections. It stores all metadata in a local SQLite database.

---

## Workflow instructions

After completing each feature or significant change, and after the user has confirmed they are happy with it, automatically run:

```
git add . && git commit -m "[brief description of what was just built]" && git push
```

Write a sensible, descriptive commit message based on what was just completed. Do not ask the user to do this manually.

---

## Tech stack

- Python 3
- PyQt6 for the UI
- pygame for audio playback
- librosa for BPM and key detection
- SQLite via the standard library for storage
- config.json stored at ~/.sample_organiser/config.json for user preferences
- Database stored at ~/.sample_organiser/libraries/ (one file per library)

---

## Architecture

```
sample_organiser/
├── main.py                 — entry point
├── database.py             — all SQLite logic
├── audio_player.py         — pygame.mixer wrapper
├── bpm_detector.py         — librosa BPM and key detection, runs in background threads
├── config.py               — config.json read/write helpers
├── requirements.txt
└── ui/
    ├── main_window.py      — root QMainWindow, wires panels together
    ├── file_browser.py     — centre panel: file list with BPM, key, rating, tag columns
    ├── filter_panel.py     — left sidebar: smart search, filters
    ├── tag_panel.py        — right sidebar: star rating, tag chips, suggestions, notes
    ├── project_panel.py    — project/session management
    └── waveform_widget.py  — waveform display
```

---

## What has been built

- Browse folders and scan for audio files (wav, mp3, aiff)
- Audio preview via pygame (play, stop, space bar toggle)
- Star rating (1–5)
- Tag chips with removal, quick-add DnB presets, custom tag input
- Notes field per sample
- Filter by filename, minimum rating, tags (AND logic)
- SQLite persistence
- Export selected/visible files to a folder
- Dark Catppuccin-inspired theme
- BPM detection via librosa (bulk, background threaded, progressive population of file browser)
- Key detection via librosa (per file, background threaded)
- BPM and key stored in database, not recalculated if already present
- BPM range filter in filter panel
- Sort file list by rating (ascending/descending) via column header
- Last opened folder remembered in config.json
- Project/session feature (named collections of samples, stored in database)
- Relative path support (paths stored relative to library root, resolved at runtime)
- Multi-library support (named libraries, each with own database, switchable from toolbar)
- Database export (CSV or JSON, from toolbar)
- Library health check (missing files, duplicates, untagged, no BPM, no rating)
- Librosa UserWarning for short files suppressed

---

## Key conventions and preferences

- The user is not a developer – keep code changes surgical and avoid unnecessary refactoring
- Always use relative paths when reading/writing to the database (resolve to absolute at runtime using library root from config.json)
- BPM and key detection always runs in background threads to avoid freezing the UI
- Files that already have BPM or key data in the database should never be re-analysed
- The app should feel fast and responsive even with 2,000+ files loaded
- Suppress librosa UserWarnings about short files
- DnB BPM range is 170–174bpm – use this as the default hint in any BPM filter UI
- Commit to GitHub automatically after each confirmed feature (see workflow instructions above)
