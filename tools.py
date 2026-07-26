"""Tools the coach can call. Training data is read-only."""
import sqlite3
import json
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "cornerman.db"
MAX_ROWS = 200


def _ro_conn():
    uri = "file:" + str(DB_PATH) + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def run_sql(query):
    q = (query or "").strip().rstrip(";")
    low = q.lower()
    if not low.startswith("select") and not low.startswith("with"):
        return {"error": "Only SELECT queries are allowed."}
    conn = _ro_conn()
    try:
        rows = conn.execute(q).fetchmany(MAX_ROWS)
        out = [dict(r) for r in rows]
        return {"rows": out, "count": len(out)}
    except sqlite3.Error as e:
        return {"error": str(e)}
    finally:
        conn.close()


def init_notes():
    conn = sqlite3.connect(DB_PATH)
    q = ("CREATE TABLE IF NOT EXISTS coach_notes ("
         "id INTEGER PRIMARY KEY AUTOINCREMENT, "
         "kind TEXT, note TEXT NOT NULL, "
         "created_at TEXT NOT NULL DEFAULT (datetime('now')))")
    conn.execute(q)
    conn.commit()
    conn.close()


def remember(note, kind="observation"):
    init_notes()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO coach_notes (kind, note) VALUES (?,?)", (kind, note))
    conn.commit()
    conn.close()
    return {"saved": note}


def recall(limit=20):
    init_notes()
    conn = _ro_conn()
    q = "SELECT kind, note, created_at FROM coach_notes ORDER BY id DESC LIMIT ?"
    rows = conn.execute(q, (limit,)).fetchall()
    conn.close()
    return {"notes": [dict(r) for r in rows]}


def schema():
    conn = _ro_conn()
    q = "SELECT name, sql FROM sqlite_master WHERE type='table'"
    rows = conn.execute(q).fetchall()
    conn.close()
    return {"tables": [dict(r) for r in rows]}


def recall(limit=20):
    init_notes()
    conn = _ro_conn()
    q = "SELECT kind, note, created_at FROM coach_notes ORDER BY id DESC LIMIT ?"
    rows = conn.execute(q, (limit,)).fetchall()
    conn.close()
    return {"notes": [dict(r) for r in rows]}


def schema():
    conn = _ro_conn()
    q = "SELECT name, sql FROM sqlite_master WHERE type='table'"
    rows = conn.execute(q).fetchall()
    conn.close()
    return {"tables": [dict(r) for r in rows]}


def trajectory(exercise):
    import analysis
    return analysis.trajectory(exercise)


def project_goal(exercise, target_kg, deadline):
    import analysis
    return analysis.project_goal(exercise, target_kg, deadline)


if __name__ == "__main__":
    init_notes()
    print("Tables:", [t["name"] for t in schema()["tables"]])
    print("Count:", run_sql("SELECT COUNT(*) AS n FROM sets"))
    print("Blocked:", run_sql("DROP TABLE workouts"))
    c = _ro_conn()
    try:
        c.execute("DELETE FROM workouts")
        print("ENGINE LOCK FAILED")
    except sqlite3.Error as e:
        print("Engine lock holds:", e)
    c.close()
