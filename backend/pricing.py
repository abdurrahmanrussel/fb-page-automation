"""
Live operator pricing sheet fetcher for the reseller tenants.

Reads a public Google Sheet (File > Share > Anyone with link) as CSV — no
API key/auth needed. One row, columns A-E = Robi, Airtel, Grameenphone,
Ryze, Banglalink, each cell a free-text block of that operator's current
offers. Cached briefly so a burst of comments/messages doesn't re-fetch
the sheet on every single reply.
"""
from __future__ import annotations

import csv
import io
import logging
import time

import requests

logger = logging.getLogger(__name__)

_CACHE_TTL = 300  # seconds
_cache: dict[str, tuple[float, dict]] = {}

_COLUMNS = ["robi", "airtel", "grameenphone", "ryze", "banglalink"]


def _sheet_id(url: str) -> str:
    # https://docs.google.com/spreadsheets/d/<ID>/edit?...
    return url.split("/d/")[1].split("/")[0]


def fetch_pricing(sheet_url: str) -> dict:
    """Returns {operator: raw_text_block}. Cached for _CACHE_TTL seconds."""
    now = time.time()
    cached = _cache.get(sheet_url)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    csv_url = f"https://docs.google.com/spreadsheets/d/{_sheet_id(sheet_url)}/export?format=csv&gid=0"
    try:
        resp = requests.get(csv_url, timeout=15)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        reader = csv.reader(io.StringIO(resp.text))
        row = next(reader)
        data = {col: row[i].strip() for i, col in enumerate(_COLUMNS) if i < len(row)}
    except Exception as e:
        logger.error("Pricing sheet fetch failed: %s", e)
        if cached:
            return cached[1]  # serve stale data rather than nothing
        return {}

    _cache[sheet_url] = (now, data)
    return data


def format_pricing_context(sheet_url: str) -> str:
    """Formats the live sheet into a system-message block for the AI."""
    data = fetch_pricing(sheet_url)
    if not data:
        return ""
    labels = {
        "robi": "Robi",
        "airtel": "Airtel",
        "grameenphone": "Grameenphone (GP)",
        "ryze": "Ryze",
        "banglalink": "Banglalink",
    }
    parts = ["📋 আজকের লাইভ দামের তালিকা (শুধু এই তথ্য ব্যবহার করবে, নিজে থেকে দাম বানাবে না):"]
    for key, label in labels.items():
        text = data.get(key, "")
        if text:
            parts.append(f"\n--- {label} ---\n{text}")
    return "\n".join(parts)
