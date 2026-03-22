from __future__ import annotations

import sqlite3
import shutil
import os
from pathlib import Path
from typing import Optional


DB_PATH = Path.home() / ".sample_organiser" / "library.db"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
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


# ── Samples ──────────────────────────────────────────────────────────────────

def upsert_sample(path: str) -> int:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO samples (path) VALUES (?)", (path,)
        )
        row = conn.execute(
            "SELECT id FROM samples WHERE path = ?", (path,)
        ).fetchone()
        return row["id"]


def set_rating(path: str, rating: int):
    with get_connection() as conn:
        conn.execute(
            "UPDATE samples SET rating = ? WHERE path = ?", (rating, path)
        )


def set_bpm(path: str, bpm: float):
    """Store a detected BPM (pass 0.0 to clear)."""
    value = bpm if bpm > 0 else None
    with get_connection() as conn:
        conn.execute(
            "UPDATE samples SET bpm = ? WHERE path = ?", (value, path)
        )


def set_key(path: str, key: str):
    """Store a detected musical key (pass '' to clear)."""
    value = key if key else None
    with get_connection() as conn:
        conn.execute(
            "UPDATE samples SET key = ? WHERE path = ?", (value, path)
        )


def set_notes(path: str, notes: str):
    with get_connection() as conn:
        conn.execute(
            "UPDATE samples SET notes = ? WHERE path = ?", (notes, path)
        )


def get_sample(path: str) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM samples WHERE path = ?", (path,)
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
        """, (path, tag))


def tags_for_sample(path: str) -> "list[str]":
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT t.name FROM tags t
            JOIN sample_tags st ON st.tag_id = t.id
            JOIN samples s      ON s.id = st.sample_id
            WHERE s.path = ?
            ORDER BY t.name
        """, (path,)).fetchall()
        return [r["name"] for r in rows]


# ── Projects ─────────────────────────────────────────────────────────────────

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
        """, (project_id, path))


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
) -> list:
    """Return rows for samples matching all active filters.

    When *project_id* is set the folder prefix filter is skipped so the project
    can contain samples from any location on disk.
    """
    clauses: list = []
    params:  list = []

    if folder and project_id is None:
        clauses.append("s.path LIKE ?")
        params.append(f"{folder}%")

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
        return conn.execute(sql, params).fetchall()


# ── Export ────────────────────────────────────────────────────────────────────

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
