# FB Page Automation

Multi-tenant **Facebook Page automation engine** — run many Facebook pages from a single hosting account. Each page is a *tenant* with its own AI personality, content schedule, and contact info.

## What it does
For every connected page:
- 🤖 **AI auto-reply** to post comments (Groq · llama-3.3-70b-versatile)
- 💬 **AI auto-reply** to Messenger inbox (with conversation history)
- 📅 **Scheduled auto-posts** — three modes, chosen per tenant (see below)
- 📋 **Canned pricing/list response** when pricing keywords are detected (no AI hallucination)

No webhooks required — pure polling every 15 seconds per page.

## Current tenants
| Slug | Page | Auto-post mode |
|---|---|---|
| `ar-techlabs` | AR TechLabs | Sheet queue |
| `pure-origin-rajshahi` | Pure Origin Rajshahi | Sheet queue |
| `primeseedbd` | Prime Seed BD | Sheet queue |
| `mother-baby` | মা ও শিশুর যত্ন a to z | Topic rotation (AI) |
| `rahul-hasan-offer-point` | Rahul Hasan Offer Point | Planned schedule (AI) |
| `banglalink-drive` | বাংলালিংক ড্রাইভ by Rahul Hasan | Planned schedule (AI) |
| `sokol-sim-offer` | সকল সিমের ইন্টারনেট এবং মিনিট অফার | Planned schedule (AI) |

## Auto-post modes
A tenant's YAML determines which mode `bot.py` uses at each scheduled slot — checked in this order:

1. **Planned schedule** (`post_plan` set) — each `auto_posts[i]` slot maps 1:1 to `post_plan[i]`, either:
   - `kind: operator` — AI writes a short 2-line hook grounded in that slot's `topic` (real prices, no invented numbers), then the `static` price block is appended verbatim.
   - `kind: emotional` — AI writes a fresh, warm brand-story post each time from `emotional_prompt` (higher temperature, no repetition).
   No Google Sheet or Apps Script needed. Used by the 3 mobile-package reseller tenants.

2. **Topic rotation** (`post_prompt` + `post_topics` set, no `post_plan`) — AI generates the caption from a topic that rotates daily (day-of-year based) through `post_topics`, so a tenant with fewer schedule slots than topics still avoids repeats. Text-only. Used by `mother-baby`.

3. **Sheet queue** (`google_script_url_env` set, no `post_prompt`) — pulls a pre-written post + random image from a Google Sheet content queue via Apps Script (`apps_script.js`). Default/original mode.

## Architecture
```
backend/
├── server.py            # Flask entry — loads all tenants, one thread per page
├── tenant.py             # Loads tenants/*.yaml + resolves namespaced env vars
├── ai_engine.py          # Per-tenant Groq client pool (round-robin + cooldown)
│                          #   generate_comment_reply / generate_inbox_reply /
│                          #   generate_post_from_topic / generate_emotional_post
├── bot.py                # TenantBot: polls comments, inbox, runs schedule
│                          #   (3 auto-post modes, see above)
├── tenants/*.yaml         # One config per page: credentials refs, prompts,
│                          #   schedule, catalogue/plan
├── add_post.py            # CLI: upload posts_<slug>.txt to a Sheet-queue tenant
├── post_offer.py          # CLI: post a one-off offer to a tenant's page
├── apps_script.js         # Google Apps Script for the Sheet content queue
│                          #   (subfolder-aware image scan, falls back to flat)
└── render.yaml            # Render deploy config — all tenant env var keys
```

## Quick start
```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Fill in .env (one namespaced group per tenant), then:
python server.py
```
Visit `http://localhost:5000/status` to see running tenants.

## Adding a new client page
1. Copy the closest matching `tenants/<slug>.yaml` as a starting point — sheet-queue tenants for a page with a content queue, or `rahul-hasan-offer-point.yaml` for a planned-schedule AI page.
2. Add `<NEW_SLUG>_APP_ID`, `<NEW_SLUG>_APP_SECRET`, `<NEW_SLUG>_PAGE_ID`, `<NEW_SLUG>_PAGE_ACCESS_TOKEN`, `<NEW_SLUG>_VERIFY_TOKEN` to `.env` and `render.yaml`.
3. Restart — the new page runs alongside the others automatically.

## Deploy to Render
- **Root:** `backend/`
- **Start:** `gunicorn server:app --bind 0.0.0.0:$PORT --timeout 120`
- Add env vars from `render.yaml` (Groq keys are shared; credentials are namespaced per tenant)
- UptimeRobot pings `/health` every 5 min to keep the free plan awake

## Endpoints
| Route | Purpose |
|---|---|
| `GET /health` | `200 OK` — for uptime monitoring |
| `GET /status` | JSON list of all running tenants + their stats |

---
Built by **Md. Abdur Rahman** · [Portfolio](https://md-abdur-rahman.vercel.app/) · abdurrahmanrussel77@gmail.com
