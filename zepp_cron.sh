#!/bin/bash
# Unattended Zepp sync. Nothing on Zepp's side expires, so this is paced and
# forgiving rather than urgent: the sync always re-fetches the last few days
# because daily data settles late, which means anything missed self-heals on the
# next run instead of needing a catch-up.
#
# flock keeps two runs from overlapping if one ever hangs -- they would contend
# for the same SQLite writes.
cd /home/ubuntu/cornerman || exit 1

LOG=/home/ubuntu/cornerman/zepp_cron.log

# Keep the log from growing without bound, which cron.log does.
if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG")" -gt 2000000 ]; then
    tail -n 2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

exec 9>/home/ubuntu/cornerman/zepp_sync.lock
if ! flock -n 9; then
    echo "=== $(date '+%Y-%m-%d %H:%M %Z') | previous sync still running, skipping ===" >> "$LOG"
    exit 0
fi

{
    echo "=== $(date '+%Y-%m-%d %H:%M %Z') ==="
    source .venv/bin/activate
    python3 zepp_backfill.py --sync
    echo "exit: $?"
    echo
} >> "$LOG" 2>&1
