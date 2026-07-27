"""One full cycle: new workouts and replies. Adaptive polling."""
import sys
import os
import time
import traceback
from datetime import datetime
import db
import convo
import secrets_cm as creds
from read_inbox import fetch_raw, mark_read
from run_cycle import respond, strip_quotes

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "last_activity.txt")
LOCK_FILE = os.path.join(HERE, "cornerman.lock")

SUBJECT = "Between rounds"
ACTIVE_WINDOW_MIN = 20      # poll every minute for this long after activity
IDLE_INTERVAL_MIN = 3       # otherwise only on these minutes. 60 divides by 3,
                            # so the cadence stays even across hour boundaries.
LOCK_STALE_MIN = 30         # assume a lock older than this is dead


def mark_activity():
    with open(STATE_FILE, "w") as f:
        f.write(str(time.time()))


def minutes_since_activity():
    try:
        with open(STATE_FILE) as f:
            return (time.time() - float(f.read().strip())) / 60.0
    except Exception:
        return 99999.0


def should_run_now():
    since = minutes_since_activity()
    if since < ACTIVE_WINDOW_MIN:
        return True, "active, " + str(int(since)) + "m since last exchange"
    if datetime.now().minute % IDLE_INTERVAL_MIN == 0:
        return True, "idle poll"
    return False, "skip"


def acquire_lock():
    if os.path.exists(LOCK_FILE):
        age = (time.time() - os.path.getmtime(LOCK_FILE)) / 60.0
        if age < LOCK_STALE_MIN:
            return False
        os.remove(LOCK_FILE)
    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True


def release_lock():
    try:
        os.remove(LOCK_FILE)
    except OSError:
        pass


def from_me(sender):
    me = (creds.MY_EMAIL or "").lower()
    return me and me in (sender or "").lower()


def handle_workout(item, verbose):
    w = item["workout"]
    wid = db.save_workout(w)
    if not wid:
        if verbose:
            print("  already stored:", w["title"])
        return 0
    if verbose:
        print("  stored:", w["title"], w["start_time"])
    task = ("TASK: He just logged this session: " + w["title"]
            + " on " + str(w["start_time"])
            + ". Write his post-workout message.")
    sent = respond(task, SUBJECT, verbose=verbose)
    if sent:
        convo.log("in", item["body"], subject=item["subject"],
                  msg_id=item.get("rfc_id"))
        mark_read(item["id"])
        return 1
    if verbose:
        print("  left unread, will retry next cycle")
    return 0


def handle_reply(item, verbose):
    text = strip_quotes(item["body"])
    if not text:
        return 0
    if verbose:
        print("  reply:", text[:60])
    task = ("TASK: He just replied to you. His message:\n\n" + text
            + "\n\nAnswer him directly. Same rules apply.")
    sent = respond(task, SUBJECT, verbose=verbose)
    if sent:
        convo.log("in", text, subject=item["subject"],
                  msg_id=item.get("rfc_id"))
        mark_read(item["id"])
        return 1
    if verbose:
        print("  left unread, will retry next cycle")
    return 0


def zepp_watchdog(verbose=True):
    """Notice if the Zepp sync has gone quiet, and say so.

    Runs here rather than inside the sync because a process cannot report its
    own absence. The coach's cycle is on a separate schedule, so it still runs
    when the Zepp sync is the broken thing.

    Deliberately swallows everything: the coach must not stop working because
    the watchdog does.
    """
    try:
        import zepp_backfill
        h = zepp_backfill.check_freshness(verbose=verbose, heartbeat=True)
        if h["kind"] and verbose:
            print("  zepp watchdog:", h["kind"],
                  "(alerted)" if h["alerted"] else "(already alerted)")
    except Exception as e:
        if verbose:
            print("  zepp watchdog skipped:", str(e)[:100])


def cycle(verbose=True):
    db.init()
    convo.init()
    zepp_watchdog(verbose=verbose)
    items = fetch_raw(search="UNSEEN", peek=True)
    if verbose:
        print("Unread messages:", len(items))
    n = 0
    for item in items:
        if item["workout"]:
            n = n + handle_workout(item, verbose)
        elif from_me(item["from"]):
            n = n + handle_reply(item, verbose)
        elif verbose:
            print("  ignored:", item["subject"][:40])
    if n > 0:
        mark_activity()
    return n


if __name__ == "__main__":
    ok, why = should_run_now()
    if not ok:
        sys.exit(0)
    if not acquire_lock():
        print(datetime.now().strftime("%H:%M"), "| still running, skipping")
        sys.exit(0)
    try:
        print("===", datetime.now().strftime("%Y-%m-%d %H:%M"), "|", why, "===")
        n = cycle(verbose=True)
        print("Handled:", n)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
    finally:
        release_lock()