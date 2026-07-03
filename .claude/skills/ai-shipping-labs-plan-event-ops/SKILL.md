---
name: ai-shipping-labs-plan-event-ops
description: Use when asked to update AI Shipping Labs sprint plans from Zoom, Slack, onboarding, questionnaire, or meeting notes; add or import missing sprint plans; clean plan task formatting; or fetch/check people registered for a production event.
---

# AI Shipping Labs Plan and Event Ops

## Overview

Use this skill for operator workflows that combine production sprint-plan updates with event attendance or registration lookups. Work against production through authenticated surfaces, preserve existing plan structure, and keep member-facing plan text clean.

## Required Context

Before taking action, read the relevant underlying skills:

- `ai-shipping-labs-prod-api` for auth, OpenAPI discovery, and the safe-write protocol.
- `ai-shipping-labs-users` when resolving members, emails, aliases, CRM notes, or Slack identities.
- `ai-shipping-labs-plan-import` when a plan is missing or must be imported from local markdown.
- `ai-shipping-labs-events` when resolving event slugs, IDs, public event details, or event-series context.

Never use local SQLite, Django shell data, fixtures, or a remote DB tunnel as production truth. Do not print API tokens.

## Plan Update Workflow

1. Identify the sprint slug and member email. Use the production API or the helper scripts from `ai-shipping-labs-plan-import`.
2. Read current state first: sprint enrollments, sprint plans, and the specific plan payload.
3. If a member clearly belongs in the sprint but is not enrolled, add the enrollment through the production API and verify it with a second GET.
4. If the member has no plan, prefer importing an existing local markdown plan from the known plan roots. Run the importer with `--dry-run`, inspect the shape, then run it live.
5. If updating an existing plan from meeting notes, prepare a minimal PATCH. Keep unchanged rows and IDs for nested collections such as weeks, checkpoints, resources, deliverables, and `next_steps`.
6. Put lossy or source-specific context in internal `interview_notes`; put only clear, useful action items and plan content in member-facing sections.
7. After every write, GET the plan again and verify the expected fields, counts, and representative text.

## Member-Facing Text Rules

- Do not prefix plan `next_steps`, checkpoints, deliverables, or resources with assignee labels such as `[Vancesca]`, `[Sai]`, `[Alexey]`, or `[The group]`.
- Do not preserve markdown checklist prefixes such as `[ ] [Name]` in production plan task descriptions.
- Phrase member-facing `next_steps` as clean actions, for example `Share the GitHub repository link in Slack so sprint members can test it.`
- If an action is for staff or the community rather than the plan owner, either move it to an internal note or rewrite it so the plan owner has a relevant action. Do not leave bracketed names to explain ownership.
- Before updating from generated meeting notes, first list proposed member-specific updates when the user asks for confirmation. Apply them only after confirmation, unless the user explicitly asks to update directly.

## Useful Commands

Read production plan summaries:

```bash
TOKEN=$(grep -E '^API_SHIPPING_LABS_API_TOKEN=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
curl -s -H "Authorization: Token $TOKEN" \
  https://aishippinglabs.com/api/sprints/july-2026/plans
```

Read one detailed plan:

```bash
curl -s -H "Authorization: Token $TOKEN" \
  https://aishippinglabs.com/api/plans/<plan_id> | python3 -m json.tool
```

Import a missing markdown plan:

```bash
python scripts/import_sprint_plan_markdown.py \
  --sprint july-2026 \
  --email member@example.com \
  --source ~/git/telegram-writing-assistant/articles/ai-shipping-labs/plans/YYYYMMDD_member.md \
  --create-if-missing \
  --dry-run
```

Then repeat without `--dry-run` after inspection.

## Event Registrant Lookup

First resolve the event from production:

```bash
curl -s -H "Authorization: Token $TOKEN" \
  "https://aishippinglabs.com/api/events?status=upcoming" | python3 -m json.tool
```

Then read the current OpenAPI spec before assuming a registrant-list endpoint exists:

```bash
curl -s -H "Authorization: Token $TOKEN" \
  https://aishippinglabs.com/api/openapi.json \
  | python3 -c "import json,sys; d=json.load(sys.stdin); [print(p) for p in sorted(d['paths']) if 'registr' in p.lower()]"
```

Current repo behavior: the token-authenticated JSON API exposes event details and counts, but event roster export is a Studio session-gated CSV route: `/studio/events/<event_id>/registrations.csv`. Read `references/event-registrants.md` before fetching rosters.

If you have a valid staff web session, use the Studio CSV export for exact registrants. If you only have the production API token and the OpenAPI spec has no roster endpoint, report that names cannot be fetched through the API, provide available counts from event details, and ask for a Studio export or create an issue to add an authenticated API endpoint.

## Safety

- Follow GET-before, write, GET-after for every production mutation.
- Use `.tmp/` for scratch scripts and downloaded CSVs.
- Do not expose secrets, signed cancellation links, private notes, or registrant personal data in chat beyond what the user requested.
- Keep production writes narrow. Do not rewrite whole plans from meeting summaries when a focused patch is enough.
