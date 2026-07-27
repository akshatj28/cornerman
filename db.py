"""SQLite storage for Cornerman."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "cornerman.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS workouts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    fingerprint TEXT NOT NULL UNIQUE,
    title       TEXT,
    start_time  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sets (
    workout_id  INTEGER NOT NULL,
    exercise    TEXT NOT NULL,
    set_index   INTEGER NOT NULL,
    kind        TEXT,
    weight_kg   REAL,
    reps        INTEGER,
    duration_s  INTEGER,
    PRIMARY KEY (workout_id, exercise, set_index),
    FOREIGN KEY (workout_id) REFERENCES workouts(id)
);

CREATE TABLE IF NOT EXISTS goals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,
    exercise     TEXT,
    target_value REAL,
    deadline     TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    active       INTEGER NOT NULL DEFAULT 1
);
"""


def connect():
    # 30s to match zepp_db. The Zepp sync writes in short per-day bursts, so
    # the default 5s would very likely have been fine -- but polling three
    # times as often means three times as many chances to find out.
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init():
    conn = connect()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def save_workout(parsed, source="hevy_email"):
    """Insert a parsed workout. Returns workout id, or None if already stored."""
    fingerprint = f"{parsed['title']}|{parsed['start_time']}"
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT id FROM workouts WHERE fingerprint = ?", (fingerprint,))
    row = cur.fetchone()
    if row:
        conn.close()
        return None

    cur.execute(
        "INSERT INTO workouts (source, fingerprint, title, start_time) VALUES (?,?,?,?)",
        (source, fingerprint, parsed["title"], parsed["start_time"]),
    )
    wid = cur.lastrowid
    for ex in parsed["exercises"]:
        for s in ex["sets"]:
            cur.execute(
                "INSERT OR REPLACE INTO sets "
                "(workout_id, exercise, set_index, kind, weight_kg, reps, duration_s) "
                "VALUES (?,?,?,?,?,?,?)",
                (wid, ex["name"], s["index"], s.get("kind"),
                 s.get("weight_kg"), s.get("reps"), s.get("duration_s")),
            )
    conn.commit()
    conn.close()
    return wid


if __name__ == "__main__":
    init()
    print(f"Database ready at {DB_PATH}")

