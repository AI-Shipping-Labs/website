---
name: slack-thread-to-crm
description: Use when asked to take people from a Slack thread/channel (e.g. "who's going to X", "who signed up for the course/event") and record them in the AI Shipping Labs CRM — resolve Slack users to emails, confirm platform users, add member notes + tags, and optionally hand off to plan generation. Triggered by screenshots of Slack threads or requests like "add these people to the CRM".
metadata:
  short-description: Read a Slack thread, map participants to platform users, write CRM notes + tags
---

# Slack thread -> CRM

Turn "who is doing X" in a Slack thread into CRM records (member notes + tags) on the AI Shipping Labs platform. This is the composed recipe; it delegates the mechanics:

- Slack reads (find channel, read thread, resolve handles to emails): `ai-shipping-labs-slack`.
- CRM reads/writes (user lookup, member notes, tags): `ai-shipping-labs-users`.
- Downstream plan generation: `ai-shipping-labs-plan-import`.

The user usually attaches a phone screenshot of the thread. The screenshot shows the topic and reply count but NOT every reply — always read the full thread from Slack, never work from the screenshot alone.

## Workflow

### 1. Read the full thread
Find the channel and pull every reply. See `ai-shipping-labs-slack` for the commands (`conversations.list`, `conversations.history`, `conversations.replies`). `#community` = `C0AFZSRAYQ4`. `reply_count` includes the parent.

### 2. Classify intent (this is the judgment, do it yourself)
Read every reply and classify each person — do not regex "I'm in". Distinguish:

- committed — "yes, I am doing that", "I'll be doing it", "I'm in".
- tentative — "I think I might join, no concrete plan yet". Flag separately; do NOT fold into the committed group.
- advanced / doesn't need it — someone who already did a prior course. The host often replies in-thread that "the only new thing for you is X". Still record them, but mark separately.

Exclude the thread author / host (the person asking "who's going?").

### 3. Resolve each person to an email
Use `ai-shipping-labs-slack` — `users.info` by ID, or page `users.list` and match `name` / `real_name` / `display_name`.

### 4. Confirm each is a platform user
`GET /api/users/{email}` (see `ai-shipping-labs-users`). Existing tags are signal: `ai-buildcamp`, `ai-buildcamp-1/2/3` => already took a prior course (corroborates "advanced"). A 404 means no platform account — surface it, don't invent one.

### 5. Write CRM: member note + tag
See `ai-shipping-labs-users` for the exact endpoints and the verify gotcha. Per person:

- Member note (`POST /api/member-notes`, `kind:general`, `visibility:internal`) — always cite the source thread and quote the person's own words.
- Cohort tag (`POST /api/users/{email}/tags`) — committed people get the course-cohort tag (e.g. `llm-zoomcamp-2026`); tentative people get the `-interested` variant so the committed group filters cleanly.

### 6. Hand off to plans (optional / usually a separate confirmed step)
"Generate plans so they can attend" is downstream. Plans attach to a sprint (`GET /api/sprints`). The LLM-Zoomcamp-style community sprint may not exist yet — do NOT auto-create a sprint and bulk plans without confirming the target sprint slug. Once confirmed, use `ai-shipping-labs-plan-import`.

## Output to the user
Give a clean committed list (name + email), call out advanced/tentative people separately, confirm notes+tags landed, and name the next decision (which sprint to generate plans against). Don't silently fold tentative or advanced members into the committed cohort.
