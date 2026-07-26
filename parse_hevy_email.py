"""Parse Hevy share-text as forwarded by the iOS shortcut."""
from __future__ import annotations

import re
from datetime import datetime

RE_WEIGHTED = re.compile(r"^Set\s+(\d+):\s*([\d.]+)\s*kg\s*[x×]\s*(\d+)", re.IGNORECASE)
RE_REPS_ONLY = re.compile(r"^Set\s+(\d+):\s*(\d+)\s*reps?", re.IGNORECASE)
RE_DURATION = re.compile(
    r"^Set\s+(\d+):\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*min)?\s*(?:(\d+)\s*s)?\s*$",
    re.IGNORECASE,
)
RE_ANY_SET = re.compile(r"^Set\s+\d+\s*:", re.IGNORECASE)

DATE_FORMATS = (
    "%A, %b %d, %Y at %I:%M%p",
    "%A, %B %d, %Y at %I:%M%p",
    "%a, %b %d, %Y at %I:%M%p",
)


def _parse_dt(line: str):
    cleaned = line.strip().replace("\u202f", " ")
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


def parse_workout(text: str):
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    lines = [ln for ln in lines
             if not ln.startswith("@hevyapp") and not ln.startswith("http")]
    if len(lines) < 3:
        return None

    title = lines[0].rstrip("!").strip()
    start_dt = _parse_dt(lines[1])
    body = lines[2:] if start_dt else lines[1:]

    exercises = []
    current = None
    for ln in body:
        if RE_ANY_SET.match(ln):
            if current is None:
                continue
            s = _parse_set(ln)
            if s:
                current["sets"].append(s)
        else:
            current = {"name": ln, "sets": []}
            exercises.append(current)

    exercises = [e for e in exercises if e["sets"]]
    if not exercises:
        return None

    return {
        "title": title,
        "start_time": start_dt.isoformat() if start_dt else None,
        "exercises": exercises,
    }


def _parse_set(line: str):
    m = RE_WEIGHTED.match(line)
    if m:
        return {"index": int(m.group(1)), "weight_kg": float(m.group(2)),
                "reps": int(m.group(3)), "kind": "weighted"}
    m = RE_REPS_ONLY.match(line)
    if m:
        return {"index": int(m.group(1)), "weight_kg": None,
                "reps": int(m.group(2)), "kind": "bodyweight"}
    m = RE_DURATION.match(line)
    if m and any(m.group(i) for i in (2, 3, 4)):
        secs = (int(m.group(2) or 0) * 3600 + int(m.group(3) or 0) * 60
                + int(m.group(4) or 0))
        return {"index": int(m.group(1)), "weight_kg": None, "reps": None,
                "duration_s": secs, "kind": "duration"}
    return None
