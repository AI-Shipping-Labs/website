---
name: ai-shipping-labs-users
description: Use when asked to look up a user, inspect a user's tier/subscription/bounce state, add or remove a CRM note or tag, merge duplicate accounts, add an email alias, mark a user bounced, import/export contacts, or record people in the AI Shipping Labs CRM. The user + CRM read/write surface of the production API.
metadata:
  short-description: Users + CRM — look up users, tags, notes, aliases, merges, contacts
---

# Users + CRM

Read and write the AI Shipping Labs user/CRM surface over the production HTTP API: user state, tags, member notes, aliases, account merges, deliverability, and bulk contacts. All endpoints are under `https://aishippinglabs.com/api`.

## Auth

Token, base URL, OpenAPI spec, and the safe-write protocol (GET-before, write, GET-after) live in `ai-shipping-labs-prod-api`. Read the token inline; never print or commit it.

```bash
cd /home/alexey/git/ai-shipping-labs
TOKEN=$(grep -E '^API_SHIPPING_LABS_API_TOKEN=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
```

Header `Authorization: Token <key>` (literal scheme `Token`). No trailing slash on paths. These run against PRODUCTION.

## User lookup

### Search: `GET /api/users?q=`

Substring search over email, name, Stripe customer id, Slack user id, and tags. Supports `limit` and `since` (ISO timestamp). Returns `{"users":[...], "count":N, "limit":N}`. Each row is the summary subset (`email`, `first_name`, `last_name`, `display_name`, `tier`, `tier_override_active`, `unsubscribed`, `soft_bounce_count`, `bounce_state`, `email_verified`, `slack_member`, `slack_user_id`, `stripe_customer_id`, `subscription_id`, `date_joined`, `last_login`).

### Detail: `GET /api/users/{email}`

Full state. Real payload fields:

- `email`, `first_name`, `last_name`, `display_name`
- `tier` — object `{"slug":"main","level":20}`
- `tier_override`, `tier_override_active` — manual tier grant + whether it is currently in effect
- `stripe_customer_id`, `subscription_id`
- `slack_member`, `slack_user_id`
- `email_verified`, `unsubscribed`, `email_preferences`
- `bounce_state` (e.g. `none`), `soft_bounce_count`
- `tags` — list of strings
- `aliases` — list of alias emails folded into this account
- `import_metadata` — provenance (e.g. a `slack` block with tz, ids)
- `date_joined`, `last_login`

Example reading tier + subscription state (the surface used to diagnose the Kir billing case):

```bash
curl -s -H "Authorization: Token $TOKEN" "https://aishippinglabs.com/api/users/kkrotov.kir@gmail.com" \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print('tier',d['tier'],'override_active',d['tier_override_active'],'sub',d['subscription_id'],'bounce',d['bounce_state'])"
```

A `404` / `detail` means there is no platform account for that email — surface it, do not invent one.

`PATCH /api/users/{email}` accepts only `email_verified` (true only; demote-via-API is forbidden) and `unsubscribed`.

## Tags

- Add: `POST /api/users/{email}/tags` body `{"tag":"..."}`.
- Remove: `DELETE /api/users/{email}/tags/{tag}`.

Colons in tag names (e.g. `stripe:churned`) must be URL-encoded as `%3A` in the DELETE path:

```bash
curl -s -X DELETE -H "Authorization: Token $TOKEN" \
  "https://aishippinglabs.com/api/users/$EMAIL/tags/stripe%3Achurned"
```

Cohort-tag conventions: use a course-cohort tag like `llm-zoomcamp-2026` for committed members, and a distinct `-interested` variant (e.g. `llm-zoomcamp-2026-interested`) for tentative people, so the committed group filters cleanly.

## Member notes

- Create: `POST /api/member-notes` body `{"user_email","body","kind","visibility"}`. Optional `plan_id` (integer, nullable) to attach the note to a plan.
- `kind` enum: `action_item`, `background`, `general`, `intake`, `meeting`, `persona`, `recommendation`, `source`.
- `visibility` enum: `external`, `internal`. Use `internal` for CRM/operator notes.

Always cite the source and quote the person's own words in `body`. Apply the GET-before/after rule (these are prod writes).

Verify gotcha: `GET /api/users/{email}/notes` returns `{"interview_notes":[...]}`, NOT `notes` / `results`. Each note has `id`, `user_email`, `plan_id`, `visibility`, `kind`, `body`, `created_by_email`, `created_at`, `updated_at`. (`/api/users/{email}/interview-notes` is an alias of the same list.)

## Aliases and merge

- Add alias: `POST /api/users/{email}/aliases` body `{"alias_email","note"}` (`note` optional). An alias routes future mail / Stripe events to the canonical user.
- Remove alias: `DELETE /api/users/{email}/aliases/{alias_email}`.
- Merge: `POST /api/users/merge` body `{"canonical_email","merge_email","dry_run","force"}`. Folds the `merge_email` duplicate into the `canonical_email` account.

Merging is hard to reverse. Always `dry_run: true` first and confirm identity before a real merge — this is the lesson from the two duplicate Ostrovnoy accounts created by a contacts import. Confirm both rows are the same person (same Slack id, Stripe id, or name) before folding.

## Deliverability

- `POST /api/users/{email}/mark-bounced` body `{"bounce_type","diagnostic","reason"}` (`bounce_type` is `permanent` or `soft`; mirrors the SES webhook).
- `GET /api/users/{email}/email-log` — outbound email-log rows (`limit`, `since`, `kind`).
- `GET /api/users/{email}/ses-events` — inbound SES events (`limit`, `since`, `type`).

## Contacts (bulk)

- `POST /api/contacts/import` body `{"contacts":[...], "default_tag", "default_tier"}` — bulk upsert. Each contact is an object (email + name + tags etc.). Watch for duplicates: importing a contact whose email differs from an existing account creates a separate user (see the Ostrovnoy merge note above).
- `GET /api/contacts/export?format=json|csv` — JSON returns `{"contacts":[...]}`; each contact has `email`, `first_name`, `last_name`, `tags`, `tier`, `email_verified`, `unsubscribed`, `date_joined`, `last_login`, `stripe_customer_id`, `subscription_id`, `slack_member`, `slack_checked_at`.
- `POST /api/contacts/{email}/tags` body `{"tags":[...]}` — replaces the contact's tag set (not additive).

## Recording people from Slack

- The end-to-end thread->CRM flow (read thread, classify intent, write notes + tags): `slack-thread-to-crm`.
- Resolving Slack handles / display names to emails: `ai-shipping-labs-slack`.
- This skill is the CRM write/read surface those recipes call.
