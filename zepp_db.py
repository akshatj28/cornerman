"""Zepp storage. Six tables alongside the Hevy ones in cornerman.db.

Conventions carried over from the Hevy side:

  - Workouts are keyed (source, activity_id) so a backfilled row and a
    live-synced row are indistinguishable downstream.
  - Every table keeps the raw payload. A decoder bug should cost a re-parse,
    not a re-download -- the backfill is ~1,100 paced requests.
  - Writes are INSERT OR REPLACE, so re-running a backfill is safe.
"""
import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "cornerman.db"

# Section 6 of the brief. sport_title is empty and sport_mode is 0 on every
# record, so these readings are inferred from field signatures, not labels from
# Zepp. detail_free means the session is real but carries no set/rep detail.
SPORT_TYPES = {
    1:  ("Outdoor running",           0, 0),
    52: ("Indoor gym / strength",     1, 0),
    8:  ("Treadmill intervals",       0, 0),
    6:  ("Run logged under wrong mode", 0, 0),
    24: ("One-off outdoor activity",  0, 1),
    12: ("Left running by accident",  0, 1),
    57: ("Sensor barely sampled",     0, 1),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS zepp_workout (
    source          TEXT    NOT NULL,
    activity_id     TEXT    NOT NULL,
    start_time      TEXT,
    type            INTEGER,
    type_label      TEXT,
    distance_m      REAL,
    duration_s      INTEGER,
    calorie         REAL,
    avg_heart_rate  INTEGER,
    max_heart_rate  INTEGER,
    total_step      INTEGER,
    avg_pace        REAL,
    vo2_max         REAL,
    te              REAL,
    anaerobic_te    REAL,
    exercise_load   REAL,
    rpe             REAL,
    deviceid        TEXT,
    sn              TEXT,
    detail_free     INTEGER NOT NULL DEFAULT 0,
    suspect         INTEGER NOT NULL DEFAULT 0,
    has_streams     INTEGER NOT NULL DEFAULT 0,
    raw_json        TEXT    NOT NULL,
    raw_detail      TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (source, activity_id)
);

CREATE INDEX IF NOT EXISTS ix_zepp_workout_start ON zepp_workout(start_time);
CREATE INDEX IF NOT EXISTS ix_zepp_workout_type  ON zepp_workout(type);

CREATE TABLE IF NOT EXISTS zepp_workout_hr (
    source      TEXT    NOT NULL,
    activity_id TEXT    NOT NULL,
    t_offset_s  INTEGER NOT NULL,
    bpm         INTEGER NOT NULL,
    PRIMARY KEY (source, activity_id, t_offset_s)
);

CREATE TABLE IF NOT EXISTS zepp_daily_hr (
    date    TEXT    NOT NULL,
    minute  INTEGER NOT NULL,
    bpm     INTEGER NOT NULL,
    PRIMARY KEY (date, minute)
);

CREATE TABLE IF NOT EXISTS zepp_sleep (
    date        TEXT PRIMARY KEY,
    start_ts    INTEGER,
    end_ts      INTEGER,
    deep_min    INTEGER,
    light_min   INTEGER,
    wake_min    INTEGER,
    wake_count  INTEGER,
    total_min   INTEGER,
    stages_json TEXT,
    raw_summary TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS zepp_daily (
    date            TEXT PRIMARY KEY,
    steps           INTEGER,
    distance_m      REAL,
    calories        REAL,
    run_calories    REAL,
    hr_sample_count INTEGER,
    hr_min          INTEGER,
    hr_max          INTEGER,
    hr_mean         REAL,
    rhr_overnight   REAL,
    raw_summary     TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sync_state (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _num(v):
    """Coerce a Zepp field to a number, or None.

    The API is inconsistent about types: on one real record trackid, dis,
    run_time, calorie, avg_heart_rate and avg_pace all arrive as strings while
    type, max_heart_rate and total_step arrive as ints. Anything doing
    arithmetic on these fields has to coerce first or it hits a TypeError on the
    first live row.
    """
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v):
    f = _num(v)
    return None if f is None else int(f)


def _opt(v):
    """Same as _num, but -1 is Zepp's "not measured" sentinel, not a value.

    Storing -1 would let a missing VO2_max drag an average downwards.
    """
    f = _num(v)
    return None if f is None or f == -1 else f


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate(conn):
    """Additive column migrations. SQLite has no ADD COLUMN IF NOT EXISTS."""
    cols = set(r[1] for r in conn.execute("PRAGMA table_info(zepp_sleep)"))
    for name in ("rem_min", "awake_min", "reported_dp", "reported_lb"):
        if name not in cols:
            conn.execute("ALTER TABLE zepp_sleep ADD COLUMN " + name + " INTEGER")


def init():
    conn = connect()
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    conn.close()


def classify(rec):
    """Label a workout and decide whether it should be trusted.

    Two independent reasons to mark a row suspect: its type is one the brief
    calls out (12, 24, 57), or its heart rate never rose meaningfully above
    resting. A 162-minute "workout" at HR 74 would otherwise corrupt any
    training-load figure.
    """
    t = _int(rec.get("type"))
    label, detail_free, suspect = SPORT_TYPES.get(t, ("Unknown type " + str(t), 0, 0))
    avg = _num(rec.get("avg_heart_rate")) or 0
    mx = _num(rec.get("max_heart_rate")) or 0
    if mx == 0 or mx < 90 or (mx - avg) < 5:
        suspect = 1
    return label, detail_free, suspect


def save_workout(rec, source, activity_id, start_time, raw_json=None):
    """Upsert one history record, preserving anything the detail pass wrote.

    Deliberately not INSERT OR REPLACE: that is DELETE-then-INSERT in SQLite, so
    columns absent from the statement revert to their defaults. It would wipe
    raw_detail and has_streams on every run, which silently defeats resume and
    re-downloads every stream.
    """
    label, detail_free, suspect = classify(rec)
    row = {
        "source": source,
        "activity_id": str(activity_id),
        "start_time": start_time,
        "type": _int(rec.get("type")),
        "type_label": label,
        "distance_m": _num(rec.get("dis")),
        "duration_s": _int(rec.get("run_time")),
        "calorie": _num(rec.get("calorie")),
        "avg_heart_rate": _int(rec.get("avg_heart_rate")),
        "max_heart_rate": _int(rec.get("max_heart_rate")),
        "total_step": _int(rec.get("total_step")),
        "avg_pace": _num(rec.get("avg_pace")),
        "vo2_max": _opt(rec.get("VO2_max")),
        "te": _opt(rec.get("te")),
        "anaerobic_te": _opt(rec.get("anaerobic_te")),
        "exercise_load": _opt(rec.get("exercise_load")),
        "rpe": _opt(rec.get("rpe")),
        "deviceid": rec.get("deviceid"),
        "sn": rec.get("sn"),
        "detail_free": detail_free,
        "suspect": suspect,
        "raw_json": raw_json if raw_json is not None else json.dumps(rec, default=str),
    }
    cols = ", ".join(row)
    marks = ", ".join(":" + k for k in row)
    updates = ", ".join(k + " = excluded." + k for k in row
                        if k not in ("source", "activity_id"))
    conn = connect()
    conn.execute("INSERT INTO zepp_workout (" + cols + ") VALUES (" + marks + ") "
                 "ON CONFLICT(source, activity_id) DO UPDATE SET " + updates, row)
    conn.commit()
    conn.close()
    return row["activity_id"]


def save_workout_detail(source, activity_id, raw_detail, hr_samples):
    """Store a workout's raw detail payload and its expanded HR stream."""
    conn = connect()
    conn.execute("UPDATE zepp_workout SET raw_detail = ?, has_streams = ? "
                 "WHERE source = ? AND activity_id = ?",
                 (raw_detail, 1 if hr_samples else 0, source, str(activity_id)))
    if hr_samples:
        conn.executemany(
            "INSERT OR REPLACE INTO zepp_workout_hr "
            "(source, activity_id, t_offset_s, bpm) VALUES (?,?,?,?)",
            [(source, str(activity_id), t, v) for t, v in hr_samples])
    conn.commit()
    conn.close()
    return len(hr_samples or [])


def save_daily_hr(date, samples):
    if not samples:
        return 0
    conn = connect()
    conn.executemany("INSERT OR REPLACE INTO zepp_daily_hr (date, minute, bpm) "
                     "VALUES (?,?,?)",
                     [(date, m, v) for m, v in samples])
    conn.commit()
    conn.close()
    return len(samples)


def save_daily(date, stp, hr, raw_summary):
    """One day of activity plus the HR stats the analysis layer needs."""
    stp = stp or {}
    hr = hr or {}
    row = (date, stp.get("ttl"), stp.get("dis"), stp.get("cal"), stp.get("runCal"),
           hr.get("count"), hr.get("min"), hr.get("max"), hr.get("mean"),
           hr.get("rhr_overnight"), raw_summary)
    conn = connect()
    conn.execute("INSERT OR REPLACE INTO zepp_daily (date, steps, distance_m, "
                 "calories, run_calories, hr_sample_count, hr_min, hr_max, "
                 "hr_mean, rhr_overnight, raw_summary) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?)", row)
    conn.commit()
    conn.close()
    return date


def save_sleep(date, slp, raw_summary):
    """One night. Phase minutes come from the stages, not from dp + lb.

    The reported dp and lb are kept alongside so the two can be compared: dp
    tracks the summed mode-5 stages to within a few percent, while lb does not
    correspond to light sleep at all and is stored uninterpreted.
    """
    import zepp_decode
    slp = slp or {}
    stages = slp.get("stage") or []
    s = zepp_decode.sleep_summary(stages)
    row = (date, _int(slp.get("st")), _int(slp.get("ed")),
           s["deep_min"], s["light_min"], s["rem_min"], s["awake_min"],
           _int(slp.get("wk")), _int(slp.get("wc")), s["total_min"],
           _int(slp.get("dp")), _int(slp.get("lb")),
           json.dumps(stages, default=str), raw_summary)
    conn = connect()
    conn.execute("INSERT OR REPLACE INTO zepp_sleep (date, start_ts, end_ts, "
                 "deep_min, light_min, rem_min, awake_min, wake_min, "
                 "wake_count, total_min, reported_dp, reported_lb, "
                 "stages_json, raw_summary) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
    conn.commit()
    conn.close()
    return date


def get_state(key, default=None):
    conn = connect()
    r = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
    conn.close()
    return r["value"] if r else default


def set_state(key, value):
    conn = connect()
    conn.execute("INSERT OR REPLACE INTO sync_state (key, value, updated_at) "
                 "VALUES (?,?,datetime('now'))", (key, str(value)))
    conn.commit()
    conn.close()
    return value


def known_activity_ids():
    conn = connect()
    rows = conn.execute("SELECT activity_id FROM zepp_workout").fetchall()
    conn.close()
    return set(r["activity_id"] for r in rows)


def ids_with_detail():
    conn = connect()
    rows = conn.execute("SELECT activity_id FROM zepp_workout "
                        "WHERE raw_detail IS NOT NULL").fetchall()
    conn.close()
    return set(r["activity_id"] for r in rows)


def dates_with_hr():
    conn = connect()
    rows = conn.execute("SELECT DISTINCT date FROM zepp_daily_hr").fetchall()
    conn.close()
    return set(r["date"] for r in rows)


def report():
    """Recovery report. Counts, ranges, and coverage per the brief's task 1."""
    conn = connect()
    out = []
    for t in ("zepp_workout", "zepp_workout_hr", "zepp_daily_hr",
              "zepp_sleep", "zepp_daily", "sync_state"):
        n = conn.execute("SELECT COUNT(*) FROM " + t).fetchone()[0]
        out.append((t, n))
    q = conn.execute("SELECT MIN(start_time), MAX(start_time), "
                     "SUM(has_streams), SUM(suspect), SUM(detail_free) "
                     "FROM zepp_workout").fetchone()
    days = conn.execute("SELECT MIN(date), MAX(date), COUNT(*) "
                        "FROM zepp_daily").fetchone()
    hrdays = conn.execute("SELECT COUNT(DISTINCT date) FROM zepp_daily_hr").fetchone()[0]
    sleep = conn.execute("SELECT MIN(date), MAX(date), COUNT(*) "
                         "FROM zepp_sleep").fetchone()
    bytype = conn.execute("SELECT type, type_label, COUNT(*) n FROM zepp_workout "
                          "GROUP BY type ORDER BY n DESC").fetchall()
    conn.close()

    print("Row counts")
    for t, n in out:
        print("  %-18s %s" % (t, n))
    print()
    print("Workouts     %s -> %s" % (q[0], q[1]))
    print("  with streams %s   suspect %s   detail-free %s" % (q[2], q[3], q[4]))
    for r in bytype:
        print("    type %-4s %-28s n=%s" % (r["type"], r["type_label"], r["n"]))
    print()
    print("Daily        %s -> %s  (%s days)" % (days[0], days[1], days[2]))
    print("Days with HR %s" % hrdays)
    print("Sleep        %s -> %s  (%s nights)" % (sleep[0], sleep[1], sleep[2]))


if __name__ == "__main__":
    init()
    conn = connect()
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'zepp%' "
        "OR name = 'sync_state' ORDER BY name")]
    conn.close()
    print("Zepp tables in " + str(DB_PATH) + ":")
    for n in names:
        print("  " + n)
