# FB Page Automation

Multi-tenant **Facebook Page automation engine** — run many Facebook pages from a single hosting account. Each page is a *tenant* with its own AI personality, content schedule, and contact info.

## What it does
For every connected page:
- 🤖 **AI auto-reply** to post comments (Groq · llama-3.3-70b-versatile)
- 💬 **AI auto-reply** to Messenger inbox (with conversation history)
- 📅 **Scheduled auto-posts** from a Google Sheet content queue
- 📋 **Canned pricing/list response** when pricing keywords are detected (no AI hallucination)

No webhooks required — pure polling every 15 seconds per page.

## Architecture
```
backend/
├── server.py            # Flask entry — loads all tenants, one thread per page
├── tenant.py            # Loads tenants/*.yaml + resolves namespaced env vars
├── ai_engine.py         # Per-tenant Groq client pool (round-robin + cooldown)
├── bot.py               # TenantBot: polls comments, inbox, runs schedule
├── tenants/
│   ├── ar-techlabs.yaml             # Tenant config (prompts, schedule, catalogue)
│   └── pure-origin-rajshahi.yaml
├── add_post.py          # CLI: upload posts to a tenant's Sheet queue
├── post_offer.py        # CLI: post a one-off offer to a tenant's page
├── apps_script.js       # Google Apps Script for the Sheet content queue
└── render.yaml          # Render deploy config
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
1. Copy `tenants/ar-techlabs.yaml` → `tenants/<new-slug>.yaml`
2. Add `<NEW_SLUG>_PAGE_ID`, `<NEW_SLUG>_PAGE_ACCESS_TOKEN`, etc. to `.env`
3. Restart — the new page runs alongside the others automatically

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