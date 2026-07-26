"""One full cycle: new workouts and replies."""
import sys
import traceback
import db
import convo
import secrets_cm as creds
from read_inbox import fetch_raw, mark_read
from run_cycle import respond, strip_quotes


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
    sent = respond(task, "Cornerman", verbose=verbose)
    if sent:
        convo.log("in", item["body"], subject=item["subject"], msg_id=item.get("rfc_id"))
        mark_read(item["id"])
        return 1
    if verbose:
        print("  left unread, will retry next cycle")
    return 0


def handle_reply(item, verbose):
    text = strip_quotes(item["body"])
    if not text:
        return 0
    convo.log("in", text, subject=item["subject"], msg_id=item.get("rfc_id"))
    if verbose:
        print("  reply:", text[:60])
    task = ("TASK: He just replied to you. His message:\n\n" + text
            + "\n\nAnswer him directly. Same rules apply.")
    sent = respond(task, "Cornerman", verbose=verbose)
    if sent:
        mark_read(item["id"])
        return 1
    if verbose:
        print("  left unread, will retry next cycle")
    return 0


def cycle(verbose=True):
    db.init()
    convo.init()
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
    return n


if __name__ == "__main__":
    try:
        print("Handled:", cycle(verbose=True))
    except Exception:
        traceback.print_exc()
        sys.exit(1)
