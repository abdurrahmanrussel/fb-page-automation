# Multi-Tenant Facebook Automation

## What This Is
A **multi-tenant Facebook Page automation engine**. A single deployment runs
many Facebook pages from one hosting account — each page is a *tenant*.

For every tenant the bot:
- Replies to **post comments** with Groq AI (llama-3.3-70b-versatile)
- Replies to **Messenger inbox** messages (with conversation history)
- Publishes **scheduled auto-posts** — three modes, chosen per tenant (see below)
- Serves a **canned list/pricing response** when pricing keywords are detected (no AI)

No webhooks needed — pure polling every 15 seconds per tenant.

## External Resources (reference links)

| What | Link | Used by |
|---|---|---|
| Live operator pricing sheet (Robi/Airtel/GP/Ryze/Banglalink, columns A-E) | https://docs.google.com/spreadsheets/d/1Hqpbyqov9lvq8WNAeHAXVjlc4aLMylkUwaAYXyXdoWI/edit?usp=sharing | `rahul-hasan-offer-point`, `banglalink-drive`, `sokol-sim-offer` (via `pricing_sheet_url` in each tenant yaml) |
| মা ও শিশুর যত্ন Drive image folder (topic subfolders) | https://drive.google.com/drive/folders/1d1YvcrxvwO1tYEchTFGODu4L3qtbWrGe | Not currently wired into any tenant — `mother-baby` posts are text-only (topic rotation, no image). Kept here in case image posting gets added back later. |

## Architecture

```
backend/
├── server.py            # Flask entry point. Loads all tenants, starts one thread per page.
├── tenant.py             # Loads tenants/*.yaml + resolves env vars per tenant.
├── ai_engine.py          # Per-tenant Groq client pool with round-robin + cooldown.
├── bot.py                # One TenantBot per page: polls comments, inbox, runs schedule.
├── tenants/
│   ├── ar-techlabs.yaml               # Sheet-queue tenant
│   ├── pure-origin-rajshahi.yaml      # Sheet-queue tenant
│   ├── primeseedbd.yaml               # Sheet-queue tenant
│   ├── mother-baby.yaml               # Topic-rotation AI tenant
│   ├── rahul-hasan-offer-point.yaml   # Planned-schedule AI tenant
│   ├── banglalink-drive.yaml          # Planned-schedule AI tenant (same owner, +5min offset)
│   └── sokol-sim-offer.yaml           # Planned-schedule AI tenant (same owner, +10min offset)
├── add_post.py           # CLI: upload posts to a sheet-queue tenant's Google Sheet
├── post_offer.py         # CLI: post a one-off offer to a tenant's page
├── download_images.py    # CLI: download images by keyword (tenant-agnostic)
├── apps_script.js        # Reference Google Apps Script for the Sheet (subfolder-aware)
├── posts_<slug>.txt      # Local content queue per sheet-queue tenant
├── render.yaml            # Render deploy config (all tenant env vars)
├── requirements.txt
└── .env                   # Local secrets (namespaced per tenant, gitignored)
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

## Auto-Post Modes

`bot.py`'s `check_scheduled_post()` picks a mode per tenant, checked in this order:

### 1. Planned schedule (`post_plan` set in the YAML)
Each `auto_posts[i]` time slot maps 1:1 to `post_plan[i]`, a dict:
- `kind: operator` — `topic` gives the AI just enough real numbers to write a
  grounded 2-line hook (`ai_engine.generate_post_from_topic`, uses `post_prompt`
  as system message); the `static` block (verbatim price list + CTA) is
  appended after, unchanged. No hallucinated prices — the AI only ever sees
  the numbers it's allowed to mention.
- `kind: emotional` — `ai_engine.generate_emotional_post` writes a fresh
  brand-story post from `emotional_prompt` each time (temperature 0.95,
  explicitly instructed to vary angle/moment so daily posts don't repeat).

No Google Sheet/Apps Script involved. Used by the mobile-package reseller
tenants (`rahul-hasan-offer-point`, `banglalink-drive`, `sokol-sim-offer`) —
all three are the same owner/offers, schedules offset by 5min each so they
don't post in lockstep.

### 2. Topic rotation (`post_prompt` + `post_topics` set, no `post_plan`)
`_get_topic_post` picks `post_topics[(day_of_year + slot_index) % len(post_topics)]`
and AI-generates the caption from it. Rotating by day-of-year means a tenant
with fewer schedule slots than topics (e.g. 1 slot/day, 6 topics) still cycles
through all topics without repeating the same one every day. Text-only, no
image. Used by `mother-baby`.

### 3. Sheet queue (`google_script_url_env` set, no `post_prompt`)
Original mode — `_get_next_post` pops a pre-written post + random image from
the tenant's Google Sheet queue via Apps Script. See `apps_script.js`;
`getRandomImage()` picks a random topic *subfolder* first if the Drive folder
has subfolders, else falls back to flat files in the root folder — falls back
to `null` (text-only post) if the folder is empty/missing rather than failing
the whole request.

## Reseller Tenants: Operator Recognition

`rahul-hasan-offer-point`, `banglalink-drive`, `sokol-sim-offer` sell
GP/Banglalink/Robi/Airtel/Ryze packages. Their `base_prompt` (inbox) and
`comment_prompt` include an operator-recognition block so the AI correctly
identifies which operator a customer means even from a phone number prefix,
an abbreviation, or a typo — **don't strip this out when editing these
prompts**, it's what prevents the AI quoting the wrong operator's prices:

- Number prefix: `017`/`013`→GP, `019`/`014`→Banglalink or Ryze (ambiguous,
  bot asks which), `016`→Airtel, `018`→Robi
- Name/abbreviation: `gp`/`gb`/`গ্রামীণ`/`grameen`→GP, `bl`/`banglalink`→Banglalink,
  `robi`→Robi, `airtel`→Airtel, `ryze`→Ryze
- `base_prompt` also embeds a condensed real price reference per operator so
  the AI answers grounded in actual numbers instead of guessing; `comment_prompt`
  only gets the recognition rules (kept short, points to inbox/call for prices).

## Adding a New Tenant / Client Page

1. **Create the config:** Copy the closest matching tenant YAML — a
   sheet-queue tenant (`ar-techlabs.yaml`) for a page with a content queue,
   or `rahul-hasan-offer-point.yaml` for a planned-schedule AI page. Edit
   `name`, prompts, schedule (`auto_posts`), `list_response`, keywords.

2. **Add env vars** (namespaced by slug, uppercased):
   ```env
   NEW_SLUG_APP_ID=...
   NEW_SLUG_APP_SECRET=...
   NEW_SLUG_PAGE_ACCESS_TOKEN=...
   NEW_SLUG_PAGE_ID=...
   NEW_SLUG_VERIFY_TOKEN=...
   NEW_SLUG_GOOGLE_SCRIPT_URL=...   # only if using sheet-queue mode
   ```
   Reference them in the YAML via `page_id_env: NEW_SLUG_PAGE_ID`, etc.
   Add the same keys to `render.yaml` (declares keys only, not values —
   paste real values into Render's dashboard separately).

3. **Get a non-expiring Page Access Token**: Graph API Explorer → select the
   app → check `public_profile, pages_show_list, pages_read_engagement,
   pages_read_user_content, pages_manage_metadata, pages_manage_posts,
   pages_manage_engagement, pages_messaging` → generate token → extend to
   long-lived → `GET /me/accounts` with that token to get the page-scoped
   token (`expires_at: 0` = never expires). Full list in `PERMISSIONS.md`.

4. **If using sheet-queue mode**: set up a Google Sheet + Apps Script
   (`apps_script.js`) for the content queue. Paste the `/exec` URL into the
   env var. Deployment must be Web App, Execute as **Me**, access **Anyone**.

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
- `comment_max_tokens: 200` on every tenant — Bangla tokenizes into more
  tokens/word than English, so a lower ceiling was truncating replies
  mid-sentence even with short-reply prompt instructions. The prompt
  controls actual length ("১-২ লাইনে"); the token limit is just a safety
  ceiling, keep it generous.

## CLI Tools

### Upload content queue (sheet-queue tenants only)
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
window after the scheduled time — if the bot wasn't running during that
window (e.g. mid-deploy), that slot is silently skipped for the day, no
catch-up. Content source depends on the tenant's auto-post mode (see above).
