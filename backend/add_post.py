"""
Multi-tenant post queue manager.

Upload/list/clear the Google Sheet content queue for a specific tenant.

Usage:
    python add_post.py <tenant-slug>            # upload from posts_<slug>.txt
    python add_post.py <tenant-slug> --list     # show current queue
    python add_post.py <tenant-slug> --clear    # clear queue
    python add_post.py --tenants                # list available tenants

Example:
    python add_post.py ar-techlabs
    python add_post.py pure-origin-rajshahi

posts_<slug>.txt format — just numbered posts, no image tags needed:
    ১. First post text here
    ২. Second post text here
    ৩. Third post (can be multi-line,
       just indent continuation lines)
"""
import os
import re
import sys
import requests
from dotenv import load_dotenv

from tenant import load_all_tenants

load_dotenv()

BASE_DIR = os.path.dirname(__file__)

# Matches Bengali (১২৩...) or English (123...) numbered list markers
NUMBERED = re.compile(r"(?:^|\n)[\s]*[১২৩৪৫৬৭৮৯০\d]+[.।]\s*")


def get_tenant(slug: str):
    for t in load_all_tenants():
        if t.slug == slug:
            return t
    print(f"❌ Tenant '{slug}' not found or not configured (check env vars).")
    print("Run: python add_post.py --tenants")
    sys.exit(1)


def parse_posts(text: str) -> list[str]:
    parts = NUMBERED.split(text)
    return [p.strip() for p in parts if p.strip()]


def upload(tenant, post_text: str) -> bool:
    if not tenant.google_script_url:
        print(f"ERROR: GOOGLE_SCRIPT_URL not set for tenant '{tenant.slug}'")
        return False
    resp = requests.get(
        tenant.google_script_url,
        params={"action": "add", "post": post_text},
        timeout=20,
    )
    try:
        data = resp.json()
    except Exception:
        print(f"  ❌ Bad response (status {resp.status_code}): {resp.text[:200]}")
        return False
    if data.get("status") == "added":
        return True
    print(f"  ❌ Script error: {data}")
    return False


def list_posts(tenant) -> None:
    if not tenant.google_script_url:
        print(f"ERROR: GOOGLE_SCRIPT_URL not set for tenant '{tenant.slug}'")
        return
    resp = requests.get(tenant.google_script_url, params={"action": "list"}, timeout=20)
    if not resp.ok:
        print(f"❌ {resp.status_code}: {resp.text}")
        return
    posts = resp.json().get("posts", [])
    if not posts:
        print("Queue is empty.")
        return
    print(f"\n📋 [{tenant.name}] {len(posts)} queued posts:\n")
    for i, p in enumerate(posts, 1):
        preview = p.get("post", "")[:70].replace("\n", " ")
        img = " 🖼️" if p.get("image") else ""
        print(f"  {i}.{img} {preview}{'...' if len(p.get('post', '')) > 70 else ''}")
    print()


def clear_queue(tenant) -> None:
    if not tenant.google_script_url:
        print(f"ERROR: GOOGLE_SCRIPT_URL not set for tenant '{tenant.slug}'")
        return
    confirm = input("Clear ALL queued posts? (y/n): ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return
    resp = requests.get(tenant.google_script_url, params={"action": "clear"}, timeout=20)
    if resp.ok:
        print(f"✅ Cleared {resp.json().get('deleted', '?')} posts.")
    else:
        print(f"❌ {resp.status_code}: {resp.text}")


def main():
    args = sys.argv[1:]

    if not args:
        print(__doc__)
        return

    if args[0] == "--tenants":
        print("\nAvailable tenants:")
        for t in load_all_tenants():
            print(f"  • {t.slug} — {t.name}")
        return

    slug = args[0]
    tenant = get_tenant(slug)
    posts_file = os.path.join(BASE_DIR, f"posts_{tenant.slug}.txt")

    flags = args[1:]
    if flags and flags[0] == "--list":
        list_posts(tenant)
        return
    if flags and flags[0] == "--clear":
        clear_queue(tenant)
        return

    if not os.path.exists(posts_file):
        print(f"posts_{tenant.slug}.txt not found. Create it at:\n  {posts_file}")
        return

    raw = open(posts_file, encoding="utf-8").read()
    posts = parse_posts(raw)

    if not posts:
        print(f"No numbered posts found in posts_{tenant.slug}.txt.")
        return

    print(f"\n📋 [{tenant.name}] Found {len(posts)} post(s):\n")
    for i, text in enumerate(posts, 1):
        preview = text[:80].replace("\n", " ")
        print(f"  {i}. {preview}{'...' if len(text) > 80 else ''}")

    print()
    confirm = input(f"Upload all {len(posts)} to queue? (y/n): ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return

    print()
    ok = 0
    for i, text in enumerate(posts, 1):
        if upload(tenant, text):
            print(f"  ✅ Post {i} uploaded")
            ok += 1
        else:
            print(f"  ❌ Post {i} failed")

    print(f"\n✅ {ok}/{len(posts)} uploaded.")

    if ok == len(posts):
        clear = input(f"Clear posts_{tenant.slug}.txt now? (y/n): ").strip().lower()
        if clear == "y":
            open(posts_file, "w", encoding="utf-8").close()
            print(f"posts_{tenant.slug}.txt cleared.")


if __name__ == "__main__":
    main()