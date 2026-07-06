---
name: ai-shipping-labs-users
description: Use when asked to look up a user, inspect a user's tier/subscription/bounce state, add or remove a CRM note or tag, merge duplicate accounts, add an email alias, mark a user bounced, import/export contacts, or record people in the AI Shipping Labs CRM. The user + CRM read/write surface of the production API.
metadata:
  short-description: Users + CRM — look up users, tags, notes, aliases, merges, contacts
---

# Users + CRM

Read and write the AI Shipping Labs user/CRM surface over the production HTTP API: user state, tags, member notes, aliases, account merges, deliverability, and bulk contacts. All endpoints are under `https://aishippinglabs.com/api`.

## Auth

Token, base URL, OpenAPI spec, and the safe-write protocol (GET-before, write, GET-after) live in `ai-shipping-labs-prod-api`. All calls go through the `asl` CLI, which resolves the token from `.env` automatically. Never print or commit the token.

## User lookup

### Search: `asl users-list`

Substring search over email, name, Stripe customer id, Slack user id, and tags. Supports `--limit` and `--since` (ISO timestamp). Returns `{"users":[...], "count":N, "limit":N}`. Each row is the summary subset (`email`, `first_name`, `last_name`, `display_name`, `tier`, `tier_override_active`, `unsubscribed`, `soft_bounce_count`, `bounce_state`, `email_verified`, `slack_member`, `slack_user_id`, `stripe_customer_id`, `subscription_id`, `date_joined`, `last_login`).

```bash
uv run asl users-list -q alexey --format table
```

### Detail: `asl users-get`

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

```bash
uv run asl users-get someone@example.com
```

A `404` / `detail` means there is no platform account for that email — surface it, do not invent one.

`asl users-patch` accepts only `email_verified` (true only; demote-via-API is forbidden) and `unsubscribed`.

## Tags

- Add: `asl users-tags-add <email> <tag>`
- Remove: `asl users-tags-remove <email> <tag>`

Cohort-tag conventions: use a course-cohort tag like `llm-zoomcamp-2026` for committed members, and a distinct `-interested` variant (e.g. `llm-zoomcamp-2026-interested`) for tentative people, so the committed group filters cleanly.

## Member notes

- List: `asl users-notes <email>`
- Create: `asl users-notes-add <email> --body "..." --kind <kind> --visibility <visibility>`
- `kind` enum: `action_item`, `background`, `general`, `intake`, `meeting`, `persona`, `recommendation`, `source`.
- `visibility` enum: `external`, `internal`. Use `internal` for CRM/operator notes.

Always cite the source and quote the person's own words in `body`. Apply the GET-before/after rule (these are prod writes).

Verify gotcha: `asl users-notes` returns `{"interview_notes":[...]}`, NOT `notes` / `results`. Each note has `id`, `user_email`, `plan_id`, `visibility`, `kind`, `body`, `created_by_email`, `created_at`, `updated_at`.

## Aliases and merge

- Add alias: `asl users-aliases-add <email> --alias-email <alias> [--note "..."]`. An alias routes future mail / Stripe events to the canonical user.
- Remove alias: `asl users-aliases-remove <email> <alias>`.
- Merge: `asl users-merge --canonical-email <email> --merge-email <email> [--dry-run] [--force]`. Folds the `merge_email` duplicate into the `canonical_email` account.

Merging is hard to reverse. Always `--dry-run` first and confirm identity before a real merge — this is the lesson from the two duplicate Ostrovnoy accounts created by a contacts import. Confirm both rows are the same person (same Slack id, Stripe id, or name) before folding.

## Deliverability

- Mark bounced: `asl users-mark-bounced <email> --bounce-type permanent|soft [--reason "..."] [--diagnostic "..."]`
- Email log: `asl users-email-log <email> [--limit N] [--kind ...]`
- SES events: `asl users-ses-events <email> [--limit N] [--type ...]`

## Contacts (bulk)

- Import: `asl contacts-import '{"contacts":[...], "default_tag":"...", "default_tier":"..."}'` — bulk upsert. Watch for duplicates: importing a contact whose email differs from an existing account creates a separate user.
- Export: `asl contacts-export [--format json|csv]` — JSON returns `{"contacts":[...]}`.
- Set tags: `asl contacts-set-tags <email> '{"tags":[...]}'` — replaces the contact's tag set (not additive).

## Recording people from Slack

- The end-to-end thread->CRM flow (read thread, classify intent, write notes + tags): `slack-thread-to-crm`.
- Resolving Slack handles / display names to emails: `ai-shipping-labs-slack`.
- This skill is the CRM write/read surface those recipes call.
