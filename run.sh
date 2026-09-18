#!/bin/sh
set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/uvicorn ]; then
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
fi

echo "http://127.0.0.1:8000"
echo "api only - jobs stay queued without python -m app.worker in a second terminal"
echo "You should see: datasets page, register form, nav to jobs"
echo "stop with ctrl-c"

.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
