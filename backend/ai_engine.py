"""
Per-tenant Groq AI reply generator.

Groq clients are built once per tenant (using that tenant's keys, or the shared
global keys). Each tenant keeps its own round-robin index + rate-limit cooldown
state, so a spike on one page never blocks another.
"""
from __future__ import annotations

import logging
import threading
import time
from groq import Groq

logger = logging.getLogger(__name__)

_COOLDOWN_SECS = 60


class TenantAI:
    """Thread-safe AI reply generator bound to one Tenant."""

    def __init__(self, tenant):
        self.tenant = tenant
        self._lock = threading.Lock()
        self._next_client_index = 0
        self._rate_limited_until: dict[int, float] = {}
        self._clients = self._build_clients()

    def _build_clients(self):
        keys = self.tenant.groq_keys
        if not keys:
            logger.warning(
                "Tenant %s has no Groq API keys — AI replies will use fallbacks.",
                self.tenant.slug,
            )
            return []
        return [
            (Groq(api_key=k, max_retries=0), self.tenant.model) for k in keys
        ]

    def _chat(self, messages: list, max_tokens: int, temperature: float) -> str:
        n = len(self._clients)
        if n == 0:
            raise RuntimeError("No Groq clients configured")
        with self._lock:
            now = time.time()
            start = self._next_client_index
        for attempt in range(n):
            with self._lock:
                i = (start + attempt) % n
                if now < self._rate_limited_until.get(i, 0):
                    continue
                client, model = self._clients[i]
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                with self._lock:
                    self._next_client_index = (i + 1) % n
                return resp.choices[0].message.content.strip()
            except Exception as e:
                err = str(e)
                if "429" in err or "rate_limit" in err.lower():
                    with self._lock:
                        self._rate_limited_until[i] = time.time() + _COOLDOWN_SECS
                    logger.warning(
                        "[%s] Rate limit key %d — cooldown %ds",
                        self.tenant.slug,
                        i,
                        _COOLDOWN_SECS,
                    )
                    continue
                if "413" in err or "too large" in err.lower():
                    logger.warning("[%s] Payload too large, trying next client...", self.tenant.slug)
                    continue
                raise
        raise RuntimeError(f"[{self.tenant.slug}] All Groq clients exhausted")

    # ── Public reply generators ────────────────────────────────────────────────

    def generate_comment_reply(self, comment_text: str, post_text: str = "") -> str:
        context = f'পোস্ট: "{post_text[:150]}"\n' if post_text else ""
        try:
            return self._chat(
                messages=[
                    {"role": "system", "content": self.tenant.comment_prompt},
                    {"role": "user", "content": f'{context}কমেন্ট: "{comment_text}"'},
                ],
                max_tokens=self.tenant.comment_max_tokens,
                temperature=self.tenant.temperature,
            )
        except Exception as e:
            logger.error("[%s] Groq comment reply failed: %s", self.tenant.slug, e)
            return self.tenant.comment_fallback

    def generate_inbox_reply(self, user_message: str, history: list = None) -> str:
        messages = [{"role": "system", "content": self.tenant.base_prompt}]
        if history:
            messages.extend(history[-4:])
        messages.append({"role": "user", "content": user_message})
        try:
            return self._chat(
                messages=messages,
                max_tokens=self.tenant.inbox_max_tokens,
                temperature=self.tenant.temperature,
            )
        except Exception as e:
            logger.error("[%s] Groq inbox reply failed: %s", self.tenant.slug, e)
            return self.tenant.inbox_fallback

    def generate_post_from_topic(self, topic: str) -> str:
        try:
            return self._chat(
                messages=[
                    {"role": "system", "content": self.tenant.post_prompt},
                    {"role": "user", "content": f"বিষয়: {topic}"},
                ],
                max_tokens=400,
                temperature=0.8,
            )
        except Exception as e:
            logger.error("[%s] Groq post generation failed: %s", self.tenant.slug, e)
            return ""

    def generate_emotional_post(self, topic: str) -> str:
        try:
            return self._chat(
                messages=[
                    {"role": "system", "content": self.tenant.emotional_prompt},
                    {"role": "user", "content": f"আজকের গল্পের আবেগ/দৃষ্টিকোণ: {topic}"},
                ],
                max_tokens=400,
                temperature=0.95,
            )
        except Exception as e:
            logger.error("[%s] Groq emotional post failed: %s", self.tenant.slug, e)
            return ""