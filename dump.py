"""Compact full-database dump for single-call prompting."""
import db


def _rows():
    conn = db.connect()
    q = ("SELECT w.start_time AS st, w.title AS title, s.exercise AS ex, "
         "s.weight_kg AS wt, s.reps AS r, s.kind AS k "
         "FROM workouts w JOIN sets s ON w.id = s.workout_id "
         "ORDER BY w.start_time, s.exercise, s.set_index")
    rows = conn.execute(q).fetchall()
    conn.close()
    return rows


def _fmt(r):
    if r["wt"] and r["r"]:
        return str(r["wt"]) + "x" + str(r["r"])
    if r["r"]:
        return str(r["r"]) + "reps"
    return str(r["k"] or "?")


def as_text():
    groups = {}
    order = []
    for r in _rows():
        key = (r["st"] or "", r["title"] or "")
        if key not in groups:
            groups[key] = {}
            order.append(key)
        ex = r["ex"]
        if ex not in groups[key]:
            groups[key][ex] = []
        groups[key][ex].append(_fmt(r))
    lines = []
    for key in sorted(order):
        lines.append(key[0][:16] + "  " + key[1])
        for ex in groups[key]:
            lines.append("   " + ex + ": " + ", ".join(groups[key][ex]))
    return "\n".join(lines)


if __name__ == "__main__":
    t = as_text()
    print(t[:1200])
    print("...")
    print()
    print("chars:", len(t), " approx tokens:", len(t) // 4)
