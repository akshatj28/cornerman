"""Live regression fixtures from section 4 of the integration brief.

These numbers were measured against a real account on 2026-07-26. They are not
illustrative -- they are the contract. If a decoder change stops reproducing
them, the change is wrong, not the fixture.

Costs three API calls. Run it before a backfill and after any edit to
zepp_decode.py.

    python3 zepp_fixtures.py
"""
import sys

import zepp_decode
from zepp_client import Zepp, ZeppError, TokenExpired, WrongRegion

# A 5.23 km run on 2026-07-20, 47:38.
RUN_TRACKID = "1784516143"
RUN_EXPECT = {"count": 997, "mean": 140.4, "min": 92, "max": 165, "span_s": 2858}
RUN_AVG_HR_REPORTED = 140.0      # what Zepp itself claims for this run

DAY = "2026-07-25"
DAY_EXPECT = {"count": 1278, "min": 48, "max": 123,
              "mean": 69.6, "rhr_overnight": 57.4}

TOL = 0.051      # means are rounded to one decimal; counts must be exact


def _check(label, got, want, results):
    if isinstance(want, float):
        ok = got is not None and abs(got - want) <= TOL
    else:
        ok = got == want
    results.append(ok)
    flag = "ok  " if ok else "FAIL"
    print("    %s %-16s got %-8s want %s" % (flag, label, got, want))
    return ok


def check_run(z, results):
    print("Run stream: trackid " + RUN_TRACKID)
    rec = None
    for r in z.workout_history():
        if str(r.get("trackid")) == RUN_TRACKID:
            rec = r
            break
    if rec is None:
        print("    FAIL workout not found in history")
        results.append(False)
        return

    print("    source %s, run_time %ss, Zepp avg_heart_rate %s"
          % (rec.get("source"), rec.get("run_time"), rec.get("avg_heart_rate")))

    data = z.workout_detail(rec)
    stream = data.get("heart_rate") or ""
    if not stream:
        print("    FAIL no heart_rate stream in detail payload")
        results.append(False)
        return

    samples = zepp_decode.undelta(stream)
    st = zepp_decode.stream_stats(samples)
    _check("sample count", st["count"], RUN_EXPECT["count"], results)
    _check("mean bpm", st["mean"], RUN_EXPECT["mean"], results)
    _check("min bpm", st["min"], RUN_EXPECT["min"], results)
    _check("max bpm", st["max"], RUN_EXPECT["max"], results)
    _check("span_s", st["span_s"], RUN_EXPECT["span_s"], results)

    # The span must equal the summary's own duration, or stream and summary
    # disagree and one of them is lying.
    _check("span == run_time", st["span_s"], rec.get("run_time"), results)


def check_day(z, results):
    print("Daily minute HR: " + DAY)
    recs = z.band_data(DAY, DAY)
    rec = None
    for r in recs:
        if r.get("date_time") == DAY:
            rec = r
            break
    if rec is None:
        print("    FAIL no band_data record for " + DAY)
        results.append(False)
        return
    if not rec.get("data_hr"):
        print("    FAIL record has no data_hr -- query_type must be `detail`")
        results.append(False)
        return

    samples = zepp_decode.day_hr(rec["data_hr"])
    s = zepp_decode.hr_summary(samples)
    _check("valid minutes", s["count"], DAY_EXPECT["count"], results)
    _check("min bpm", s["min"], DAY_EXPECT["min"], results)
    _check("max bpm", s["max"], DAY_EXPECT["max"], results)
    _check("mean bpm", s["mean"], DAY_EXPECT["mean"], results)
    _check("00:00-05:00 mean", s["rhr_overnight"],
           DAY_EXPECT["rhr_overnight"], results)


def main():
    try:
        z = Zepp()
    except ZeppError as e:
        print("Not configured:")
        print(" ", e)
        return 1

    results = []
    try:
        check_run(z, results)
        print()
        check_day(z, results)
    except TokenExpired as e:
        print("Token expired:", e)
        return 2
    except WrongRegion as e:
        print("Wrong region:", e)
        return 3

    passed = sum(1 for r in results if r)
    print()
    print("%s/%s fixtures reproduced (%s API calls)"
          % (passed, len(results), z.calls))
    if passed != len(results):
        print("A decoder is wrong. Do not trust a backfill run on this code.")
        return 1
    print("Decoders agree with the brief.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
