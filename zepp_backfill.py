"""Backfill and incremental sync for Zepp data.

Nothing on Zepp's side expires -- streams are intact on runs 1000 days old and
minute HR goes back to account start -- so this is paced and resumable rather
than urgent. Every request sleeps 0.6s. A full backfill is roughly 100 workout
calls, 100 detail calls and 75 day-range calls: a few minutes, not hours.

Errors are tolerated per item and reported at the end. One bad day must not
abandon the other thousand. A 401 is the exception: the token is dead, every
subsequent call would fail, so it stops and emails.

Usage:
    python3 zepp_backfill.py --check              token and host only
    python3 zepp_backfill.py --backfill           everything, resumable
    python3 zepp_backfill.py --workouts           workouts and streams only
    python3 zepp_backfill.py --daily --since DATE days only, from DATE
    python3 zepp_backfill.py --sync               incremental, for cron
    python3 zepp_backfill.py --report             what is stored now
"""
import json
import sys
import time
from datetime import date, datetime, timedelta

import zepp_db
import zepp_decode
from zepp_client import Zepp, ZeppError, TokenExpired, WrongRegion, EmptyDetail

ACCOUNT_START = "2023-10-10"
CHUNK_DAYS = 15         # days per band_data call
RECENT_DAYS = 3         # always re-fetch this many; daily data settles late
SPAN_TOLERANCE = 2      # seconds of disagreement allowed between stream and summary


def _start_time(trackid):
    """trackid is a unix timestamp in the account's local zone.

    Interpreted with the machine's local timezone, which on the VM is IST and
    matches the account region. The zone in force is written to sync_state so
    that running this somewhere else shows up as a discrepancy rather than
    silently shifting every date by hours.
    """
    try:
        return datetime.fromtimestamp(int(trackid)).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _chunks(start, end, n):
    a = date.fromisoformat(start)
    z = date.fromisoformat(end)
    while a <= z:
        b = min(a + timedelta(days=n - 1), z)
        yield a.isoformat(), b.isoformat()
        a = b + timedelta(days=1)


def alert_token_expired(err):
    """Write the failure down and say so out loud. Never fail silently."""
    stamp = datetime.now().isoformat(timespec="seconds")
    zepp_db.set_state("zepp_token_status", "expired")
    zepp_db.set_state("zepp_token_expired_at", stamp)
    print("  TOKEN EXPIRED at " + stamp + ": " + str(err))
    body = ("The Zepp sync stopped because the app_token is no longer valid.\n\n"
            "To fix it:\n"
            "  1. Log in at https://watchface.zepp.com/ in a desktop browser.\n"
            "  2. DevTools > Application > Cookies > copy hm-user-login-info.\n"
            "  3. URL-decode it and take token_info.app_token.\n"
            "  4. Put it in secrets_cm.py as ZEPP_TOKEN.\n"
            "  5. Close the tab. Do NOT click log out -- that voids the token.\n\n"
            "Then run: python3 zepp_backfill.py --check\n\n"
            "Nothing was lost. Zepp does not expire data, so the sync resumes\n"
            "from where it stopped.\n")
    try:
        import mailer
        mailer.send("Cornerman: Zepp token expired", body)
        print("  alert emailed")
    except Exception as e:                      # an alert failing must not mask
        print("  could not email alert: " + str(e)[:120])   # the original cause


def sync_workouts(z, verbose=True, detail=True, resume=True):
    """History, then streams. Returns a counts dict."""
    recs = z.workout_history()
    if verbose:
        print("History: " + str(len(recs)) + " workouts")

    stored = 0
    for rec in recs:
        tid = rec.get("trackid")
        src = rec.get("source")
        if not tid or not src:
            continue
        zepp_db.save_workout(rec, src, tid, _start_time(tid))
        stored += 1
    if verbose:
        print("  stored/updated " + str(stored))

    out = {"workouts": stored, "streams": 0, "no_streams": 0,
           "span_mismatch": [], "failed": []}
    if not detail:
        return out

    done = zepp_db.ids_with_detail() if resume else set()
    if verbose and done:
        print("  skipping " + str(len(done)) + " that already have detail")

    for rec in recs:
        tid = str(rec.get("trackid") or "")
        src = rec.get("source")
        if not tid or not src or tid in done:
            continue
        try:
            data = z.workout_detail(rec)
        except EmptyDetail:
            # Correct source, genuinely no streams. Not every session has them.
            out["no_streams"] += 1
            continue
        except TokenExpired:
            raise
        except ZeppError as e:
            out["failed"].append((tid, str(e)[:100]))
            continue

        stream = data.get("heart_rate") or ""
        try:
            hr = zepp_decode.undelta(stream) if stream else []
        except ValueError as e:
            out["failed"].append((tid, "undelta: " + str(e)[:80]))
            hr = []

        zepp_db.save_workout_detail(src, tid, json.dumps(data, default=str), hr)
        if hr:
            out["streams"] += 1
            # The brief's integrity check: stream span must equal run_time.
            span = hr[-1][0]
            rt = zepp_db._int(rec.get("run_time")) or 0   # arrives as a string
            if rt and abs(span - rt) > SPAN_TOLERANCE:
                out["span_mismatch"].append((tid, span, rt))
        if verbose and out["streams"] % 10 == 0 and hr:
            print("  streams: " + str(out["streams"]))
    return out


def sync_daily(z, start, end, verbose=True):
    """Sleep, steps and minute HR for a date range. Tolerant per day."""
    out = {"days": 0, "hr_days": 0, "hr_rows": 0, "sleep_nights": 0,
           "failed": [], "stage_votes": {}}
    for a, b in _chunks(start, end, CHUNK_DAYS):
        try:
            recs = z.band_data(a, b)
        except TokenExpired:
            raise
        except ZeppError as e:
            out["failed"].append((a + ".." + b, str(e)[:100]))
            continue
        if verbose:
            print("  " + a + " .. " + b + "  " + str(len(recs)) + " days")

        for rec in recs:
            d = rec.get("date_time")
            if not d:
                continue
            try:
                blob = rec.get("summary")
                obj = zepp_decode.summary_json(blob) if blob else {}
                stp = obj.get("stp") or {}
                slp = obj.get("slp") or {}

                samples = []
                if rec.get("data_hr"):
                    samples = zepp_decode.day_hr(rec["data_hr"])
                stats = zepp_decode.hr_summary(samples)

                if samples:
                    out["hr_rows"] += zepp_db.save_daily_hr(d, samples)
                    out["hr_days"] += 1
                zepp_db.save_daily(d, stp, stats, blob or "")
                out["days"] += 1

                if slp:
                    zepp_db.save_sleep(d, slp, blob or "")
                    out["sleep_nights"] += 1
                    # Confirm the stage codes from data instead of guessing.
                    m = zepp_decode.match_stage_modes(slp)
                    for role in ("deep_mode", "light_mode"):
                        mode = m.get(role)
                        if mode is not None:
                            key = role + "=" + str(mode)
                            out["stage_votes"][key] = out["stage_votes"].get(key, 0) + 1
            except Exception as e:
                out["failed"].append((d, type(e).__name__ + ": " + str(e)[:80]))

        zepp_db.set_state("zepp_daily_watermark", b)
    return out


def _print_summary(w, d, elapsed, calls):
    print()
    print("=" * 62)
    print("RECOVERY REPORT")
    print("=" * 62)
    if w:
        print("Workouts stored/updated : %s" % w["workouts"])
        print("  with HR streams       : %s" % w["streams"])
        print("  no streams (expected) : %s" % w["no_streams"])
        if w["span_mismatch"]:
            print("  SPAN MISMATCH (stream length != run_time):")
            for tid, span, rt in w["span_mismatch"][:10]:
                print("    trackid %s  stream %ss vs run_time %ss" % (tid, span, rt))
        else:
            print("  span check            : all streams match run_time")
        for tid, err in w["failed"][:10]:
            print("  FAILED %s: %s" % (tid, err))
    if d:
        print("Days stored             : %s" % d["days"])
        print("  with minute HR        : %s" % d["hr_days"])
        print("  minute-HR rows        : %s" % d["hr_rows"])
        print("  sleep nights          : %s" % d["sleep_nights"])
        if d["stage_votes"]:
            print("  sleep stage codes (by agreement across nights):")
            for k, n in sorted(d["stage_votes"].items(), key=lambda x: -x[1]):
                print("    %-16s %s nights" % (k, n))
        else:
            print("  sleep stage codes     : undetermined")
        for day, err in d["failed"][:10]:
            print("  FAILED %s: %s" % (day, err))
        if len(d["failed"]) > 10:
            print("  ... +%d more failures" % (len(d["failed"]) - 10))
    print()
    print("API calls: %s    elapsed: %.1fs" % (calls, elapsed))
    print()
    zepp_db.report()


def main(argv):
    zepp_db.init()

    if "--report" in argv:
        zepp_db.report()
        return 0

    try:
        z = Zepp()
    except ZeppError as e:
        print("Not configured:")
        print(" ", e)
        return 1

    zepp_db.set_state("zepp_tz", time.tzname[0] if time.tzname else "unknown")

    if "--check" in argv:
        try:
            info = z.check()
        except TokenExpired as e:
            alert_token_expired(e)
            return 2
        except WrongRegion as e:
            print("Wrong region:", e)
            return 3
        print("Token OK. Workouts visible:", info["workouts"])
        print("By type:", dict(sorted((k, v) for k, v in info["by_type"].items()
                                      if k is not None)))
        zepp_db.set_state("zepp_token_status", "ok")
        return 0

    since = ACCOUNT_START
    if "--since" in argv:
        since = argv[argv.index("--since") + 1]
    today = date.today().isoformat()

    do_workouts = not ("--daily" in argv)
    do_daily = not ("--workouts" in argv)

    if "--sync" in argv:
        # Incremental: new workouts, plus the last few days because daily data
        # settles late. Watermark only moves forward.
        mark = zepp_db.get_state("zepp_daily_watermark")
        back = (date.today() - timedelta(days=RECENT_DAYS)).isoformat()
        since = min(mark, back) if mark else back

    t0 = time.time()
    w = d = None
    try:
        if do_workouts:
            print("--- workouts ---")
            w = sync_workouts(z, detail=True)
        if do_daily:
            print("--- daily (%s .. %s) ---" % (since, today))
            d = sync_daily(z, since, today)
    except TokenExpired as e:
        alert_token_expired(e)
        _print_summary(w, d, time.time() - t0, z.calls)
        return 2
    except WrongRegion as e:
        print("Wrong region:", e)
        return 3

    zepp_db.set_state("zepp_token_status", "ok")
    zepp_db.set_state("zepp_last_sync", datetime.now().isoformat(timespec="seconds"))
    _print_summary(w, d, time.time() - t0, z.calls)
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 1:
        print(__doc__)
        raise SystemExit(0)
    raise SystemExit(main(sys.argv[1:]))
