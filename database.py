from __future__ import annotations

import sqlite3
import shutil
import os
from pathlib import Path
from typing import Optional

import config


# Legacy single-file path — used as fallback before library migration runs.
_LEGACY_DB_PATH = Path.home() / ".sample_organiser" / "library.db"

# Active library DB path — set by set_active_db() on startup and on switch.
_active_db_path: Optional[Path] = None


def set_active_db(path: Path):
    """Point all subsequent DB operations at *path*."""
    global _active_db_path
    _active_db_path = path


def _db_path() -> Path:
    return _active_db_path or _LEGACY_DB_PATH


def get_connection() -> sqlite3.Connection:
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS samples (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                path    TEXT UNIQUE NOT NULL,
                rating  INTEGER DEFAULT 0 CHECK(rating BETWEEN 0 AND 5),
                notes   TEXT DEFAULT '',
                bpm     REAL DEFAULT NULL,
                key     TEXT DEFAULT NULL
            );

            CREATE TABLE IF NOT EXISTS tags (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sample_tags (
                sample_id INTEGER REFERENCES samples(id) ON DELETE CASCADE,
                tag_id    INTEGER REFERENCES tags(id)    ON DELETE CASCADE,
                PRIMARY KEY (sample_id, tag_id)
            );

            CREATE TABLE IF NOT EXISTS projects (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT UNIQUE NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS project_samples (
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                sample_id  INTEGER NOT NULL REFERENCES samples(id)  ON DELETE CASCADE,
                PRIMARY KEY (project_id, sample_id)
            );

            CREATE INDEX IF NOT EXISTS idx_samples_path ON samples(path);
            CREATE INDEX IF NOT EXISTS idx_sample_tags_sample ON sample_tags(sample_id);
            CREATE INDEX IF NOT EXISTS idx_project_samples_proj ON project_samples(project_id);
        """)
        # Migrate databases that pre-date these columns
        for ddl in (
            "ALTER TABLE samples ADD COLUMN bpm REAL DEFAULT NULL",
            "ALTER TABLE samples ADD COLUMN key TEXT DEFAULT NULL",
        ):
            try:
                conn.execute(ddl)
            except Exception:
                pass  # column already exists


# ── Path helpers ──────────────────────────────────────────────────────────────
# All DB functions receive/return *absolute* paths in their public API.
# These helpers convert at the boundary so the DB always stores relative paths.

def _rel(path: str) -> str:
    """Absolute → relative (for storing in DB)."""
    return config.to_relative(path)


def _abs(path: str) -> str:
    """Relative → absolute (for returning to callers)."""
    return config.to_absolute(path)


# ── Migration helpers ─────────────────────────────────────────────────────────

def migrate_to_relative_paths(library_root: str):
    """One-time migration: rewrite all stored absolute paths as relative paths.

    Paths that fall outside *library_root* are left unchanged (absolute
    fallback).  Safe to call multiple times – unchanged rows are skipped.
    """
    if not library_root:
        return
    with get_connection() as conn:
        rows = conn.execute("SELECT id, path FROM samples").fetchall()
        for row in rows:
            stored = row["path"]
            p = Path(stored)
            if not p.is_absolute():
                continue  # already relative
            try:
                rel = str(p.relative_to(library_root))
                if rel != stored:
                    conn.execute(
                        "UPDATE samples SET path = ? WHERE id = ?",
                        (rel, row["id"]),
                    )
            except ValueError:
                pass  # outside root, leave as absolute


def change_library_root(old_root: str, new_root: str):
    """Re-relativize every stored path from *old_root* to *new_root*.

    Used when the user confirms a library-root change (e.g. they opened a
    folder outside the current root and agreed to move the root up to the
    common ancestor).
    """
    if not new_root:
        return
    with get_connection() as conn:
        rows = conn.execute("SELECT id, path FROM samples").fetchall()
        for row in rows:
            stored = row["path"]
            # Reconstruct absolute regardless of how it's stored
            p = Path(stored)
            abs_path = stored if p.is_absolute() else str(Path(old_root) / stored)
            try:
                new_rel = str(Path(abs_path).relative_to(new_root))
                if new_rel != stored:
                    conn.execute(
                        "UPDATE samples SET path = ? WHERE id = ?",
                        (new_rel, row["id"]),
                    )
            except ValueError:
                pass  # outside new_root, leave as-is


# ── Samples ───────────────────────────────────────────────────────────────────

def upsert_sample(path: str) -> int:
    rel = _rel(path)
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO samples (path) VALUES (?)", (rel,)
        )
        row = conn.execute(
            "SELECT id FROM samples WHERE path = ?", (rel,)
        ).fetchone()
        return row["id"]


def set_rating(path: str, rating: int):
    with get_connection() as conn:
        conn.execute(
            "UPDATE samples SET rating = ? WHERE path = ?", (rating, _rel(path))
        )


def set_bpm(path: str, bpm: float):
    """Store a detected BPM (pass 0.0 to clear)."""
    value = bpm if bpm > 0 else None
    with get_connection() as conn:
        conn.execute(
            "UPDATE samples SET bpm = ? WHERE path = ?", (value, _rel(path))
        )


def set_key(path: str, key: str):
    """Store a detected musical key (pass '' to clear)."""
    value = key if key else None
    with get_connection() as conn:
        conn.execute(
            "UPDATE samples SET key = ? WHERE path = ?", (value, _rel(path))
        )


def set_notes(path: str, notes: str):
    with get_connection() as conn:
        conn.execute(
            "UPDATE samples SET notes = ? WHERE path = ?", (notes, _rel(path))
        )


def get_sample(path: str) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM samples WHERE path = ?", (_rel(path),)
        ).fetchone()


# ── Tags ──────────────────────────────────────────────────────────────────────

def all_tags() -> "list[str]":
    with get_connection() as conn:
        rows = conn.execute("SELECT name FROM tags ORDER BY name").fetchall()
        return [r["name"] for r in rows]


def ensure_tag(name: str) -> int:
    with get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
        return conn.execute(
            "SELECT id FROM tags WHERE name = ?", (name,)
        ).fetchone()["id"]


def add_tag(path: str, tag: str):
    sample_id = upsert_sample(path)
    tag_id = ensure_tag(tag)
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sample_tags VALUES (?, ?)",
            (sample_id, tag_id),
        )


def remove_tag(path: str, tag: str):
    with get_connection() as conn:
        conn.execute("""
            DELETE FROM sample_tags
            WHERE sample_id = (SELECT id FROM samples WHERE path = ?)
              AND tag_id    = (SELECT id FROM tags    WHERE name = ?)
        """, (_rel(path), tag))


def tags_for_sample(path: str) -> "list[str]":
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT t.name FROM tags t
            JOIN sample_tags st ON st.tag_id = t.id
            JOIN samples s      ON s.id = st.sample_id
            WHERE s.path = ?
            ORDER BY t.name
        """, (_rel(path),)).fetchall()
        return [r["name"] for r in rows]


# ── Projects ──────────────────────────────────────────────────────────────────

def get_projects() -> list:
    with get_connection() as conn:
        return conn.execute(
            "SELECT id, name FROM projects ORDER BY name COLLATE NOCASE"
        ).fetchall()


def create_project(name: str) -> int:
    with get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO projects (name) VALUES (?)", (name,))
        return conn.execute(
            "SELECT id FROM projects WHERE name = ?", (name,)
        ).fetchone()["id"]


def rename_project(project_id: int, new_name: str):
    with get_connection() as conn:
        conn.execute(
            "UPDATE projects SET name = ? WHERE id = ?", (new_name, project_id)
        )


def delete_project(project_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))


def add_to_project(path: str, project_id: int):
    sample_id = upsert_sample(path)
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO project_samples VALUES (?, ?)",
            (project_id, sample_id),
        )


def remove_from_project(path: str, project_id: int):
    with get_connection() as conn:
        conn.execute("""
            DELETE FROM project_samples
            WHERE project_id = ?
              AND sample_id  = (SELECT id FROM samples WHERE path = ?)
        """, (project_id, _rel(path)))


def project_sample_count(project_id: int) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM project_samples WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        return row["n"] if row else 0


# ── Search / filter ───────────────────────────────────────────────────────────

def search_samples(
    folder: str = "",
    name_query: str = "",
    tags: Optional[list] = None,
    min_rating: int = 0,
    min_bpm: int = 0,
    max_bpm: int = 0,
    key_filter: str = "",
    project_id: Optional[int] = None,
    smart_query: str = "",
) -> list:
    """Return rows for samples matching all active filters.

    When *project_id* is set the folder prefix filter is skipped so the project
    can contain samples from any location on disk.

    *smart_query* performs an OR search across filename, notes, key, tags and
    BPM (fuzzy ±5 if the query looks like a number).

    All returned rows are dicts with absolute paths.
    """
    clauses: list = []
    params:  list = []

    # Folder filter — convert absolute folder to relative for LIKE matching
    if folder and project_id is None:
        root = config.get_library_root()
        if root:
            try:
                rel_folder = str(Path(folder).relative_to(root))
                if rel_folder != ".":
                    # subfolder within root
                    clauses.append("s.path LIKE ?")
                    params.append(f"{rel_folder}%")
                # else: folder IS the root, all relative paths are in scope
            except ValueError:
                # folder is outside root (or root unset) — use absolute LIKE
                clauses.append("s.path LIKE ?")
                params.append(f"{folder}%")
        else:
            clauses.append("s.path LIKE ?")
            params.append(f"{folder}%")

    if smart_query:
        q = smart_query.strip()
        like = f"%{q}%"
        or_parts = [
            "s.path LIKE ?",
            "s.notes LIKE ?",
            "s.key LIKE ?",
            "EXISTS (SELECT 1 FROM sample_tags st2 JOIN tags t2 ON t2.id = st2.tag_id"
            " WHERE st2.sample_id = s.id AND t2.name LIKE ?)",
        ]
        or_params = [like, like, like, like]
        try:
            bpm_val = float(q)
            if 20.0 <= bpm_val <= 400.0:
                or_parts.append("(s.bpm IS NOT NULL AND ABS(s.bpm - ?) <= 5)")
                or_params.append(bpm_val)
        except ValueError:
            pass
        clauses.append("(" + " OR ".join(or_parts) + ")")
        params.extend(or_params)

    if name_query:
        clauses.append("s.path LIKE ?")
        params.append(f"%{name_query}%")

    if min_rating > 0:
        clauses.append("s.rating >= ?")
        params.append(min_rating)

    if min_bpm > 0:
        clauses.append("s.bpm IS NOT NULL AND s.bpm >= ?")
        params.append(min_bpm)

    if max_bpm > 0:
        clauses.append("s.bpm IS NOT NULL AND s.bpm <= ?")
        params.append(max_bpm)

    if key_filter:
        clauses.append("s.key = ?")
        params.append(key_filter)

    if project_id is not None:
        clauses.append(
            "s.id IN (SELECT sample_id FROM project_samples WHERE project_id = ?)"
        )
        params.append(project_id)

    where = " AND ".join(clauses) if clauses else "1"

    # If tag filters are active, add a HAVING clause approach
    tag_having = ""
    if tags:
        placeholders = ",".join("?" * len(tags))
        tag_having = f"""
            AND s.id IN (
                SELECT st.sample_id FROM sample_tags st
                JOIN tags t ON t.id = st.tag_id
                WHERE t.name IN ({placeholders})
                GROUP BY st.sample_id
                HAVING COUNT(DISTINCT t.name) = {len(tags)}
            )
        """
        params.extend(tags)

    sql = f"""
        SELECT s.*, GROUP_CONCAT(t.name, ', ') AS tag_list
        FROM samples s
        LEFT JOIN sample_tags st ON st.sample_id = s.id
        LEFT JOIN tags t         ON t.id = st.tag_id
        WHERE {where} {tag_having}
        GROUP BY s.id
        ORDER BY s.path
    """
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()

    # Convert stored (relative) paths back to absolute for callers
    result = []
    for row in rows:
        d = dict(row)
        d["path"] = _abs(d["path"])
        result.append(d)
    return result


# ── Health check ─────────────────────────────────────────────────────────────

def get_health_report() -> dict:
    """Scan the active library and return a dict with five issue lists.

    Keys
    ----
    total       int          – total number of samples in the library
    missing     [str]        – absolute paths whose file no longer exists on disk
    duplicates  [dict]       – [{"filename": str, "paths": [str, ...]}], groups > 1
    untagged    [str]        – absolute paths with no tags at all
    no_bpm      [str]        – absolute paths where bpm IS NULL
    no_rating   [str]        – absolute paths where rating = 0 (unrated)
    """
    with get_connection() as conn:
        all_rows = conn.execute(
            "SELECT id, path, rating, bpm FROM samples ORDER BY path"
        ).fetchall()
        tagged_ids = {
            r["sample_id"]
            for r in conn.execute(
                "SELECT DISTINCT sample_id FROM sample_tags"
            ).fetchall()
        }

    missing:   list = []
    dup_map:   dict = {}   # basename → [abs_path]
    untagged:  list = []
    no_bpm:    list = []
    no_rating: list = []

    for row in all_rows:
        abs_path = _abs(row["path"])
        basename = os.path.basename(abs_path)

        if not Path(abs_path).exists():
            missing.append(abs_path)

        dup_map.setdefault(basename, []).append(abs_path)

        if row["id"] not in tagged_ids:
            untagged.append(abs_path)

        if row["bpm"] is None:
            no_bpm.append(abs_path)

        if not row["rating"]:
            no_rating.append(abs_path)

    duplicates = sorted(
        [{"filename": fn, "paths": paths} for fn, paths in dup_map.items() if len(paths) > 1],
        key=lambda d: d["filename"].lower(),
    )

    return {
        "total":      len(all_rows),
        "missing":    missing,
        "duplicates": duplicates,
        "untagged":   untagged,
        "no_bpm":     no_bpm,
        "no_rating":  no_rating,
    }


def remove_sample(path: str):
    """Permanently delete a sample and its tags/project memberships from the DB."""
    rel = _rel(path)
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM samples WHERE path = ?", (rel,)
        ).fetchone()
        if not row:
            return
        sid = row["id"]
        conn.execute("DELETE FROM sample_tags    WHERE sample_id = ?", (sid,))
        conn.execute("DELETE FROM project_samples WHERE sample_id = ?", (sid,))
        conn.execute("DELETE FROM samples         WHERE id = ?",        (sid,))


# ── Library data export ───────────────────────────────────────────────────────

def get_all_samples_for_export() -> list:
    """Return every sample in the active library as a list of plain dicts.

    Paths are absolute.  Tags are a list of strings.  Numeric fields that
    have not yet been analysed are None.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, path, rating, bpm, key, notes FROM samples ORDER BY path"
        ).fetchall()

        # Fetch all tag assignments in one query then group in Python.
        tag_rows = conn.execute("""
            SELECT st.sample_id, t.name
            FROM sample_tags st
            JOIN tags t ON t.id = st.tag_id
            ORDER BY st.sample_id, t.name
        """).fetchall()

    tags_by_id: dict = {}
    for tr in tag_rows:
        tags_by_id.setdefault(tr["sample_id"], []).append(tr["name"])

    result = []
    for row in rows:
        abs_path = _abs(row["path"])
        result.append({
            "path":     abs_path,
            "filename": os.path.basename(abs_path),
            "rating":   row["rating"] or 0,
            "bpm":      row["bpm"],          # float or None
            "key":      row["key"] or "",
            "tags":     tags_by_id.get(row["id"], []),
            "notes":    row["notes"] or "",
        })
    return result


# ── File copy export ──────────────────────────────────────────────────────────

def export_samples(paths: list, dest_folder: str):
    dest = Path(dest_folder)
    dest.mkdir(parents=True, exist_ok=True)
    copied, skipped = 0, 0
    for p in paths:
        src = Path(p)
        if src.exists():
            shutil.copy2(src, dest / src.name)
            copied += 1
        else:
            skipped += 1
    return copied, skipped
