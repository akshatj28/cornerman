"""One full cycle: read inbox, store, coach, reply, log."""
import sys
import traceback
import db
import convo
import mailer
import dump
import llm
import tools
import analysis
from read_inbox import fetch
from parse_hevy_email import parse_workout

KEY_LIFTS = ["Squat (Barbell)", "Deadlift (Trap bar)",
             "Bench Press (Barbell)", "Incline Bench Press (Dumbbell)",
             "Overhead Press (Barbell)"]


def metrics_block():
    lines = ["COMPUTED METRICS (authoritative, do not recalculate)"]
    for ex in KEY_LIFTS:
        t = analysis.trajectory(ex)
        if t.get("sessions", 0) == 0:
            continue
        lines.append("  " + ex + ": est 1RM " + str(t["current_e1rm"])
                     + " kg, last " + t["current_date"]
                     + ", trend " + str(t["kg_per_week"]) + " kg/wk")
    return "\n".join(lines)


def strip_quotes(text):
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith(">"):
            continue
        if s.startswith("On ") and "wrote:" in s:
            break
        out.append(ln)
    return "\n".join(out).strip()


from persona import PERSONA



def build_prompt(task):
    parts = []
    parts.append(metrics_block())
    parts.append("")
    parts.append("YOUR SAVED NOTES: " + str(tools.recall(15)["notes"]))
    parts.append("")
    parts.append("RECENT CONVERSATION")
    parts.append(convo.as_text(20))
    parts.append("")
    parts.append("FULL TRAINING LOG")
    parts.append(dump.as_text())
    parts.append("")
    parts.append(task)
    return "\n".join(parts)


def extract_note(text):
    keep = []
    note = None
    for ln in text.splitlines():
        if ln.strip().upper().startswith("NOTE:"):
            note = ln.strip()[5:].strip()
        else:
            keep.append(ln)
    return "\n".join(keep).strip(), note


def build_prompt(task):
    parts = []
    parts.append(metrics_block())
    parts.append("")
    parts.append("YOUR SAVED NOTES: " + str(tools.recall(15)["notes"]))
    parts.append("")
    parts.append("RECENT CONVERSATION")
    parts.append(convo.as_text(20))
    parts.append("")
    parts.append("FULL TRAINING LOG")
    parts.append(dump.as_text())
    parts.append("")
    parts.append(task)
    return "\n".join(parts)


def extract_note(text):
    keep = []
    note = None
    for ln in text.splitlines():
        if ln.strip().upper().startswith("NOTE:"):
            note = ln.strip()[5:].strip()
        else:
            keep.append(ln)
    return "\n".join(keep).strip(), note


def cycle(verbose=True):
    db.init()
    convo.init()
    seen_ids = set(m["body"][:60] for m in convo.recent(200)
                   if m["direction"] == "in")
    found = fetch(search="UNSEEN", peek=False)
    new_workouts = 0
    for item in found:
        w = item["workout"]
        wid = db.save_workout(w)
        if wid:
            new_workouts += 1
            if verbose:
                print("  stored:", w["title"], w["start_time"])
            task = ("TASK: He just logged this session: " + w["title"]
                    + " on " + str(w["start_time"])
                    + ". Write his post-workout message.")
            respond(task, "Cornerman", verbose=verbose)
    return new_workouts






def respond(task, subject, verbose=False):
    prompt = build_prompt(task)
    if verbose:
        print("  prompt approx tokens:", len(prompt) // 4)
    out = llm.ask_persistent(PERSONA, prompt, verbose=verbose)
    if out is None:
        print("  LLM unavailable after retries, nothing sent")
        return None
    body, note = extract_note(out["text"])
    if not body:
        print("  empty reply, skipping send")
        return None
    if note:
        tools.remember(note)
        if verbose:
            print("  saved note:", note[:70])
    mid = mailer.send_threaded(subject, body)
    convo.log("out", body, subject=subject, msg_id=mid)
    return body


if __name__ == "__main__":
    import sys
    import traceback
    try:
        n = cycle(verbose=True)
        print()
        print("Cycle done. New workouts:", n)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
