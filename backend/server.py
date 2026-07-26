"""
Entry point for Render deployment — multi-tenant Facebook automation.

Loads every tenant from ./tenants/*.yaml, starts one background thread per
tenant, and exposes /health + /status endpoints so Render (and UptimeRobot)
can keep the single process alive while it serves many pages.
"""
import logging
import threading

from flask import Flask, jsonify

from bot import TenantBot
from tenant import load_all_tenants

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

_bots: list[TenantBot] = []
_threads: list[threading.Thread] = []


def _run_bot(bot: TenantBot):
    try:
        bot.run()
    except Exception as e:
        logger.error("[%s] Bot thread crashed: %s", bot.slug, e)


@app.route("/")
@app.route("/health")
def health():
    return "OK", 200


@app.route("/status")
def status():
    """Show which tenants are running in this single process."""
    return jsonify(
        {
            "tenants": [
                {
                    "slug": b.slug,
                    "name": b.t.name,
                    "schedule_slots": len(b.t.auto_posts),
                    "comments_tracked": len(b.replied_comments),
                    "messages_tracked": len(b.replied_messages),
                }
                for b in _bots
            ]
        }
    )


def start_all_bots():
    tenants = load_all_tenants()
    if not tenants:
        logger.warning("No configured tenants found. Set up env vars + tenants/*.yaml.")
        return

    logger.info("Starting %d tenant bot(s)...", len(tenants))
    for t in tenants:
        bot = TenantBot(t)
        _bots.append(bot)
        thread = threading.Thread(target=_run_bot, args=(bot,), daemon=True)
        _threads.append(thread)
        thread.start()
    logger.info("All tenant bots started in background threads.")


# Start bots when the module loads (gunicorn imports `server:app`)
start_all_bots()