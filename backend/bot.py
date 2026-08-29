"""
Per-tenant Facebook poller.

One TenantBot instance handles a single Facebook page: polls comments and
inbox every `POLL_INTERVAL` seconds and runs the tenant's auto-post schedule.
Multiple TenantBot instances run in parallel threads (see server.py) so one
hosting can serve many pages.
"""
from __future__ import annotations

import base64
import logging
import re
import time
from datetime import datetime, timezone, timedelta

import requests

import memory
from ai_engine import TenantAI
from pricing import fetch_pricing, format_pricing_context
from tenant import Tenant

memory.init_db()

logger = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com/v25.0"
POLL_INTERVAL = 5  # seconds
BD_TZ = timedelta(hours=6)  # Bangladesh = UTC+6

_BN_DIGITS = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")
_BN_MONTHS = [
    "জানুয়ারি", "ফেব্রুয়ারি", "মার্চ", "এপ্রিল", "মে", "জুন",
    "জুলাই", "আগস্ট", "সেপ্টেম্বর", "অক্টোবর", "নভেম্বর", "ডিসেম্বর",
]
_OPERATOR_LABELS = {
    "grameenphone": "Grameenphone (GP)",
    "banglalink": "Banglalink",
    "robi": "Robi",
    "airtel": "Airtel",
    "ryze": "Ryze",
}


def _bangla_date(dt) -> str:
    day = str(dt.day).translate(_BN_DIGITS)
    month = _BN_MONTHS[dt.month - 1]
    year = str(dt.year).translate(_BN_DIGITS)
    return f"{day} {month}, {year}"


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

        # Reply tracking — preloaded from sqlite so a restart mid-conversation
        # can't cause a double-reply (previously in-memory only, reset on
        # every restart, relying solely on the startup backlog-seed pass).
        self.replied_comments: set = memory.load_replied_ids(self.slug, "comment")
        self.replied_messages: set = memory.load_replied_ids(self.slug, "message")

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
                memory.mark_replied(self.slug, "comment", cid)
                if reply:
                    post_text = post.get("message") or post.get("story") or ""
                    comment_text = comment.get("message", "")
                    pricing_ctx = (
                        format_pricing_context(self.t.pricing_sheet_url)
                        if self.t.pricing_sheet_url
                        else ""
                    )
                    ai_reply = self.ai.generate_comment_reply(comment_text, post_text, pricing_ctx)
                    if ai_reply.strip() == "NO_REPLY_NEEDED":
                        self._log("info", "Skipping comment %s — no reply needed", cid)
                        continue
                    full_reply = ai_reply + self.t.comment_suffix
                    self._reply_to_comment(cid, full_reply)

    # ── Messenger polling ──────────────────────────────────────────────────────

    def _download_attachment(self, url: str) -> bytes | None:
        try:
            resp = requests.get(url, timeout=20)
            if resp.ok:
                return resp.content
            self._log("warning", "Attachment download failed (%s): %s", resp.status_code, url[:80])
        except Exception as e:
            self._log("warning", "Attachment download error: %s", e)
        return None

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
                        memory.mark_replied(self.slug, "message", mid)
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
        memory.mark_replied(self.slug, "message", mid)

        user_text = latest_user_msg.get("message", "")
        attachments = latest_user_msg.get("attachments", {}).get("data", [])

        # Facebook returns "mime_type" (e.g. "image/jpeg", "audio/mp4"), not a
        # "type" field — classify off that instead.
        image_att = None
        audio_att = None
        for a in attachments:
            mime = a.get("mime_type", "")
            if mime.startswith("image/") and image_att is None:
                image_att = a
            elif (mime.startswith("audio/") or mime.startswith("video/")) and audio_att is None:
                audio_att = a

        if audio_att:
            url = (
                audio_att.get("video_data", {}).get("url")
                or audio_att.get("image_data", {}).get("url")
                or audio_att.get("file_url")
            )
            audio_bytes = self._download_attachment(url) if url else None
            transcribed = self.ai.transcribe_audio(audio_bytes) if audio_bytes else ""
            if transcribed:
                self._log("info", "Transcribed voice message: %.60s...", transcribed)
                user_text = transcribed
            else:
                self._send_message(sender_id, self.t.voice_message_reply)
                return

        if image_att:
            url = image_att.get("image_data", {}).get("url")
            img_bytes = self._download_attachment(url) if url else None
            description = ""
            if img_bytes:
                b64 = base64.b64encode(img_bytes).decode()
                mime = image_att.get("mime_type", "image/jpeg")
                description = self.ai.describe_image(
                    b64,
                    mime,
                    "এটা একজন গ্রাহকের পাঠানো ছবি, কাস্টমার সার্ভিস কনভারসেশনে। ছবিতে কী "
                    "আছে সংক্ষেপে বাংলায় বর্ণনা করো (যেমন: পেমেন্ট/লেনদেনের স্ক্রিনশট, "
                    "প্রোডাক্টের ছবি, কোনো সমস্যার ছবি, স্ক্রিনশট, ইত্যাদি)।",
                )
            if description:
                if user_text:
                    user_text = f"{user_text}\n[সাথে পাঠানো ছবিতে যা আছে: {description}]"
                else:
                    user_text = f"[গ্রাহক শুধু একটা ছবি পাঠিয়েছেন। ছবিতে যা আছে: {description}]"
            elif not user_text:
                return  # couldn't describe and no text either — nothing to reply to

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

        # Facebook's own thread window (limit 10, sliced further to last 4 in
        # ai_engine) drops older context fast — a customer who asked about GP
        # pricing two days ago and sends one short message today may get a
        # window containing only that one message. The durable sqlite log
        # doesn't have that limit, so prefer it once it has caught up (it
        # will, after this exchange saves below); fall back to the raw FB
        # parse for a customer's very first exchange, before anything's
        # logged yet.
        db_history = memory.get_recent_history(self.slug, sender_id, limit=8)
        if len(db_history) >= len(history):
            history = db_history

        pricing_ctx = (
            format_pricing_context(self.t.pricing_sheet_url)
            if self.t.pricing_sheet_url
            else ""
        )
        memory.save_message(self.slug, sender_id, "user", user_text)
        ai_reply = self.ai.generate_inbox_reply(user_text, history, pricing_ctx)
        if ai_reply.strip() == "NO_REPLY_NEEDED":
            self._log("info", "Skipping message from %s — no reply needed", sender_id)
            return
        self._send_message(sender_id, ai_reply)
        memory.save_message(self.slug, sender_id, "assistant", ai_reply)

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
        """AI-generate a caption for a rotating topic. No script/queue needed.

        Topic is chosen by day-of-year (+ slot position, for multi-slot
        schedules) so it varies day to day instead of always picking the
        same fixed topic when there are fewer slots than topics.
        """
        if not self.t.post_topics:
            self._log("error", "No post_topics configured for slot %s", post_time)
            return None, None
        try:
            slot_idx = self.t.auto_posts.index(post_time)
        except ValueError:
            slot_idx = 0
        day_of_year = datetime.now(timezone.utc).timetuple().tm_yday
        topic = self.t.post_topics[(day_of_year + slot_idx) % len(self.t.post_topics)]
        caption = self.ai.generate_post_from_topic(topic)
        if not caption:
            self._log("error", "AI returned empty caption for topic '%s'", topic)
            return None, None
        self._log("info", "Generated post for topic '%s': %.60s...", topic, caption)
        return caption, None

    def _get_planned_post(self, post_time: str):
        """AI post from a fixed per-slot plan (operator hook+static list, or emotional). No script/queue needed."""
        try:
            idx = self.t.auto_posts.index(post_time)
            plan = self.t.post_plan[idx]
        except (ValueError, IndexError):
            self._log("error", "No post_plan entry for slot %s", post_time)
            return None, None
        kind = plan.get("kind")
        topic = plan.get("topic", "")
        if kind == "emotional":
            caption = self.ai.generate_emotional_post(topic)
            if not caption:
                self._log("error", "AI returned empty emotional post for slot %s", post_time)
                return None, None
            return caption, None
        if kind == "story":
            caption = self.ai.generate_story_post(topic)
            if not caption:
                self._log("error", "AI returned empty story post for slot %s", post_time)
                return None, None
            return caption, None
        if kind == "viral":
            caption = self.ai.generate_viral_post(topic)
            if not caption:
                self._log("error", "AI returned empty viral post for slot %s", post_time)
                return None, None
            return caption, None
        if kind == "full_list":
            operators = plan.get("operators") or ([plan["operator"]] if plan.get("operator") else [])
            if not operators:
                self._log("error", "full_list slot has no operator(s) configured for %s", post_time)
                return None, None
            day_of_year = datetime.now(timezone.utc).timetuple().tm_yday
            operator = operators[day_of_year % len(operators)]
            if not self.t.pricing_sheet_url:
                self._log("error", "full_list slot but no pricing_sheet_url set for %s", post_time)
                return None, None
            data = fetch_pricing(self.t.pricing_sheet_url)
            raw = data.get(operator, "")
            if not raw:
                self._log("error", "No live pricing data for operator '%s' at slot %s", operator, post_time)
                return None, None
            body = self.ai.generate_full_list_post(operator, raw)
            if not body:
                self._log("error", "AI returned empty full-list post for %s at slot %s", operator, post_time)
                return None, None
            now_bd = datetime.now(timezone.utc) + BD_TZ
            label = _OPERATOR_LABELS.get(operator, operator)
            header = f"📅 {_bangla_date(now_bd)} — আজকের {label} সব অফার দেখুন একটা লিস্টে! 👇\n\n"
            return header + body, None
        hook = self.ai.generate_post_from_topic(topic)
        if not hook:
            self._log("error", "AI returned empty hook for topic '%s'", topic)
            return None, None
        static = plan.get("static", "")
        text = f"{hook}\n\n{static}" if static else hook
        self._log("info", "Generated planned post (%s/%s): %.60s...", kind, topic, text)
        return text, None

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

            if self.t.post_plan:
                post_text, image_url = self._get_planned_post(post_time)
            elif self.t.post_prompt and self.t.post_topics:
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