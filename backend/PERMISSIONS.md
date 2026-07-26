# Facebook App Permissions

> **Status: App created, Page + tokens pending.** App Secret lives only in `backend/.env`
> (gitignored) — never put it in this file.

## App Details

| Field | Value |
|---|---|
| App Name | AR Techlabs |
| App ID | 1737222074372538 |
| Page | AR TechLabs _(pending — create the Page)_ |
| Page ID | _(pending)_ |
| App Mode | Development |

---

## Permissions Needed (OAuth Scopes)

| Permission | Purpose |
|---|---|
| `public_profile` | Basic app access |
| `pages_show_list` | List pages managed by the user |
| `pages_read_engagement` | Read post comments and user interactions on the page |
| `pages_read_user_content` | Read content posted by users on the page |
| `pages_manage_metadata` | Subscribe to webhooks, manage page settings |
| `pages_manage_engagement` | Reply to comments, like/unlike comments |
| `pages_manage_posts` | Create and manage posts on the page |
| `pages_messaging` | Send and receive Messenger messages, send private replies |

Poller mode (current default, see `poller.py`) needs no webhook — it polls the Graph
API every 15s. Webhook fields below only apply if a future webhook-driven mode is built.

## Webhook Configuration (only if using webhook mode)

| Field | Value |
|---|---|
| Callback URL | _(pending — e.g. Render URL)/webhook_ |
| Verify Token | _(set your own, store in `.env` as `VERIFY_TOKEN`)_ |
| Subscribed Fields | `feed`, `messages`, `messaging_postbacks` |

---

## Tokens

| Token | Details |
|---|---|
| Page Access Token | Generate a never-expiring page token, store in `.env` as `PAGE_ACCESS_TOKEN` |
| User Access Token | Needed once to generate the page token; not stored long-term |

---

## AI (Groq)

| Field | Value |
|---|---|
| Provider | Groq |
| Model | `llama-3.3-70b-versatile` |
| Used for | Comment replies + Inbox replies + daily auto-post copy (via Sheet queue) |
| Language | Bangla by default, mirrors English if asked in English |
