# Cornerman

A personal AI strength coach that reads your training log, measures it against a standing goal, and coaches you over email.

Runs on a free cloud VM for roughly zero rupees a month. Built for one person — me — and published because the hard-won part isn't the code, it's knowing which doors are locked.

---

## The loop

1. Finish a workout. Hit share in Hevy, tap a shortcut. Two taps.
2. The workout text lands in the coach's mailbox.
3. A cron job picks it up within ten minutes, parses it into sets and reps, stores it.
4. The coach reads your full training history, your standing goal, and its own notes — then writes you a message.
5. You reply. It answers, in the same email thread, remembering everything.

No app. No dashboard. Just email.

---

## What makes it a coach rather than a chatbot

**It has a standing goal and judges every session against it.** Not "here's what you lifted" but "the trap-bar work was the point today and you under-loaded it; pull 140 for threes next week."

**It disagrees with you.** The goal is yours; the route to it is the coach's to argue with. If your programming is scattering effort, it says so — including about lifts you've told it you believe in.

**It remembers.** Goals, injuries, patterns, what it told you last time. Stored in its own notes table, read back at the start of every message.

**Its numbers are verified.** Estimated maxes, trends and goal projections come from deterministic Python, not from the model's arithmetic.

---

## Architecture: code computes, the model narrates

The central design rule, and the reason a free model is sufficient:

| Layer | Job | Implementation |
|---|---|---|
| Ingestion | Get workouts in | IMAP + regex parser |
| Storage | Remember everything | SQLite, deduplicated by fingerprint |
| Analysis | Compute the facts | Pure Python — Epley 1RM, linear trend, median-based outlier rejection, goal projection |
| Coach | Decide what to say | LLM via OpenRouter |
| Delivery | Reach the athlete | SMTP with threading headers |

The model never does arithmetic. It receives numbers that are already correct and reasons over them. That keeps advice auditable and lets a free-tier model do the job, because it is never asked to be a calculator.

**The database is read-only to the model.** The connection is opened in SQLite's read-only mode, so a destructive query fails at the engine, not at a filter that might have a gap. The coach can write to exactly one place: its own notes table.

---

## The hard part: getting data out

Most of the effort in this project went into a problem that sounds trivial — reading your own workouts out of a fitness app. Eight routes were attempted. Five are closed.

| # | Route | Outcome |
|---|---|---|
| 1 | Strava as a central hub | **Abandoned.** Paid since June 2026, terms ban AI/ML use, and it strips sets and reps anyway. |
| 2 | Hevy internal API, direct login | **Blocked.** Generic HTML 401 — rejected at the edge before credentials were read. |
| 3 | Replay a captured browser login | **Blocked.** reCAPTCHA on the login, plus rate limiting after a few attempts. |
| 4 | Intercept the phone app's traffic | **Declined.** Certificate pinning; needs an emulator, a patched APK and a proxy. Token expires, so repeat forever. |
| 5 | Reuse an open-source tool's method | **Declined.** Its backend runs a headless browser to produce a reCAPTCHA token server-side. That is bot-detection defeat, not an integration. |
| 6 | Apple Health as a middleman | **Dead end.** No server-side API exists at all, and Health stores "47 minute workout", not your sets. |
| 7 | The official Hevy API | **Available.** Sanctioned, stable, webhooks. Requires the paid tier. |
| 8 | Share sheet to email | **Shipped.** Two taps, zero cost, nothing to break. |

The pattern worth naming: every free automatic route dead-ended at either defeating a protection the company deliberately built, or losing the sets-and-reps detail that makes the data worth having. When a company hardens against you three times over, that is an answer.

A manual two-tap habit that works forever beat an automated pipeline that needs re-authenticating monthly and breaks on vendor updates.

### Watch data

The Amazfit side went better, with one undocumented surprise: the full account export carries daily sleep, steps and run summaries right up to today, but **minute-level heart rate stops in May 2025** — with no watch change and no gap in the app itself. A single per-run export still contains second-by-second heart rate, so the data exists; the bulk export just omits it.

Automatic watch sync is parked. It needs a session token obtained from a traffic capture, which failed on iOS and is waiting on an Android device.

---

## What works, and what doesn't

**Working**

- Hevy workouts to inbox to database, two taps
- Full history backfilled from CSV export
- Estimated 1RM, trend, outlier rejection, goal projection
- Goal-aware coaching with persistent memory
- Threaded email conversation, replies handled
- Retry with model fallback; nothing lost if the LLM is unavailable
- Runs unattended on cron

**Not built yet**

- Automatic watch sync (parked, needs Android)
- Running analysis — heart-rate zones, pace at a given heart rate
- Morning readiness note from sleep and resting heart rate
- Reconciling how a session *felt* against what the data shows
- Body composition tracking

**Known limitations**

- The coach's arithmetic drifts about 5 kg when it estimates a max itself rather than using the verified figure. A hybrid prompt that supplies pre-computed metrics fixes this and is written but not yet the default.
- Free-tier model access is congested and rate-limited. The fallback chain handles it; a small one-time credit purchase would handle it better.
- No bodyweight data, so the coach can judge your training but not your physique.

---

## Cost

| Component | Cost |
|---|---|
| Cloud VM | Free tier |
| Database, analysis, scheduler | Free — your own code |
| Email | Free |
| LLM | Free tier, rate-limited |
| Hevy, Zepp | Free tiers, manual export |

Roughly zero per month, with one honest caveat: free LLM access allows about 50 requests a day, and each coaching message costs one. A one-time credit purchase raises that ceiling twentyfold if you use it heavily.

---

## Setup

Requires Python 3.9+, a dedicated Gmail account for the coach, and an OpenRouter API key.

```bash
git clone https://github.com/YOURNAME/cornerman.git
cd cornerman
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp secrets_example.py secrets_cm.py   # then fill in your credentials
cp goal_example.py goal.py            # then write your own goal

python3 db.py                          # create the database
python3 import_csv.py your_export.csv  # optional: backfill history
python3 daemon.py                      # one cycle, by hand
```

Gmail needs 2-Step Verification enabled and an [app password](https://myaccount.google.com/apppasswords) — not your account password.

To run it unattended, add to cron:

```
* * * * * /path/to/cornerman/cron.sh >> /path/to/cornerman/cron.log 2>&1
```

Every minute is deliberate. Cron only wakes the daemon; `daemon.py` decides
whether the cycle actually runs — every minute for 20 minutes after an
exchange, then every tenth minute once things go quiet. Schedule it as `*/10`
and the active window can never fire, because cron would never invoke it on the
intervening minutes.

### Sending workouts from your phone

On iOS, create a Shortcut that accepts text from the share sheet and mails it to the coach's address. Then: finish in Hevy → Share → Copy Text → your shortcut. Android equivalents exist via Tasker.

---

## Files

```
db.py                 SQLite schema, connection, workout storage
convo.py              Email history and message IDs for threading
parse_hevy_email.py   Hevy share-text to structured sets
read_inbox.py         IMAP fetch and mark-read
mailer.py             SMTP send, with threading headers
analysis.py           e1RM, trend, outlier rejection, goal projection
tools.py              Read-only SQL, coach memory, trusted functions
llm.py                OpenRouter client, retries, model fallback
dump.py               Whole database as compact text
zepp_decode.py        Zepp base64 and delta-stream decoders
zepp_db.py            Zepp schema and writers
zepp_client.py        Zepp API client
zepp_backfill.py      Zepp backfill and incremental sync
zepp_fixtures.py      Live regression fixtures for the Zepp decoders
zepp_cron.sh          Unattended Zepp sync wrapper
goal.py               Your standing goal          (gitignored)
persona.py            How the coach writes
run_cycle.py          Prompt assembly and response
daemon.py             The cycle cron runs
cron.sh               Wrapper with venv activation
deploy.sh             Pull, activate venv, smoke-test imports
import_csv.py         One-time history backfill
docs/                 Build log and architecture explainers
```

---

## What this was worth learning

The analysis engine came together in an afternoon and was right first time. Getting data to feed it took days. Budget accordingly — the interesting work is rarely where the time goes.

Prove the valuable half with the cheap path first. A plain CSV export validated the entire analysis engine before a single live API was touched. When the live routes failed, nothing was wasted.

Login attempts are a budget, not a free action. Probing an endpoint "just to see" costs an attempt, and enough of them locks you out and poisons every reading afterward.

Keeping the model out of arithmetic is what keeps it cheap. The architectural discipline and the running cost turn out to be the same decision.

Negative results are the deliverable. Five blocked routes look like failure in a progress report and read like gold to the next person attempting this.

---

## License

MIT. Use it, fork it, adapt it.

This is a personal tool made public, not a product. It assumes one athlete, one goal, one mailbox. If you want it for yourself, expect to change `goal.py` and probably `persona.py` — that is where your training philosophy lives.
