---
name: ai-shipping-labs-plan-from-onboarding
description: Use when asked to create or update an AI Shipping Labs sprint plan from a member's submitted onboarding answers, CRM/user context, or member profile; includes fetching production onboarding data, drafting a markdown plan, importing it through the production plans API, and attaching internal context to the member profile/CRM.
---

# AI Shipping Labs Plan From Onboarding

Create a human-prepared sprint plan from production onboarding and CRM context.
Use production APIs only; never use local SQLite as production truth.

## Inputs

Identify these before writing:

- Member email. Resolve fuzzy names with `GET /api/users?q=...`.
- Sprint slug. If absent, inspect current/upcoming sprints and missing-plan rows with `scripts/find_missing_sprint_plans.py`.
- Source context: submitted onboarding response, CRM/user detail, existing notes/plans, and any explicit user request.

Read `ai-shipping-labs-prod-api` for auth/safe-write rules and `ai-shipping-labs-users` for user/CRM endpoints.
Read `ai-shipping-labs-plan-import` before importing a markdown plan.

## Fetch Context

Load the API token inline from `.env`; do not print it.

```bash
TOKEN=$(grep -E '^API_SHIPPING_LABS_API_TOKEN=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
```

Find the member:

```bash
curl -s -H "Authorization: Token $TOKEN" \
  "https://aishippinglabs.com/api/users?q=Pavlo&limit=10" | python3 -m json.tool
```

Fetch onboarding:

```bash
curl -s -H "Authorization: Token $TOKEN" \
  "https://aishippinglabs.com/api/onboarding/responses/member@example.com" \
  | python3 -m json.tool
```

Fetch user detail:

```bash
curl -s -H "Authorization: Token $TOKEN" \
  "https://aishippinglabs.com/api/users/member@example.com" | python3 -m json.tool
```

For CRM aggregate lookup, prefer the fast filtered endpoint once available:

```bash
curl -s -H "Authorization: Token $TOKEN" \
  "https://aishippinglabs.com/api/crm/export?email=member@example.com"
```

Until that ships, avoid broad `scope=all&count=5000` exports unless the user explicitly needs a bulk export.

## Draft The Plan

Use the existing markdown format consumed by `scripts/import_sprint_plan_markdown.py`.
Read `references/plan-markdown-format.md` when drafting.

Plan from the member's stated goal, not from generic curriculum. Keep the member-facing sections direct and concrete:

- Summary: current situation, six-week goal, main gap, hours, why this plan.
- Focus: one main focus plus a few supporting focuses.
- Timeline: week-by-week checkpoints matching the sprint duration.
- Resources: specific links/books/courses only when they serve the build.
- Deliverables: artifacts the member can show.
- Accountability: cadence, demos, and check-ins.
- Next Steps: immediate actions.

Put private interpretation into internal sections, not member-facing plan text:

- Persona
- Background
- Initial Input or Questions and Answers
- Internal Recommendations
- Internal Action Items
- Sources

## Import Safely

Dry-run first:

```bash
python scripts/import_sprint_plan_markdown.py \
  --sprint <sprint-slug> \
  --email member@example.com \
  --source .tmp/plans/member-plan.md \
  --create-if-missing \
  --dry-run
```

Check the parsed payload: goal, week count, checkpoint count, resources, deliverables, next steps, and internal notes.

Then import:

```bash
python scripts/import_sprint_plan_markdown.py \
  --sprint <sprint-slug> \
  --email member@example.com \
  --source .tmp/plans/member-plan.md \
  --create-if-missing
```

Verify after write with `GET /api/plans/<id>` or rerun the importer dry-run and inspect the existing plan.

## Guardrails

- Never include confidential CRM notes in member-facing plan sections.
- Preserve the member's own wording in internal notes when useful.
- Do not invent sprint enrollment or identity; confirm email and sprint first.
- If onboarding is missing or draft, do not create a full plan unless the user explicitly says to proceed anyway.
- If an existing plan exists, update it only after inspecting it.
- Use `.tmp/` for local draft plan files.
