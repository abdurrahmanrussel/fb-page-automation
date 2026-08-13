"""
Per-tenant Facebook poller.

One TenantBot instance handles a single Facebook page: polls comments and
inbox every `POLL_INTERVAL` seconds and runs the tenant's auto-post schedule.
Multiple TenantBot instances run in parallel threads (see server.py) so one
hosting can serve many pages.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone, timedelta

import requests

from ai_engine import TenantAI
from tenant import Tenant

logger = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com/v25.0"
POLL_INTERVAL = 15  # seconds
BD_TZ = timedelta(hours=6)  # Bangladesh = UTC+6


def _fix_image_url(url: str) -> str:
    """Convert Google Drive share links to direct download URLs."""
    if not url:
        return url
    match = re.search(r"/d/([a-zA-Z0-9_-]+)", url)
    if match:
        return f"https://drive.google.com/uc?export=download&id={match.group(1)}"
    return url


class TenantBot:
    """Polling-based Facebook auto-reply + auto-post bot for one tenant."""

    def __init__(self, tenant: Tenant):
        self.t = tenant
        self.ai = TenantAI(tenant)
        self.slug = tenant.slug

        # Schedule state
        self._posted_today: set = set()
        self._last_post_date = None

        # Reply tracking (in-memory, resets on restart)
        self.replied_comments: set = set()
        self.replied_messages: set = set()

        self._stop = False

    # ── Logging helpers ────────────────────────────────────────────────────────

    def _log(self, level, msg, *args):
        getattr(logger, level)("[%s] " + msg, self.slug, *args)

    # ── Comment polling ────────────────────────────────────────────────────────

    def _get_recent_posts(self):
        resp = requests.get(
            f"{GRAPH}/{self.t.page_id}/posts",
            params={
                "access_token": self.t.page_access_token,
                "limit": 5,
                "fields": "id,message,story,created_time",
            },
            timeout=10,
        )
        if not resp.ok:
            self._log("error", "Failed to fetch posts: %s", resp.text)
            return []
        return resp.json().get("data", [])

    def _get_comments(self, post_id):
        resp = requests.get(
            f"{GRAPH}/{post_id}/comments",
            params={
                "access_token": self.t.page_access_token,
                "filter": "stream",
                "limit": 25,
                "fields": "id,from,message,created_time",
            },
            timeout=10,
        )
        if not resp.ok:
            self._log("error", "Failed to fetch comments for %s: %s", post_id, resp.text)
            return []
        return resp.json().get("data", [])

    def _reply_to_comment(self, comment_id, message):
        resp = requests.post(
            f"{GRAPH}/{comment_id}/comments",
            data={"message": message, "access_token": self.t.page_access_token},
            timeout=10,
        )
        if resp.ok:
            self._log("info", "Replied to comment %s", comment_id)
        else:
            self._log("error", "Failed comment reply %s: %s", comment_id, resp.text)

    def check_comments(self, reply=True):
        posts = self._get_recent_posts()
        for post in posts:
            comments = self._get_comments(post["id"])
            for comment in comments:
                cid = comment["id"]
                from_id = comment.get("from", {}).get("id", "")
                if from_id == self.t.page_id:
                    continue
                if cid in self.replied_comments:
                    continue
                self.replied_comments.add(cid)
                if reply:
                    post_text = post.get("message") or post.get("story") or ""
                    comment_text = comment.get("message", "")
                    ai_reply = self.ai.generate_comment_reply(comment_text, post_text)
                    full_reply = ai_reply + self.t.comment_suffix
                    self._reply_to_comment(cid, full_reply)

    # ── Messenger polling ──────────────────────────────────────────────────────

    def _send_message(self, recipient_id, message):
        resp = requests.post(
            f"{GRAPH}/me/messages",
            json={
                "recipient": {"id": recipient_id},
                "message": {"text": message},
            },
            params={"access_token": self.t.page_access_token},
            timeout=10,
        )
        if resp.ok:
            self._log("info", "Sent DM to %s", recipient_id)
        else:
            self._log("error", "Failed DM to %s: %s", recipient_id, resp.text)

    def _get_conversations(self):
        resp = requests.get(
            f"{GRAPH}/{self.t.page_id}/conversations",
            params={"access_token": self.t.page_access_token, "limit": 10},
            timeout=10,
        )
        if not resp.ok:
            self._log("warning", "Cannot fetch conversations: %s", resp.text)
            return []
        return resp.json().get("data", [])

    def _get_messages_in_conversation(self, conv_id):
        resp = requests.get(
            f"{GRAPH}/{conv_id}/messages",
            params={
                "access_token": self.t.page_access_token,
                "fields": "id,from,message,created_time,attachments",
                "limit": 10,
            },
            timeout=10,
        )
        if not resp.ok:
            return []
        return list(reversed(resp.json().get("data", [])))

    def check_inbox(self, reply=True):
        conversations = self._get_conversations()

        if not reply:
            for conv in conversations:
                for msg in self._get_messages_in_conversation(conv["id"]):
                    mid = msg.get("id")
                    sender_id = msg.get("from", {}).get("id", "")
                    if mid and sender_id != self.t.page_id:
                        self.replied_messages.add(mid)
            return

        candidates = []
        conv_messages_map = {}
        for conv in conversations:
            messages = self._get_messages_in_conversation(conv["id"])
            conv_messages_map[conv["id"]] = messages

            last_msg = messages[-1] if messages else None
            if last_msg and last_msg.get("from", {}).get("id") == self.t.page_id:
                continue

            for msg in reversed(messages):
                mid = msg.get("id")
                sender_id = msg.get("from", {}).get("id", "")
                if not mid or sender_id == self.t.page_id:
                    continue
                if mid in self.replied_messages:
                    continue
                text = msg.get("message", "")
                attachments = msg.get("attachments", {}).get("data", [])
                if not text and not attachments:
                    continue
                candidates.append((msg, conv["id"]))
                break

        if not candidates:
            return

        candidates.sort(key=lambda x: x[0].get("created_time", ""), reverse=True)
        latest_user_msg, conv_id = candidates[0]
        messages = conv_messages_map[conv_id]

        mid = latest_user_msg["id"]
        sender_id = latest_user_msg.get("from", {}).get("id", "")
        self.replied_messages.add(mid)

        user_text = latest_user_msg.get("message", "")
        attachments = latest_user_msg.get("attachments", {}).get("data", [])
        attach_types = {a.get("type", "") for a in attachments}

        # Voice → ask for text
        if "audio" in attach_types:
            self._send_message(sender_id, self.t.voice_message_reply)
            return

        # Image only → ignore
        if "image" in attach_types and not user_text:
            return

        # Pricing/list request → raw canned response (no AI, no hallucination)
        if self.t.is_list_request(user_text):
            self._send_message(sender_id, self.t.list_response)
            return

        history = []
        for m in messages:
            if m["id"] == mid:
                break
            role = "assistant" if m.get("from", {}).get("id") == self.t.page_id else "user"
            if m.get("message"):
                history.append({"role": role, "content": m["message"]})

        ai_reply = self.ai.generate_inbox_reply(user_text, history)
        self._send_message(sender_id, ai_reply)

    # ── Scheduled daily posts ──────────────────────────────────────────────────

    def _post_to_page(self, message: str, image_url: str = None):
        if image_url:
            resp = requests.post(
                f"{GRAPH}/{self.t.page_id}/photos",
                data={"url": image_url, "message": message, "access_token": self.t.page_access_token},
                timeout=15,
            )
        else:
            resp = requests.post(
                f"{GRAPH}/{self.t.page_id}/feed",
                data={"message": message, "access_token": self.t.page_access_token},
                timeout=15,
            )
        if resp.ok:
            self._log("info", "Auto-post published. ID: %s", resp.json().get("id", resp.json().get("post_id")))
        else:
            self._log("error", "Auto-post failed: %s", resp.text)
        return resp.ok

    def _get_next_post(self):
        """Fetch next queued post from the tenant's Google Sheet queue."""
        if not self.t.google_script_url:
            self._log("error", "GOOGLE_SCRIPT_URL env var not set for tenant.")
            return None, None
        try:
            resp = requests.get(self.t.google_script_url, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            post = data.get("post") or None
            image = _fix_image_url(data.get("image") or "")
            if post:
                self._log("info", "Got post from sheet: %.60s...", post)
            else:
                self._log("info", "Sheet empty — no post to publish.")
            return post, image
        except Exception as e:
            self._log("error", "Sheet fetch failed: %s", e)
            return None, None

    def _get_next_ai_post(self):
        """Fetch a random Drive topic+image, then AI-generate the caption from the topic name."""
        if not self.t.google_script_url:
            self._log("error", "GOOGLE_SCRIPT_URL env var not set for tenant.")
            return None, None
        try:
            resp = requests.get(self.t.google_script_url, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            topic = data.get("topic") or None
            image = _fix_image_url(data.get("image") or "")
            if not topic:
                self._log("info", "No topic/image available from Drive — skipping.")
                return None, None
            caption = self.ai.generate_post_from_topic(topic)
            if not caption:
                self._log("error", "AI returned empty caption for topic '%s'", topic)
                return None, None
            self._log("info", "Generated post for topic '%s': %.60s...", topic, caption)
            return caption, image
        except Exception as e:
            self._log("error", "Drive topic fetch failed: %s", e)
            return None, None

    def _get_topic_post(self, post_time: str):
        """AI-generate a caption for the fixed topic paired with this time slot. No script/queue needed."""
        try:
            idx = self.t.auto_posts.index(post_time)
            topic = self.t.post_topics[idx]
        except (ValueError, IndexError):
            self._log("error", "No topic configured for slot %s", post_time)
            return None, None
        caption = self.ai.generate_post_from_topic(topic)
        if not caption:
            self._log("error", "AI returned empty caption for topic '%s'", topic)
            return None, None
        self._log("info", "Generated post for topic '%s': %.60s...", topic, caption)
        return caption, None

    def check_scheduled_post(self):
        now = datetime.now(timezone.utc) + BD_TZ
        today = now.date()

        if self._last_post_date != today:
            self._posted_today = set()
            self._last_post_date = today

        now_minutes = now.hour * 60 + now.minute

        for post_time in self.t.auto_posts:
            if post_time in self._posted_today:
                continue
            ph, pm = map(int, post_time.split(":"))
            slot_minutes = ph * 60 + pm
            diff = now_minutes - slot_minutes
            # only fire within a 10-minute window after scheduled time
            if not (0 <= diff < 10):
                continue
            self._posted_today.add(post_time)

            if self.t.post_prompt and self.t.post_topics:
                post_text, image_url = self._get_topic_post(post_time)
            elif self.t.post_prompt:
                post_text, image_url = self._get_next_ai_post()
            else:
                post_text, image_url = self._get_next_post()
            if not post_text:
                self._log("info", "No post available for %s — skipping.", post_time)
                continue
            self._post_to_page(post_text, image_url)
            self._log("info", "Auto-post done at %s.", post_time)

    # ── Main loop ──────────────────────────────────────────────────────────────

    def seed(self):
        """Mark existing comments/messages as replied so we don't reply to old ones."""
        self._log("info", "Seeding existing comments and messages...")
        try:
            self.check_comments(reply=False)
            self.check_inbox(reply=False)
        except Exception as e:
            self._log("error", "Seeding error: %s", e)
        self._log(
            "info",
            "Seeded %d comment(s) and %d message(s). Watching for NEW ones...",
            len(self.replied_comments),
            len(self.replied_messages),
        )

    def run(self):
        """Main poll loop. Runs until stop() is called."""
        self._log("info", "%s — FB Bot starting...", self.t.name)
        self.seed()
        schedule = (
            f"{self.t.auto_posts[0]} → {self.t.auto_posts[-1]} ({len(self.t.auto_posts)} slots)"
            if len(self.t.auto_posts) > 1
            else (self.t.auto_posts[0] if self.t.auto_posts else "(none)")
        )
        self._log("info", "Polling every %ds. Auto-post schedule: %s BD time.", POLL_INTERVAL, schedule)

        while not self._stop:
            time.sleep(POLL_INTERVAL)
            try:
                self.check_scheduled_post()
                self.check_comments()
                self.check_inbox()
            except Exception as e:
                self._log("error", "Unexpected error: %s", e)

        self._log("info", "Bot stopped.")

    def stop(self):
        self._stop = True