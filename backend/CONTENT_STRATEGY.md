# Post Content Strategy

Analysis of a high-performing post format (an n8n automation agency's page)
against what each of our 7 pages currently posts, plus what changed and what's
still worth doing later.

## The reference post — why it works

Structure (PAS: Problem → Agitate → Solution, plus benefit bullets + ICP targeting):

1. **Hook** (1-2 lines) — a specific, concrete scene or question. Not
   generic ("we offer automation") — specific ("your IELTS 7.0 student from
   last night, who's processing their file right now?"). 🚨 emoji as a
   visual stop.
2. **Relatable problem** — "sound familiar?" then a scenario with real
   numbers (200-300 messages/day, staff working 9am-7pm).
3. **Agitate** — name the exact moment it goes wrong (10pm-1am, staff
   asleep, decision-stage leads going cold) and the cost (lead goes to a
   competitor overnight).
4. **💡 Solution** — "what if you had..." then a bullet list of concrete
   capabilities, not vague promises.
5. **🎯 Numbered business benefits** — framed as owner outcomes (zero lead
   leakage, near-zero opex, clean database, brand trust), not features
   restated.
6. **Who it's for** — named business categories, so the right reader
   self-identifies.
7. **CTA** — one clear action ("message DEMO"), plus a phone/contact.
8. **Hashtags**.

The mechanism that makes it work: specificity (real numbers, real moments)
+ loss-aversion framing (money already being lost, right now) + a reader
can see themselves in it within the first line.

## Per-page analysis

| Page | Old post style | Verdict | Change made |
|---|---|---|---|
| **AR TechLabs** | Sheet queue, static 1-2 line generic posts ("messages come in, replies take an hour 🤖") | Same market as the reference post (automation agency selling to businesses) but posts had no hook, no story, no urgency — just a flat statement | **Rebuilt.** 2 posts/day (12:00, 20:00), AI-generated from 6 rotating pain-point angles, full PAS structure grounded in AR TechLabs' real feature set (comment/inbox AI reply, daily auto-post, Starter/Growth pricing) — see `post_prompt` in `tenants/ar-techlabs.yaml` |
| **মা ও শিশুর যত্ন a to z** | AI-generated, topic-rotation, practical tips, no explicit hook instruction | Good bones (warm, useful), just started flat instead of grabbing attention | Added explicit hook-first instruction ("আপনার শিশু রাতে বারবার জাগে?" style opener before the tips) |
| **Rahul Hasan Offer Point / Banglalink Drive / Sokol Sim Offer** | AI hook (2 lines) + static price list, already curiosity-driven | Already close to the reference structure — hook, then payoff | Light strengthening: instructed to open with a question or a specific price/number, not just "an attractive hook" in the abstract |
| **Prime Seed BD** | Pre-written story posts (grandfather's wisdom, fake-seed heartbreak, a father's tree outliving him) | Already matches the reference's core mechanism — specific scene, emotional stakes, no flat statements | No change — this was built hook-first from the start this session |
| **Pure Origin Rajshahi** | Sheet queue, currently pre-season (mangoes not ripe yet) | No AI post-generation at all right now — nothing to improve until there's real seasonal content | **Recommendation for later**: once mango season starts, switch to Prime Seed BD's story-post pattern (specific harvest scenes, family/trust angle) instead of generic pricing posts |

## What "hook first" actually means in practice

Not just "start with something interesting" — start with one of:
- **A specific number or price** ("মাত্র ৫৮০৳ তে...")
- **A specific moment/scene** ("রাত ১১টায় মেসেজ এলো...")
- **A direct question that assumes the reader's situation** ("আপনার শিশু
  রাতে বারবার জাগে?")

Generic openers ("আজকে আমরা কথা বলবো...", "আমাদের পেজে...") get scrolled
past. The reader needs to see themselves in the first line before they'll
read the second.

## Guardrails that matter (learned from testing this session)

- **Never let the AI invent statistics.** Testing AR TechLabs' new format
  surfaced the model fabricating "৭০% কমে" / "৩ গুণ বাড়ে" — plausible-
  sounding but made up. Every post_prompt that makes benefit claims now
  explicitly forbids invented percentages; use qualitative language
  ("অনেকটাই কমে") instead.
- **Ground claims in a real feature/price list**, never "whatever sounds
  good." Every post_prompt that references pricing or capabilities lists
  them explicitly and instructs the model to use only those.
- **Markdown gets stripped in code** (`ai_engine._strip_markdown`), not
  just prompted against — models don't reliably follow "no markdown"
  instructions on their own, and `**bold**` renders as literal asterisks
  on Facebook.
- **Token budget must fit the format.** The 15-25 line PAS structure needs
  ~900 tokens of headroom; a 400-token ceiling (fine for a short tip post)
  truncates it mid-sentence. Match `max_tokens` to the actual target length
  per prompt style, not one shared default.
