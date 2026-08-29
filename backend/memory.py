"""
Durable per-tenant conversation memory + reply-dedup, backed by a single
sqlite file (backend/data/memory.db).

Two problems this fixes that pure in-memory state didn't:
1. Inbox history for the AI prompt came only from Facebook's own
   conversation window (`/messages?limit=10`), which drops older exchanges
   once enough new messages pile up — a customer who asked about GP pricing
   two days ago and sends one short message today gets no memory of that at
   all if the window only returns today's message. Logging every exchange
   here means we can fill that gap regardless of Graph API's window size.
2. `replied_comments`/`replied_messages` were plain in-memory sets, wiped on
   every restart — dedup then depended entirely on the startup backlog-seed
   pass. Persisting them here means a restart mid-conversation can't cause
   an accidental double-reply even if the seed pass ever misses something.

Kept intentionally simple: short-lived sqlite3 connections per call (no
long-held connection across threads), WAL mode so the 7 tenant threads
don't block each other, a single lock only to serialize writes.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "memory.db")
_lock = threading.Lock()
_initialized = False


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    """Idempotent — safe to call every time a TenantBot starts."""
    global _initialized
    if _initialized:
        return
    with _lock, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant TEXT NOT NULL,
                sender_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_tenant_sender "
            "ON messages (tenant, sender_id, id)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS replied_ids (
                tenant TEXT NOT NULL,
                kind TEXT NOT NULL,
                item_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (tenant, kind, item_id)
            )
            """
        )
    _initialized = True


def save_message(tenant: str, sender_id: str, role: str, content: str) -> None:
    """role: 'user' or 'assistant'."""
    if not content or not content.strip():
        return
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO messages (tenant, sender_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (tenant, sender_id, role, content, datetime.now(timezone.utc).isoformat()),
        )


def get_recent_history(tenant: str, sender_id: str, limit: int = 8) -> list[dict]:
    """Oldest-first, ready to feed straight into the AI messages list."""
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages "
            "WHERE tenant = ? AND sender_id = ? ORDER BY id DESC LIMIT ?",
            (tenant, sender_id, limit),
        ).fetchall()
    return [{"role": role, "content": content} for role, content in reversed(rows)]


def mark_replied(tenant: str, kind: str, item_id: str) -> None:
    """kind: 'comment' or 'message'."""
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO replied_ids (tenant, kind, item_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (tenant, kind, item_id, datetime.now(timezone.utc).isoformat()),
        )


def load_replied_ids(tenant: str, kind: str) -> set:
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT item_id FROM replied_ids WHERE tenant = ? AND kind = ?",
            (tenant, kind),
        ).fetchall()
    return {row[0] for row in rows}
