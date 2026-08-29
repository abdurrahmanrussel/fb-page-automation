"""
Per-tenant AI reply generator. Tries Ollama Cloud models in order (gpt-oss:120b
-> gpt-oss:20b -> deepseek-v4-flash) — a single shared key, not per-tenant —
and falls back to the existing per-tenant Groq round-robin only if all three
fail. Groq behaviour is unchanged when Ollama isn't used.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time

import requests
from dotenv import load_dotenv
from groq import Groq

# Self-sufficient: don't rely on some other module (tenant.py) happening to
# call load_dotenv() before this module is imported and its module-level
# OLLAMA_API_KEY constant gets read. Import order previously meant this was
# always "" in production (bot.py -> ai_engine.py runs before tenant.py's
# load_dotenv()), so Ollama was silently never actually used.
load_dotenv()

logger = logging.getLogger(__name__)

_COOLDOWN_SECS = 60


def _strip_markdown(text: str) -> str:
    """Models sometimes use **bold**/headers despite prompt instructions not
    to — Facebook renders those as literal asterisks/hashes, so strip them
    as a safety net regardless of how well the prompt is followed."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = text.replace("`", "")
    return text

OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "")
OLLAMA_MODELS = ["gpt-oss:120b", "gpt-oss:20b", "deepseek-v4-flash:0731"]
OLLAMA_VISION_MODELS = ["gemma4:31b", "qwen3.5:397b"]  # gpt-oss/deepseek don't accept images
OLLAMA_URL = "https://ollama.com/v1/chat/completions"
WHISPER_MODEL = "whisper-large-v3-turbo"


# Inbox: conversational back-and-forth is normal here, so a customer's "ok"/
# "thanks" after we already answered genuinely needs no further reply.
_SKIP_INSTRUCTION_INBOX = (
    "\n\nবিশেষ নিয়ম: যদি এই মেসেজে কোনো প্রকৃত প্রশ্ন, অনুরোধ, বা আলোচনার মতো বিষয় "
    'না থাকে — শুধু "ok", "thanks", "ধন্যবাদ", "হ্যাঁ"/"আচ্ছা", ইমোজি/স্টিকার, বা এই '
    "ধরনের নিছক স্বীকৃতি হয় যার জবাব দেওয়ার দরকার নেই (যেমন আমাদের আগের রিপ্লাইয়ের "
    "উত্তরে শুধু স্বীকৃতি) — তাহলে অন্য কিছু না লিখে ঠিক এইটুকু লিখবে: NO_REPLY_NEEDED"
)

# Applied to every generated message via _chat() — the model has swapped in
# ₹ (rupee) and even "dollar" in testing despite the prompt already using ৳.
_CURRENCY_GUARD = (
    "\n\nটাকার প্রতীক হিসেবে সবসময় ৳ ব্যবহার করবে — ₹ (রুপি), $ (ডলার), বা অন্য কোনো "
    "কারেন্সি চিহ্ন/নাম ব্যবহার করবে না। এটা বাংলাদেশের ব্যবসা।"
)

# Comments: usually a fresh reaction to the post, not a reply-to-our-reply —
# so a compliment ("ভালো লাগলো", "সুন্দর") still deserves a short warm thanks.
# Only skip when there's genuinely nothing to respond to.
_SKIP_INSTRUCTION_COMMENT = (
    "\n\nবিশেষ নিয়ম: এই কমেন্টে যদি সত্যিই কিছু না থাকে — শুধু ইমোজি/স্টিকার, কাউকে "
    "ট্যাগ করা ছাড়া আর কিছু নেই, বা স্প্যাম/সম্পূর্ণ অপ্রাসঙ্গিক — তাহলে অন্য কিছু না "
    "লিখে ঠিক এইটুকু লিখবে: NO_REPLY_NEEDED। কিন্তু প্রশংসামূলক/ভালো লাগার কমেন্টে "
    '(যেমন "ভালো লাগলো", "সুন্দর পোস্ট") ছোট্ট একটা উষ্ণ ধন্যবাদসূচক রিপ্লাই দিও — '
    "এগুলো স্কিপ করবে না।"
)


def _ollama_chat_one(model: str, messages: list, max_tokens: int, temperature: float) -> str:
    resp = requests.post(
        OLLAMA_URL,
        headers={"Authorization": f"Bearer {OLLAMA_API_KEY}"},
        json={
            "model": model,
            "reasoning_effort": "low",
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        timeout=20,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"].strip()
    if not content:
        raise RuntimeError(f"Ollama ({model}) returned empty content")
    return content


def _ollama_chat(messages: list, max_tokens: int, temperature: float) -> str:
    last_err = None
    for model in OLLAMA_MODELS:
        try:
            return _ollama_chat_one(model, messages, max_tokens, temperature)
        except Exception as e:
            last_err = e
            logger.warning("Ollama model %s failed (%s), trying next", model, e)
    raise last_err


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
        if messages and messages[0].get("role") == "system":
            messages = [
                {**messages[0], "content": messages[0]["content"] + _CURRENCY_GUARD},
                *messages[1:],
            ]
        if OLLAMA_API_KEY:
            try:
                return _strip_markdown(_ollama_chat(messages, max_tokens, temperature))
            except Exception as e:
                logger.warning(
                    "[%s] Ollama failed (%s), falling back to Groq", self.tenant.slug, e
                )
        return _strip_markdown(self._chat_groq(messages, max_tokens, temperature))

    def _chat_groq(self, messages: list, max_tokens: int, temperature: float) -> str:
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

    def generate_comment_reply(self, comment_text: str, post_text: str = "", extra_context: str = "") -> str:
        context = f'পোস্ট: "{post_text[:150]}"\n' if post_text else ""
        system = self.tenant.comment_prompt + _SKIP_INSTRUCTION_COMMENT
        if extra_context:
            system = f"{system}\n\n{extra_context}"
        try:
            return self._chat(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": f'{context}কমেন্ট: "{comment_text}"'},
                ],
                max_tokens=self.tenant.comment_max_tokens,
                temperature=self.tenant.temperature,
            )
        except Exception as e:
            logger.error("[%s] AI comment reply failed: %s", self.tenant.slug, e)
            return self.tenant.comment_fallback

    def generate_inbox_reply(self, user_message: str, history: list = None, extra_context: str = "") -> str:
        system = self.tenant.base_prompt + _SKIP_INSTRUCTION_INBOX
        if extra_context:
            system = f"{system}\n\n{extra_context}"
        messages = [{"role": "system", "content": system}]
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
            logger.error("[%s] AI inbox reply failed: %s", self.tenant.slug, e)
            return self.tenant.inbox_fallback

    def generate_post_from_topic(self, topic: str) -> str:
        try:
            return self._chat(
                messages=[
                    {"role": "system", "content": self.tenant.post_prompt},
                    {"role": "user", "content": f"বিষয়: {topic}"},
                ],
                max_tokens=900,
                temperature=0.8,
            )
        except Exception as e:
            logger.error("[%s] AI post generation failed: %s", self.tenant.slug, e)
            return ""

    def generate_viral_post(self, topic: str) -> str:
        try:
            return self._chat(
                messages=[
                    {"role": "system", "content": self.tenant.viral_prompt},
                    {"role": "user", "content": f"আজকের হুক/অ্যাঙ্গেল: {topic}"},
                ],
                max_tokens=700,
                temperature=0.85,
            )
        except Exception as e:
            logger.error("[%s] AI viral post failed: %s", self.tenant.slug, e)
            return ""

    def generate_full_list_post(self, operator_label: str, raw_data: str) -> str:
        try:
            return self._chat(
                messages=[
                    {"role": "system", "content": self.tenant.full_list_prompt},
                    {"role": "user", "content": f"অপারেটর: {operator_label}\n\nRAW DATA:\n{raw_data}"},
                ],
                max_tokens=3500,
                temperature=0.6,
            )
        except Exception as e:
            logger.error("[%s] AI full-list post failed: %s", self.tenant.slug, e)
            return ""

    def generate_story_post(self, topic: str) -> str:
        try:
            return self._chat(
                messages=[
                    {"role": "system", "content": self.tenant.story_prompt},
                    {"role": "user", "content": f"আজকের গল্পের বিষয়/ইঙ্গিত: {topic}"},
                ],
                max_tokens=700,
                temperature=0.9,
            )
        except Exception as e:
            logger.error("[%s] AI story post failed: %s", self.tenant.slug, e)
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
            logger.error("[%s] AI emotional post failed: %s", self.tenant.slug, e)
            return ""

    # ── Audio / image understanding ─────────────────────────────────────────────

    def transcribe_audio(self, audio_bytes: bytes, filename: str = "voice.ogg") -> str:
        """Speech-to-text via Groq Whisper. Returns "" on any failure."""
        if not self._clients:
            return ""
        client = self._clients[0][0]
        try:
            resp = client.audio.transcriptions.create(
                file=(filename, audio_bytes),
                model=WHISPER_MODEL,
            )
            return (resp.text or "").strip()
        except Exception as e:
            logger.error("[%s] Audio transcription failed: %s", self.tenant.slug, e)
            return ""

    def describe_image(self, image_b64: str, mime_type: str, prompt: str) -> str:
        """Vision via Ollama Cloud (gpt-oss/deepseek don't accept images, so a
        separate model list). Returns "" if unavailable or all models fail."""
        if not OLLAMA_API_KEY:
            return ""
        for model in OLLAMA_VISION_MODELS:
            try:
                resp = requests.post(
                    OLLAMA_URL,
                    headers={"Authorization": f"Bearer {OLLAMA_API_KEY}"},
                    json={
                        "model": model,
                        "reasoning_effort": "low",
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": prompt},
                                    {
                                        "type": "image_url",
                                        "image_url": {"url": f"data:{mime_type};base64,{image_b64}"},
                                    },
                                ],
                            }
                        ],
                        "max_tokens": 1000,
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"].strip()
                if content:
                    return content
            except Exception as e:
                logger.warning("Image description failed on %s: %s", model, e)
        return ""