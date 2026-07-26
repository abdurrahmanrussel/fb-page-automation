#!/bin/bash
# ──────────────────────────────────────────────────────────────
#  Multi-tenant Facebook Automation — local runner
#  Runs all configured tenants in one process and restarts on crash.
#
#  Usage:
#    chmod +x start.sh
#    ./start.sh            # foreground, logs to terminal
#    ./start.sh --bg       # background, logs to bot.log
# ──────────────────────────────────────────────────────────────
cd "$(dirname "$0")"
source venv/bin/activate 2>/dev/null || source .venv/bin/activate 2>/dev/null

BACKGROUND=false
[[ "$1" == "--bg" ]] && BACKGROUND=true

run_bot() {
    while true; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting multi-tenant server..."
        python server.py
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Server stopped. Restarting in 5s..."
        sleep 5
    done
}

if $BACKGROUND; then
    run_bot >> bot.log 2>&1 &
    echo $! > bot.pid
    echo "Server started in background. PID: $(cat bot.pid)"
    echo "Logs:  tail -f $(pwd)/bot.log"
    echo "Stop:  kill \$(cat $(pwd)/bot.pid)"
else
    echo "Press Ctrl+C to stop."
    run_bot
fi