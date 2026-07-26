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

    start/stop are INCLUSIVE minute-of-day indices, so a segment covers
    stop - start + 1 minutes. Verified against the app on two nights: a
    00:25-00:59 deep segment is reported as 35 minutes, not 34, and summing a
    night with no wake time reproduces the in-bed elapsed time exactly. Plain
    subtraction undercounts by one minute per segment, which is 20-35 minutes
    over a full night.

    A night crossing midnight has stop < start; those wrap forward rather than
    counting as negative time.
    """
    totals = {}
    for st in stages:
        mins = st["stop"] - st["start"]
        if mins < 0:
            mins = mins + MINUTES_PER_DAY
        mode = st.get("mode")
        totals[mode] = totals.get(mode, 0) + mins + 1
    return totals


# Stage mode codes, resolved empirically over 732 nights rather than guessed.
# mode 5 totals 59.9 min/night against a reported dp of 63.7 (6% off), and mode
# 7 totals 9.7 against a reported wk of 10.8 (10% off). Mode 4 dominates at
# 293.3 min/night and mode 8 sits at 36.9, which is the shape of light sleep and
# REM respectively. Sum: ~390 min, a realistic night.
STAGE_LIGHT = 4
STAGE_DEEP = 5
STAGE_AWAKE = 7
STAGE_REM = 8

_KNOWN_STAGES = (STAGE_LIGHT, STAGE_DEEP, STAGE_AWAKE, STAGE_REM)


def sleep_summary(stages):
    """Minutes per sleep phase, summed from the stage list.

    Derived from the stages, never from dp + lb. The brief takes `lb` to be
    light-sleep minutes, and it is not: lb averages 2.9 min/night across this
    account, which cannot be light sleep when mode 4 alone averages 293. Its
    real meaning is still unknown, so it is stored raw and not interpreted.

    total_min excludes awake time, so it is time asleep rather than time in bed.
    """
    totals = stage_durations(stages)
    light = totals.get(STAGE_LIGHT, 0)
    deep = totals.get(STAGE_DEEP, 0)
    rem = totals.get(STAGE_REM, 0)
    awake = totals.get(STAGE_AWAKE, 0)
    out = {}
    out["light_min"] = light
    out["deep_min"] = deep
    out["rem_min"] = rem
    out["awake_min"] = awake
    out["total_min"] = light + deep + rem
    out["unknown_modes"] = dict((m, v) for m, v in totals.items()
                                if m not in _KNOWN_STAGES)
    return out


# Activity block modes, labelled by measured cadence rather than guessed, the
# same way the sleep modes were resolved. Across 8,968 blocks:
#   mode 7   12.9 steps/min   incidental movement
#   mode 1   41.8 steps/min   slow walk
#   mode 3   94.7 steps/min   brisk walk
#   mode 4  148.2 steps/min   running (matches the ~150 cadence on GPS runs)
ACTIVITY_MODES = {7: "incidental", 1: "walk_slow", 3: "walk_brisk", 4: "running"}


def activity_blocks(stp):
    """Timestamped activity blocks for one day, out of the `stp` object.

    Each block carries its own steps, distance and calories over a minute range.
    Collapsing them to a daily total throws away the timing, and the timing is
    what makes heart rate interpretable: an elevated evening average means
    nothing until you can see that the evening is when the walking happens.
    """
    out = []
    for b in (stp.get("stage") or []):
        start = b.get("start")
        stop = b.get("stop")
        if start is None or stop is None:
            continue
        mins = stop - start
        if mins < 0:
            mins = mins + MINUTES_PER_DAY
        mins = mins + 1                      # inclusive bounds, as with sleep
        mode = b.get("mode")
        steps = b.get("step") or 0
        rec = {}
        rec["start_min"] = start
        rec["stop_min"] = stop
        rec["minutes"] = mins
        rec["mode"] = mode
        rec["mode_label"] = ACTIVITY_MODES.get(mode, "mode_" + str(mode))
        rec["steps"] = steps
        rec["distance_m"] = b.get("dis")
        rec["calories"] = b.get("cal")
        rec["steps_per_min"] = round(steps / float(mins), 1) if mins else 0.0
        out.append(rec)
    out.sort(key=lambda r: r["start_min"])
    return out


def sleep_stages(slp):
    """Sleep segments for one night, flattened for storage one row per segment."""
    out = []
    for s in (slp.get("stage") or []):
        start = s.get("start")
        stop = s.get("stop")
        if start is None or stop is None:
            continue
        mins = stop - start
        if mins < 0:
            mins = mins + MINUTES_PER_DAY
        mins = mins + 1
        mode = s.get("mode")
        phase = {STAGE_LIGHT: "light", STAGE_DEEP: "deep",
                 STAGE_REM: "rem", STAGE_AWAKE: "awake"}.get(mode,
                                                             "mode_" + str(mode))
        out.append({"start_min": start, "stop_min": stop, "minutes": mins,
                    "mode": mode, "phase": phase})
    out.sort(key=lambda r: r["start_min"])
    return out


def expand_minutes(start_min, minutes):
    """Minute-of-day indices a segment covers, wrapping past midnight."""
    return [(start_min + k) % MINUTES_PER_DAY for k in range(minutes)]


def sleep_consistency(slp):
    """Cross-check the stage list against the recorded start and end times.

    Two independent sources for the same night: the stages, and st/ed. Summed
    stages must account for the whole time in bed. This invariant is what caught
    the inclusive-bounds off-by-one -- worth keeping, because a decoder that
    silently loses a minute per segment looks perfectly reasonable otherwise.
    """
    st = slp.get("st")
    ed = slp.get("ed")
    s = sleep_summary(slp.get("stage") or [])
    if st is None or ed is None:
        return {"checked": False}
    elapsed = int(round((int(ed) - int(st)) / 60.0))
    accounted = (s["total_min"] + s["awake_min"]
                 + sum(s["unknown_modes"].values()))
    out = {}
    out["checked"] = True
    out["elapsed_min"] = elapsed
    out["accounted_min"] = accounted
    out["delta_min"] = accounted - elapsed
    return out


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
    stages = [{"start": 1380, "stop": 1440, "mode": 5},   # inclusive, so 61
              {"start": 0, "stop": 90, "mode": 5},        # 91
              {"start": 90, "stop": 120, "mode": 4},      # 31
              {"start": 1400, "stop": 20, "mode": 8}]     # wraps midnight, 61
    d = stage_durations(stages)
    assert d == {5: 152, 4: 31, 8: 61}, d

    # Ground truth from the app for the night ending 2026-07-26: these two deep
    # segments are reported as 35 and 11 minutes, not 34 and 10.
    assert stage_durations([{"start": 25, "stop": 59, "mode": STAGE_DEEP}]) \
        == {STAGE_DEEP: 35}
    assert stage_durations([{"start": 122, "stop": 132, "mode": STAGE_DEEP}]) \
        == {STAGE_DEEP: 11}
    # A one-minute waking is stored as start == stop. Subtracting gives 0 and
    # loses the event entirely; the app and Zepp's own wk field both say 1.
    assert stage_durations([{"start": 275, "stop": 275, "mode": STAGE_AWAKE}]) \
        == {STAGE_AWAKE: 1}

    # sleep_summary derives phases from stages, not from dp + lb
    real = [{"start": 0, "stop": 300, "mode": STAGE_LIGHT},
            {"start": 301, "stop": 360, "mode": STAGE_DEEP},
            {"start": 361, "stop": 400, "mode": STAGE_REM},
            {"start": 401, "stop": 410, "mode": STAGE_AWAKE},
            {"start": 411, "stop": 420, "mode": 99}]
    ss = sleep_summary(real)
    assert ss["light_min"] == 301 and ss["deep_min"] == 60, ss
    assert ss["rem_min"] == 40 and ss["awake_min"] == 10, ss
    assert ss["total_min"] == 401, ss           # asleep, excludes the 10 awake
    assert ss["unknown_modes"] == {99: 10}, ss   # surfaced, not silently folded in
    assert sleep_summary([])["total_min"] == 0

    # contiguous segments must account for exactly the time in bed
    base = 1784500000
    cons = sleep_consistency({"st": base, "ed": base + 421 * 60, "stage": real})
    assert cons["delta_min"] == 0, cons
    # the old subtracting decoder would have lost a minute per segment
    assert cons["accounted_min"] == 421, cons
    # and a decoder that lost a minute per segment would show up here
    assert sleep_consistency({"stage": real})["checked"] is False

    m = match_stage_modes({"dp": 152, "lb": 31, "stage": stages})
    assert m["deep_mode"] == 5 and m["light_mode"] == 4, m

    # activity blocks: inclusive minutes, cadence, and midnight wrap
    blocks = activity_blocks({"stage": [
        {"start": 1267, "stop": 1311, "mode": 3, "step": 4393, "dis": 3309, "cal": 170},
        {"start": 1430, "stop": 9, "mode": 1, "step": 400, "dis": 300, "cal": 20}]})
    assert blocks[0]["minutes"] == 45, blocks[0]        # 1311 - 1267 + 1
    assert blocks[0]["steps_per_min"] == 97.6, blocks[0]
    assert blocks[0]["mode_label"] == "walk_brisk", blocks[0]
    assert blocks[1]["minutes"] == 20, blocks[1]        # wraps midnight
    assert blocks[1]["mode_label"] == "walk_slow", blocks[1]
    assert activity_blocks({}) == []
    # an unrecognised mode is labelled, not silently dropped
    assert activity_blocks({"stage": [{"start": 0, "stop": 5, "mode": 42,
                                       "step": 10}]})[0]["mode_label"] == "mode_42"

    st2 = sleep_stages({"stage": real})
    assert [s["phase"] for s in st2] == ["light", "deep", "rem", "awake", "mode_99"], st2
    assert sum(s["minutes"] for s in st2) == 421, st2

    assert expand_minutes(1438, 4) == [1438, 1439, 0, 1]

    # no stages means no guess, rather than a confident wrong answer
    assert match_stage_modes({"dp": 150})["deep_mode"] is None

    print("zepp_decode selftest: all assertions passed")


if __name__ == "__main__":
    _selftest()
