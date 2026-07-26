# Multi-Tenant Facebook Automation

## What This Is
A **multi-tenant Facebook Page automation engine**. A single deployment runs
many Facebook pages from one hosting account — each page is a *tenant*.

For every tenant the bot:
- Replies to **post comments** with Groq AI (llama-3.3-70b-versatile)
- Replies to **Messenger inbox** messages (with conversation history)
- Publishes **scheduled auto-posts** from a Google Sheet queue
- Serves a **canned list/pricing response** when pricing keywords are detected (no AI)

No webhooks needed — pure polling every 15 seconds per tenant.

## Architecture

```
backend/
├── server.py            # Flask entry point. Loads all tenants, starts one thread per page.
├── tenant.py            # Loads tenants/*.yaml + resolves env vars per tenant.
├── ai_engine.py         # Per-tenant Groq client pool with round-robin + cooldown.
├── bot.py               # One TenantBot per page: polls comments, inbox, runs schedule.
├── tenants/
│   ├── ar-techlabs.yaml            # Tenant config (prompts, schedule, catalogue)
│   └── pure-origin-rajshahi.yaml
├── add_post.py          # CLI: upload posts to a tenant's Google Sheet queue
├── post_offer.py        # CLI: post a one-off offer to a tenant's page
├── download_images.py   # CLI: download images by keyword (tenant-agnostic)
├── apps_script.js       # Reference Google Apps Script for the Sheet
├── posts_<slug>.txt     # Local content queue per tenant
├── render.yaml          # Render deploy config (all tenant env vars)
├── requirements.txt
└── .env                 # Local secrets (namespaced per tenant)
```

## How Multi-Tenancy Works

| Concern | Mechanism |
|---|---|
| Per-page business data | `tenants/<slug>.yaml` (prompts, schedule, contact, catalogue) |
| Per-page credentials | Namespaced env vars, e.g. `AR_TECHLABS_PAGE_ACCESS_TOKEN` |
| Shared AI keys | `GROQ_API_KEY`..`GROQ_API_KEY5` (used by all tenants) |
| Isolation | Each tenant runs in its own background thread with its own state |
| Rate limits | Each tenant has its own round-robin index + cooldowns |

A tenant only starts if **both** `PAGE_ID` and `PAGE_ACCESS_TOKEN` env vars are set.
Otherwise it's skipped with a warning.

## Adding a New Tenant / Client Page

1. **Create the config:** Copy `tenants/ar-techlabs.yaml` → `tenants/<new-slug>.yaml`.
   Edit the `name`, prompts, schedule (`auto_posts`), `list_response`, keywords.

2. **Add env vars** (namespaced by slug, uppercased):
   ```env
   NEW_SLUG_APP_ID=...
   NEW_SLUG_APP_SECRET=...
   NEW_SLUG_PAGE_ACCESS_TOKEN=...
   NEW_SLUG_PAGE_ID=...
   NEW_SLUG_GOOGLE_SCRIPT_URL=...
   ```
   Reference them in the YAML via `page_id_env: NEW_SLUG_PAGE_ID`, etc.

3. **Set up the Facebook app** for that page (permissions in `PERMISSIONS.md`).

4. **Set up a Google Sheet + Apps Script** (`apps_script.js`) for the content queue.
   Paste the `/exec` URL into the env var.

5. Restart — the new tenant starts automatically.

## Running Locally
```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Fill in .env (namespaced per tenant), then:
python server.py
```
Visit `http://localhost:5000/status` to see which tenants are running.

## Deploying to Render
- Root: `backend/`
- Start: `gunicorn server:app --bind 0.0.0.0:$PORT --timeout 120`
- Add all env vars from `render.yaml` in the Render dashboard.
- UptimeRobot pings `/health` every 5 min to keep the free plan awake.
- `/status` shows all running tenants (handy for monitoring).

## Groq AI
- Model: `llama-3.3-70b-versatile`
- Up to 5 shared keys (round-robin), 100k tokens/day each
- On 429 rate limit: 60s cooldown on that key, moves to next
- Per-tenant cooldown state — one page's spike never blocks another

## CLI Tools

### Upload content queue
```bash
python add_post.py ar-techlabs            # upload posts_ar-techlabs.txt
python add_post.py ar-techlabs --list     # show queue
python add_post.py ar-techlabs --clear    # clear queue
python add_post.py --tenants              # list configured tenants
```

### Post a one-off offer
```bash
python post_offer.py ar-techlabs                          # tenant's list_response
python post_offer.py ar-techlabs "Custom message here"    # custom text
```

### Download images (for the Drive folder)
```bash
python download_images.py "facebook marketing" 50
```

## Bot Behaviour (per tenant)

### Comments
- Short 1–2 line AI reply + tenant's `comment_suffix`

### Inbox (Messenger)
- Voice message → asks for text (`voice_message_reply`)
- Image only → ignored
- Pricing/list keyword → raw `list_response` (no AI, no hallucination)
- Otherwise → AI reply with last 4 turns of history
- One reply per poll cycle

## Daily Auto-Post
Per-tenant schedule in BD time (UTC+6). Each slot fires once within a 10-min
window. Post text + image pulled from that tenant's Google Sheet queue.
If the sheet is empty at a slot, it's skipped.