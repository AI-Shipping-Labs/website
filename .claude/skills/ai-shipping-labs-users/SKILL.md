---
name: ai-shipping-labs-users
description: Use when asked to look up a user, inspect a user's tier/subscription/bounce state, add or remove a CRM note or tag, merge duplicate accounts, add an email alias, mark a user bounced, import/export contacts, or record people in the AI Shipping Labs CRM. The user + CRM read/write surface of the production API.
metadata:
  short-description: Users + CRM — look up users, tags, notes, aliases, merges, contacts
---

# Users + CRM

Read and write the AI Shipping Labs user/CRM surface over the production HTTP API: user state, tags, member notes, aliases, account merges, deliverability, and bulk contacts.

## Auth

All calls go through the `asl` CLI, which resolves the token from `.env` automatically. See `ai-shipping-labs-prod-api` for full auth and the safe-write protocol (GET-before, write, GET-after).

## Discovering commands

```bash
uv run asl users --help                # see all subcommands
uv run asl users notes add --help      # see flags for any specific command
```

## User lookup

- `asl users list -q <substring>` — search over email, name, Stripe id, Slack id, tags.
- `asl users get <email>` — full state: tier, tier_override, stripe/bounce/slack state, tags, aliases, etc.

A `404` means there is no platform account for that email — surface it, do not invent one.

- `asl users patch <email> --unsubscribed` / `--email-verified` — safe writes (email_verified can only be set to true).

## Tags (`asl users tags --help`)

- `asl users tags add <email> <tag>`
- `asl users tags remove <email> <tag>`

Cohort-tag conventions: use a tag like `llm-zoomcamp-2026` for committed members, and a `-interested` variant (e.g. `llm-zoomcamp-2026-interested`) for tentative people, so the committed group filters cleanly.

## Notes (`asl users notes --help`)

- `asl users notes list <email>` — returns `{"interview_notes":[...]}`, NOT `notes` / `results`.
- `asl users notes add <email> --body "..." [--kind <kind>] [--visibility internal|external] [--plan-id N]`
- `kind` enum: `action_item`, `background`, `general`, `intake`, `meeting`, `persona`, `recommendation`, `source`.
- Use `visibility internal` for CRM/operator notes. Always cite the source and quote the person's own words in `body`.

## Aliases and merge (`asl users aliases --help`, `asl users merge --help`)

- `asl users aliases add <email> --alias-email <alias> [--note "..."]` — an alias routes future mail / Stripe events to the canonical user.
- `asl users aliases remove <email> <alias>`
- `asl users merge --canonical-email <email> --merge-email <email> [--dry-run] [--force]`

Merging is hard to reverse. Always `--dry-run` first and confirm identity before a real merge. Confirm both rows are the same person (same Slack id, Stripe id, or name) before folding.

## Deliverability

- `asl users mark-bounced <email> --bounce-type permanent|soft [--reason "..."] [--diagnostic "..."]`
- `asl users email-log <email> [--limit N] [--kind ...]`
- `asl users ses-events <email> [--limit N] [--type ...]`

## Contacts (bulk)

- `asl contacts import --data '{"contacts":[...], "default_tag":"...", "default_tier":"..."}'` — bulk upsert. Watch for duplicates.
- `asl contacts export [--format json|csv]`
- `asl contacts set-tags <email> --tags tag1,tag2` — replaces the tag set (not additive).

## Recording people from Slack

- The end-to-end thread->CRM flow (read thread, classify intent, write notes + tags): `slack-thread-to-crm`.
- Resolving Slack handles / display names to emails: `ai-shipping-labs-slack`.
- This skill is the CRM write/read surface those recipes call.
