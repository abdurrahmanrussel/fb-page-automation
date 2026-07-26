"""
Manually post a service offer to the AR TechLabs Facebook page.

Usage:
    python post_offer.py             # posts full package list
    python post_offer.py starter     # Starter package post
    python post_offer.py growth      # Growth package post
"""
import sys
import requests
from datetime import datetime
from config import PAGE_ACCESS_TOKEN, PAGE_ID
from ai import SERVICE_LIST, CONTACT_NUMBER

GRAPH = "https://graph.facebook.com/v25.0"

PACKAGE_POSTS = {
    "starter": (
        "Starter",
        "ছোট FB পেজের জন্য পরিপূর্ণ অটোমেশন — Starter প্যাকেজ! 🤖\n\n"
        "✅ কমেন্ট ও ইনবক্সে AI অটো-রিপ্লাই\n"
        "✅ প্রতিদিন AI-লিখিত অটো-পোস্ট\n\n"
        f"সেটআপ: ৳৫,০০০ | মাসিক সার্ভিস চার্জ: ৳৫০০"
    ),
    "growth": (
        "Growth",
        "FB অটোমেশন + নিজের ওয়েবসাইট — Growth প্যাকেজ! 🚀\n\n"
        "✅ Starter প্যাকেজের সবকিছু\n"
        "✅ সাধারণ বিজনেস ওয়েবসাইট, Vercel-এ হোস্টেড\n\n"
        f"সেটআপ: ৳৮,০০০ | মাসিক সার্ভিস চার্জ: ৳৫০০"
    ),
}


def post_to_page(message: str) -> bool:
    resp = requests.post(
        f"{GRAPH}/{PAGE_ID}/feed",
        data={"message": message, "access_token": PAGE_ACCESS_TOKEN},
        timeout=10,
    )
    if resp.ok:
        print("✅ Posted! ID:", resp.json().get("id"))
        return True
    print("❌ Failed:", resp.text)
    return False


def main():
    package_input = sys.argv[1].lower() if len(sys.argv) > 1 else None
    today = datetime.now().strftime("%d %B %Y")

    if package_input and package_input in PACKAGE_POSTS:
        name, body = PACKAGE_POSTS[package_input]
        message = (
            f"🤖 {name} প্যাকেজ ({today})\n\n"
            f"{body}\n\n"
            f"ছোট পেজের জন্য উপযোগী। ফ্রি/কম খরচের AI টুল ব্যবহার করি — কোনো হিডেন চার্জ নেই।\n"
            f"📲 যোগাযোগ: {CONTACT_NUMBER} (WhatsApp/কল)"
        )
    elif package_input:
        print(f"Unknown package '{package_input}'. Available: {', '.join(PACKAGE_POSTS.keys())}")
        return
    else:
        message = f"🤖 AR TechLabs প্যাকেজ ও প্রাইসিং ({today})\n\n{SERVICE_LIST}"

    print("--- Preview ---")
    print(message)
    print("---------------")
    confirm = input("Post this to the page? (y/n): ").strip().lower()
    if confirm == "y":
        post_to_page(message)
    else:
        print("Cancelled.")


if __name__ == "__main__":
    main()
