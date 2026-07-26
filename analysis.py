"""Deterministic strength analysis."""
import statistics
from datetime import datetime, date
import db


def epley_1rm(weight_kg, reps):
    if not weight_kg or not reps or reps < 1:
        return 0.0
    if reps == 1:
        return float(weight_kg)
    return weight_kg * (1 + reps / 30.0)


def _parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def session_e1rms(exercise):
    conn = db.connect()
    q = ("SELECT w.start_time AS start_time, s.weight_kg AS weight_kg, "
         "s.reps AS reps FROM sets s "
         "JOIN workouts w ON w.id = s.workout_id "
         "WHERE lower(s.exercise) = lower(?) "
         "AND s.weight_kg IS NOT NULL AND s.reps IS NOT NULL")
    rows = conn.execute(q, (exercise,)).fetchall()
    conn.close()

    per_day = {}
    for r in rows:
        dt = _parse_dt(r["start_time"])
        if not dt:
            continue
        e = epley_1rm(r["weight_kg"], int(r["reps"]))
        d = dt.date()
        best = per_day.get(d)
        if best is None or e > best["e1rm"]:
            per_day[d] = {"date": d, "e1rm": round(e, 1),
                          "top_weight": r["weight_kg"],
                          "top_reps": int(r["reps"])}

    sessions = [per_day[d] for d in sorted(per_day)]
    return _drop_outliers(sessions)


def _drop_outliers(sessions):
    if len(sessions) < 4:
        return sessions
    med = statistics.median(s["e1rm"] for s in sessions)
    return [s for s in sessions if s["e1rm"] >= 0.6 * med]


def _linear_fit(xs, ys):
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return num / denom


def trajectory(exercise):
    sessions = session_e1rms(exercise)
    if not sessions:
        return {"exercise": exercise, "sessions": 0}
    origin = sessions[0]["date"]
    xs = [(s["date"] - origin).days for s in sessions]
    ys = [s["e1rm"] for s in sessions]
    slope = _linear_fit(xs, ys)
    current = sessions[-1]
    change = None
    for s in reversed(sessions[:-1]):
        if (current["date"] - s["date"]).days >= 10:
            change = round(current["e1rm"] - s["e1rm"], 1)
            break
    out = {}
    out["exercise"] = exercise
    out["sessions"] = len(sessions)
    out["first_date"] = origin.isoformat()
    out["current_e1rm"] = current["e1rm"]
    out["current_date"] = current["date"].isoformat()
    out["top_set"] = str(current["top_weight"]) + "kg"
    out["top_reps"] = current["top_reps"]
    out["kg_per_week"] = round(slope * 7, 3)
    out["change_vs_2wk"] = change
    return out


def project_goal(exercise, target_kg, deadline_iso):
    t = trajectory(exercise)
    if t.get("sessions", 0) < 2:
        return {"status": "insufficient_data"}
    deadline = date.fromisoformat(deadline_iso)
    cur_date = date.fromisoformat(t["current_date"])
    weeks_left = max((deadline - cur_date).days / 7.0, 0.01)
    gap = target_kg - t["current_e1rm"]
    required = gap / weeks_left
    observed = t["kg_per_week"]
    projected = t["current_e1rm"]
    if observed > 0:
        projected = projected + observed * weeks_left
    g = {}
    g["status"] = "ok"
    g["exercise"] = exercise
    g["current_e1rm"] = t["current_e1rm"]
    g["target_kg"] = target_kg
    g["deadline"] = deadline_iso
    g["weeks_left"] = round(weeks_left, 1)
    g["gap_kg"] = round(gap, 1)
    g["required_per_week"] = round(required, 3)
    g["observed_per_week"] = observed
    g["projected_at_deadline"] = round(projected, 1)
    g["on_track"] = observed >= required and gap > 0
    return g


def list_exercises():
    conn = db.connect()
    q = ("SELECT s.exercise AS exercise, "
         "COUNT(DISTINCT s.workout_id) AS sessions "
         "FROM sets s WHERE s.weight_kg IS NOT NULL "
         "GROUP BY lower(s.exercise) ORDER BY sessions DESC")
    rows = conn.execute(q).fetchall()
    conn.close()
    return [(r["exercise"], r["sessions"]) for r in rows]


if __name__ == "__main__":
    import sys
    from pprint import pprint
    if len(sys.argv) > 1:
        pprint(trajectory(sys.argv[1]))
    else:
        print("Exercises in the database:")
        print()
        for name, n in list_exercises():
            print("  " + name.ljust(40) + str(n) + " session(s)")
