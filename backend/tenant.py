"""
Tenant loader for the multi-tenant Facebook automation backend.

Each tenant is a YAML file in ./tenants/*.yaml. A Tenant object holds all
business-specific data (prompts, schedule, catalogue) and resolves Facebook
page credentials from environment variables (namespaced per tenant).

Groq API keys are shared globally (GROQ_API_KEY .. GROQ_API_KEY5) unless a
tenant defines its own `groq_keys_env` list.
"""
from __future__ import annotations

import glob
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

import yaml
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

TENANTS_DIR = os.path.join(os.path.dirname(__file__), "tenants")


def _env(name: Optional[str]) -> str:
    """Resolve an environment variable name to its value (empty string if unset)."""
    if not name:
        return ""
    return os.getenv(name, "")


@dataclass
class Tenant:
    """A single Facebook page/client configuration."""

    slug: str
    name: str
    raw: dict

    # Facebook credentials (resolved from env)
    page_id: str = ""
    page_access_token: str = ""
    app_id: str = ""
    app_secret: str = ""
    verify_token: str = ""

    # Google Sheets content queue
    google_script_url: str = ""

    # Business identity
    contact_number: str = ""
    email: str = ""
    location: str = ""
    website: str = ""
    payment: str = ""

    # Schedule
    auto_posts: list = field(default_factory=list)
    # If set, must be same length as auto_posts — pairs each slot with a fixed
    # topic; the AI generates the caption at post time, no queue/script needed.
    post_topics: list = field(default_factory=list)

    # AI settings
    model: str = "llama-3.3-70b-versatile"
    comment_max_tokens: int = 100
    inbox_max_tokens: int = 500
    temperature: float = 0.7
    groq_keys: list = field(default_factory=list)

    # Behaviour content
    list_response: str = ""
    list_keywords: list = field(default_factory=list)
    base_prompt: str = ""
    comment_prompt: str = ""
    post_prompt: str = ""
    emotional_prompt: str = ""
    # Parallel to auto_posts: [{kind: "operator", topic, static} | {kind: "emotional", topic}]
    post_plan: list = field(default_factory=list)
    comment_suffix: str = ""
    comment_fallback: str = ""
    inbox_fallback: str = ""
    voice_message_reply: str = "ভয়েস মেসেজ পড়তে পারি না। দয়া করে টেক্সটে লিখুন। 🙏"

    @property
    def is_configured(self) -> bool:
        """True when this tenant has the minimum credentials to run."""
        return bool(self.page_id and self.page_access_token)

    @classmethod
    def from_dict(cls, data: dict) -> "Tenant":
        ai = data.get("ai", {}) or {}

        # Resolve groq keys: per-tenant list, else fall back to global keys
        groq_env_names = data.get("groq_keys_env") or []
        if groq_env_names:
            groq_keys = [k for k in (_env(n) for n in groq_env_names) if k]
        else:
            groq_keys = [
                os.getenv(f"GROQ_API_KEY{i if i > 1 else ''}", "")
                for i in range(1, 6)
            ]
            groq_keys = [k for k in groq_keys if k]

        t = cls(
            slug=data.get("slug", "unknown"),
            name=data.get("name", "Unknown"),
            raw=data,
            page_id=_env(data.get("page_id_env")),
            page_access_token=_env(data.get("page_access_token_env")),
            app_id=_env(data.get("app_id_env")),
            app_secret=_env(data.get("app_secret_env")),
            verify_token=_env(data.get("verify_token_env")),
            google_script_url=_env(data.get("google_script_url_env")),
            contact_number=data.get("contact_number", ""),
            email=data.get("email", ""),
            location=data.get("location", ""),
            website=data.get("website", ""),
            payment=data.get("payment", ""),
            auto_posts=list(data.get("auto_posts", [])),
            post_topics=list(data.get("post_topics", [])),
            model=ai.get("model", "llama-3.3-70b-versatile"),
            comment_max_tokens=int(ai.get("comment_max_tokens", 100)),
            inbox_max_tokens=int(ai.get("inbox_max_tokens", 500)),
            temperature=float(ai.get("temperature", 0.7)),
            groq_keys=groq_keys,
            list_response=data.get("list_response", ""),
            list_keywords=list(data.get("list_keywords", [])),
            base_prompt=data.get("base_prompt", ""),
            comment_prompt=data.get("comment_prompt", ""),
            post_prompt=data.get("post_prompt", ""),
            emotional_prompt=data.get("emotional_prompt", ""),
            post_plan=list(data.get("post_plan", [])),
            comment_suffix=data.get("comment_suffix", ""),
            comment_fallback=data.get("comment_fallback", ""),
            inbox_fallback=data.get("inbox_fallback", ""),
            voice_message_reply=data.get(
                "voice_message_reply",
                "ভয়েস মেসেজ পড়তে পারি না। দয়া করে টেক্সটে লিখুন। 🙏",
            ),
        )
        return t

    def is_list_request(self, text: str) -> bool:
        text_lower = text.lower()
        return any(kw in text_lower for kw in self.list_keywords)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Tenant {self.slug} ({self.name}) configured={self.is_configured}>"


def load_all_tenants() -> list[Tenant]:
    """Load every YAML in ./tenants/, sorted by slug for deterministic ordering."""
    tenants: list[Tenant] = []
    for path in sorted(glob.glob(os.path.join(TENANTS_DIR, "*.yaml"))):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not data:
                continue
            t = Tenant.from_dict(data)
            if t.is_configured:
                tenants.append(t)
                logger.info("Loaded tenant: %s (%s)", t.slug, t.name)
            else:
                logger.warning(
                    "Skipping tenant %s — missing PAGE_ID or PAGE_ACCESS_TOKEN env vars.",
                    t.slug,
                )
        except Exception as e:
            logger.error("Failed to load tenant config %s: %s", path, e)
    return tenants


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for t in load_all_tenants():
        print(t)