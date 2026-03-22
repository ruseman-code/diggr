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
                notes   TEXT DEFAULT ''
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

            CREATE INDEX IF NOT EXISTS idx_samples_path ON samples(path);
            CREATE INDEX IF NOT EXISTS idx_sample_tags_sample ON sample_tags(sample_id);
        """)


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


# ── Search / filter ───────────────────────────────────────────────────────────

def search_samples(
    folder: str,
    name_query: str = "",
    tags: Optional[list] = None,
    min_rating: int = 0,
) -> list:
    """Return rows for samples inside *folder* matching all filters."""
    clauses = ["s.path LIKE ?"]
    params: list = [f"{folder}%"]

    if name_query:
        clauses.append("s.path LIKE ?")
        params.append(f"%{name_query}%")

    if min_rating > 0:
        clauses.append("s.rating >= ?")
        params.append(min_rating)

    where = " AND ".join(clauses)

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
