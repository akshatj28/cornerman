#!/bin/bash
cd /home/ubuntu/cornerman
source .venv/bin/activate
echo "=== $(date -u '+%Y-%m-%d %H:%M UTC') ==="
python3 daemon.py 2>&1
