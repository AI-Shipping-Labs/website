---
name: ai-shipping-labs-users
description: Use when asked to look up a user, inspect a user's tier/subscription/bounce state, add or remove a CRM note or tag, merge duplicate accounts, add an email alias, mark a user bounced, import/export contacts, or record people in the AI Shipping Labs CRM. The user + CRM read/write surface of the production API.
metadata:
  short-description: Users + CRM — look up users, tags, notes, aliases, merges, contacts
---

# Users + CRM

Read and write the AI Shipping Labs user/CRM surface. See `ai-shipping-labs-prod-api` for auth and the safe-write protocol.

## Discovering commands

```bash
uv run asl users --help            # all subcommands
uv run asl users tag --help        # flags for a specific command
```

## User lookup

- `asl users list -q <substring>` — search over email, name, Stripe id, Slack id, tags.
- `asl users get <email>` — full state: tier, override, stripe/bounce/slack state, tags, aliases.
- `asl users patch <email> --unsubscribed` / `--email-verified` — safe writes (email_verified can only be set true).

A `404` means there is no platform account for that email.

## Tags

- `asl users tag <email> <tag>` — add a tag.
- `asl users untag <email> <tag>` — remove a tag.

Cohort convention: `llm-zoomcamp-2026` for committed members, `llm-zoomcamp-2026-interested` for tentative.

## Notes

- `asl users notes <email>` — list member notes (returns `{"interview_notes":[...]}`).
- `asl users add-note <email> --body "..." [--kind ...] [--visibility internal|external] [--plan-id N]`

## Aliases and merge

- `asl users add-alias <email> --alias-email <alias> [--note "..."]`
- `asl users remove-alias <email> <alias>`
- `asl users merge --canonical-email <email> --merge-email <email> [--dry-run] [--force]`

Always `--dry-run` a merge first and confirm identity (same Slack/Stripe id or name).

## Deliverability

- `asl users mark-bounced <email> --bounce-type permanent|soft [--reason "..."] [--diagnostic "..."]`
- `asl users email-log <email> [--limit N] [--kind ...]`
- `asl users ses-events <email> [--limit N] [--type ...]`

## Contacts

- `asl contacts import --data '{"contacts":[...]}'` — bulk upsert.
- `asl contacts export [--format json|csv]`
- `asl contacts set-tags <email> --tags tag1,tag2` — replaces (not additive).

## Recording people from Slack

- End-to-end flow: `slack-thread-to-crm`.
- Resolving Slack handles: `ai-shipping-labs-slack`.
