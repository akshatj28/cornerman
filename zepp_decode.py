"""Decoders for Zepp payloads. Pure functions, strict on malformed input.

Strict by design: the failure that cost an afternoon on this API was a success
code with an empty body, so silence is the enemy. Callers doing bulk work catch
per-item and keep going. Because every table stores raw_json, a decoder bug
later is a re-parse, not a re-download.
"""
import base64
import json

MINUTES_PER_DAY = 1440
OVERNIGHT_END = 300      # 05:00 -- the resting-HR window that survives the
                         # June 2025 sampling-density boundary


def day_hr(blob):
    """Minute-level heart rate for one day.

    Returns [(minute_of_day, bpm)], sentinels dropped. Byte index is the
    minute, so index 0 is 00:00 local.

    0 and 254 (0xFE) mean "no reading". The predicate also drops 255, which is
    how the values in the regression fixtures were measured -- do not widen it
    without re-checking them.
    """
    raw = base64.b64decode(blob + "=" * (-len(blob) % 4))
    if len(raw) > MINUTES_PER_DAY:
        raise ValueError("data_hr is " + str(len(raw))
                         + " bytes, more than a day; byte index would not be "
                           "a minute of day")
    return [(i, b) for i, b in enumerate(raw) if 0 < b < 254]


def summary_json(blob):
    """The `summary` field of a band_data day: base64 wrapping JSON.

    Returns the parsed object, whose interesting keys are stp (steps and
    activity), slp (sleep) and hr.
    """
    raw = base64.b64decode(blob + "=" * (-len(blob) % 4))
    return json.loads(raw.decode("utf-8", "replace"))


def hr_summary(samples):
    """Stats for one day of minute HR, shaped for the zepp_daily columns.

    rhr_overnight is the 00:00-05:00 mean. Use that for cross-year trends, not
    the daily mean: before June 2025 samples skew to waking hours and after it
    they include far more sleep, so a naive mean trends downward for reasons
    that have nothing to do with fitness.
    """
    if not samples:
        return {"count": 0, "min": None, "max": None,
                "mean": None, "rhr_overnight": None}
    bpm = [v for _, v in samples]
    night = [v for m, v in samples if m < OVERNIGHT_END]
    out = {}
    out["count"] = len(bpm)
    out["min"] = min(bpm)
    out["max"] = max(bpm)
    out["mean"] = round(sum(bpm) / len(bpm), 1)
    out["rhr_overnight"] = round(sum(night) / len(night), 1) if night else None
    return out


def undelta(s, n=1):
    """Expand a Zepp delta stream.

    Format is "t,v;t,v;..." where t is seconds since the previous sample and v
    is the change from the previous value. Both accumulate. With n=1 this
    returns [(t, v)]; higher n handles the wider records (gait,
    longitude_latitude) as [(t, v1, ... vn)].

    longitude_latitude still needs projecting to WGS84 after this -- the raw
    accumulated integers are not degrees.
    """
    out = []
    t = 0
    vals = [0] * n
    for pair in s.split(";"):
        if not pair:
            continue
        parts = pair.split(",")
        if len(parts) < n + 1:
            raise ValueError("stream record " + repr(pair) + " has "
                             + str(len(parts)) + " fields, expected "
                             + str(n + 1))
        t = t + int(parts[0])
        for i in range(n):
            vals[i] = vals[i] + int(parts[i + 1])
        out.append(tuple([t] + vals))
    return out


def stream_stats(samples):
    """Span and value stats for an expanded stream.

    span_s should equal the workout's run_time. If it does not, the stream and
    the summary disagree and one of them is wrong.
    """
    if not samples:
        return {"count": 0, "span_s": 0, "min": None, "max": None, "mean": None}
    vals = [r[1] for r in samples]
    out = {}
    out["count"] = len(samples)
    out["span_s"] = samples[-1][0]
    out["min"] = min(vals)
    out["max"] = max(vals)
    out["mean"] = round(sum(vals) / len(vals), 1)
    return out


def stage_durations(stages):
    """Total minutes per stage mode.

    start/stop are minutes of day, so a night that crosses midnight has
    stop < start. Those wrap forward rather than counting as negative time.
    """
    totals = {}
    for st in stages:
        start = st["start"]
        stop = st["stop"]
        mins = stop - start
        if mins < 0:
            mins = mins + MINUTES_PER_DAY
        mode = st.get("mode")
        totals[mode] = totals.get(mode, 0) + mins
    return totals


def match_stage_modes(slp):
    """Work out which stage mode means deep and which means light.

    The brief leaves these codes unconfirmed and says to determine them by
    matching summed durations against the reported fields rather than guessing.
    Returns the mapping plus the totals it reasoned from, so a wrong answer is
    visible instead of silently baked in.
    """
    totals = stage_durations(slp.get("stage") or [])
    out = {"totals": totals, "deep_mode": None, "light_mode": None,
            "deep_target": slp.get("dp"), "light_target": slp.get("lb")}
    for field, key in (("dp", "deep_mode"), ("lb", "light_mode")):
        target = slp.get(field)
        if target is None or not totals:
            continue
        best = min(totals, key=lambda m: abs(totals[m] - target))
        if abs(totals[best] - target) <= 1:      # allow one minute of rounding
            out[key] = best
    return out


def _selftest():
    # day_hr: sentinels dropped, index is the minute
    raw = bytes([0, 60, 254, 61, 255, 62]) + bytes(MINUTES_PER_DAY - 6)
    blob = base64.b64encode(raw).decode().rstrip("=")
    got = day_hr(blob)
    assert got == [(1, 60), (3, 61), (5, 62)], got

    # unpadded base64 must survive
    assert day_hr(base64.b64encode(raw).decode()) == got

    # over-long payload must not silently mis-index
    try:
        day_hr(base64.b64encode(bytes(MINUTES_PER_DAY + 1)).decode())
        raise AssertionError("expected ValueError on over-long data_hr")
    except ValueError:
        pass

    # summary_json: base64-wrapped JSON, unpadded input must work
    obj = {"stp": {"ttl": 8432}, "slp": {"dp": 150}}
    b = base64.b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    assert summary_json(b) == obj, summary_json(b)

    s = hr_summary([(0, 50), (299, 60), (300, 100), (600, 120)])
    assert s["count"] == 4 and s["min"] == 50 and s["max"] == 120, s
    assert s["mean"] == 82.5, s
    assert s["rhr_overnight"] == 55.0, s          # only minutes < 300
    assert hr_summary([])["count"] == 0

    # undelta: first value absolute, both fields accumulate
    assert undelta("0,140;5,2;5,-3") == [(0, 140), (5, 142), (10, 139)]
    assert undelta("1,10;1,10;1,10") == [(1, 10), (2, 20), (3, 30)]
    assert undelta("0,140;5,2;") == [(0, 140), (5, 142)]   # trailing semicolon
    assert undelta("") == []
    assert undelta("0,1,2;3,4,5", n=2) == [(0, 1, 2), (3, 5, 7)]
    try:
        undelta("0,1;2")
        raise AssertionError("expected ValueError on short record")
    except ValueError:
        pass

    st = stream_stats(undelta("0,140;5,2;5,-3"))
    assert st["span_s"] == 10 and st["max"] == 142 and st["min"] == 139, st
    assert stream_stats([])["count"] == 0

    # stage durations, including a night that crosses midnight
    stages = [{"start": 1380, "stop": 1440, "mode": 5},   # 23:00-00:00, 60
              {"start": 0, "stop": 90, "mode": 5},        # 00:00-01:30, 90
              {"start": 90, "stop": 120, "mode": 4},      # 30
              {"start": 1400, "stop": 20, "mode": 8}]     # wraps midnight, 60
    d = stage_durations(stages)
    assert d == {5: 150, 4: 30, 8: 60}, d

    m = match_stage_modes({"dp": 150, "lb": 30, "stage": stages})
    assert m["deep_mode"] == 5 and m["light_mode"] == 4, m
    # no stages means no guess, rather than a confident wrong answer
    assert match_stage_modes({"dp": 150})["deep_mode"] is None

    print("zepp_decode selftest: all assertions passed")


if __name__ == "__main__":
    _selftest()
