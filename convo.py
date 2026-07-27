"""Email conversation history."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "cornerman.db"


def init():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    q = ("CREATE TABLE IF NOT EXISTS messages ("
         "id INTEGER PRIMARY KEY AUTOINCREMENT, "
         "direction TEXT NOT NULL, "
         "subject TEXT, body TEXT NOT NULL, "
         "msg_id TEXT, created_at TEXT NOT NULL "
         "DEFAULT (datetime('now')))")
    conn.execute(q)
    conn.commit()
    conn.close()


def log(direction, body, subject=None, msg_id=None):
    init()
    conn = sqlite3.connect(DB_PATH, timeout=30)
    q = ("INSERT INTO messages (direction, subject, body, msg_id) "
         "VALUES (?,?,?,?)")
    conn.execute(q, (direction, subject, body, msg_id))
    conn.commit()
    conn.close()


def recent(n=30):
    init()
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    q = ("SELECT direction, body, created_at FROM messages "
         "ORDER BY id DESC LIMIT ?")
    rows = conn.execute(q, (n,)).fetchall()
    conn.close()
    return list(reversed([dict(r) for r in rows]))


def as_text(n=30):
    lines = []
    for m in recent(n):
        who = "YOU" if m["direction"] == "in" else "COACH"
        when = (m["created_at"] or "")[:16]
        lines.append("[" + when + "] " + who + ": " + m["body"].strip())
    if not lines:
        return "(no previous conversation)"
    return "\n\n".join(lines)


if __name__ == "__main__":
    init()
    print("Messages stored:", len(recent(1000)))
    print()
    print(as_text(10))


def last_msg_id():
    init()
    conn = sqlite3.connect(DB_PATH, timeout=30)
    q = ("SELECT msg_id FROM messages WHERE msg_id IS NOT NULL "
         "ORDER BY id DESC LIMIT 1")
    r = conn.execute(q).fetchone()
    conn.close()
    return r[0] if r else None


def root_msg_id():
    init()
    conn = sqlite3.connect(DB_PATH, timeout=30)
    q = ("SELECT msg_id FROM messages WHERE msg_id IS NOT NULL "
         "ORDER BY id ASC LIMIT 1")
    r = conn.execute(q).fetchone()
    conn.close()
    return r[0] if r else None
