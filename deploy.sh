#!/bin/bash
cd /home/ubuntu/cornerman
git pull
source .venv/bin/activate
python3 -c "import daemon; print('imports ok')"
