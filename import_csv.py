"""Backfill full Hevy history from the CSV export."""
import csv
import sys
from datetime import datetime
import db

DATE_FORMATS = ("%d %b %Y, %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")


def parse_dt(s):
    s = (s or "").strip()
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    return None


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def run(path):
    db.init()
    conn = db.connect()
    cur = conn.cursor()
    seen = {}
    nsets = 0
    f = open(path, newline="", encoding="utf-8-sig")
    reader = csv.DictReader(f)
    for row in reader:
        title = (row.get("title") or "").strip()
        start = (row.get("start_time") or "").strip()
        ex = (row.get("exercise_title") or "").strip()
        if not title or not start or not ex:
            continue
        dt = parse_dt(start)
        if not dt:
            continue
        fp = title + "|" + dt.isoformat()
        if fp not in seen:
            cur.execute("SELECT id FROM workouts WHERE fingerprint = ?", (fp,))
            r = cur.fetchone()
            if r:
                seen[fp] = r["id"]
            else:
                q = "INSERT INTO workouts (source, fingerprint, title, start_time) VALUES (?,?,?,?)"
                cur.execute(q, ("hevy_csv", fp, title, dt.isoformat()))
                seen[fp] = cur.lastrowid
        wid = seen[fp]
        si = row.get("set_index") or 0
        try:
            si = int(float(si))
        except (TypeError, ValueError):
            si = 0
        reps = row.get("reps")
        try:
            reps = int(float(reps))
        except (TypeError, ValueError):
            reps = None
        w = num(row.get("weight_kg"))
        kind = row.get("set_type")
        q2 = "INSERT OR REPLACE INTO sets (workout_id, exercise, set_index, kind, weight_kg, reps, duration_s) VALUES (?,?,?,?,?,?,?)"
        cur.execute(q2, (wid, ex, si, kind, w, reps, None))
        nsets = nsets + 1
    f.close()
    conn.commit()
    conn.close()
    print("Imported " + str(len(seen)) + " workouts, " + str(nsets) + " sets")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "workout_data.csv")
