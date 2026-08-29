---
name: ai-shipping-labs-plan-from-onboarding
description: Use when asked to create or update an AI Shipping Labs sprint plan from a member's submitted onboarding answers, CRM/user context, or member profile; includes fetching production onboarding data, drafting a markdown plan, importing it through the production plans API, and attaching internal context to the member profile/CRM.
---

# AI Shipping Labs Plan From Onboarding

Create a human-prepared sprint plan from production onboarding and CRM context.
Use production APIs only; never use local SQLite as production truth.

## Inputs

Identify these before writing:

- Member email. Resolve fuzzy names with `uv run asl users list --query <name>`.
- Sprint slug. If absent, inspect current/upcoming sprints and missing-plan rows with `scripts/find_missing_sprint_plans.py`.
- Source context: submitted onboarding response, CRM/user detail, existing notes/plans, and any explicit user request.

Read `ai-shipping-labs-prod-api` for auth/safe-write rules and `ai-shipping-labs-users` for user/CRM endpoints.
Read `ai-shipping-labs-plan-import` before importing a markdown plan.

## Fetch Context

Use the `asl` CLI for production API calls. It resolves the staff token
(`ASL_API_TOKEN` -> `.env` `API_SHIPPING_LABS_API_TOKEN` -> prompt) and the base URL;
do not print the token.

Find the member:

```bash
uv run asl users list --query Pavlo --limit 10
```

Fetch onboarding:

```bash
uv run asl onboarding response member@example.com
```

Fetch user detail:

```bash
uv run asl users get member@example.com
```

For CRM aggregate lookup, use the fast filtered endpoint:

```bash
uv run asl raw GET /api/crm/export -p email=member@example.com
```

For a single member, the typed alternative is `uv run asl users crm-record member@example.com`.
Avoid broad `scope=all&count=5000` exports unless the user explicitly needs a bulk export.

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

Write the member-facing plan directly to the member:

- Use second person (`you`, `your`) throughout. Do not describe the member as `she`, `he`, `the member`, or by name in shareable sections.
- Include only concrete steps the member needs to take. Do not include staff or coach tasks, owner labels such as `[Nicole]` or `[Alexey]`, unchecked-checkbox syntax such as `[ ]`, editing controls, or other planning boilerplate.
- Put staff coordination and private follow-up in `Internal Action Items`; keep `Timeline`, `Resources`, `Deliverables`, `Accountability`, and `Next Steps` usable by the member without internal context.
- When a course is the main course for the sprint, explain what it teaches and why it is in the plan. For example: `AI Hero — the main course for this sprint, covering the coding tools and how to build with them. Run it in parallel to help you decide how much AI engineering you want to pursue.`

Put private interpretation into internal sections, not member-facing plan text:

- Persona
- Background
- Initial Input or Questions and Answers
- Internal Recommendations
- Internal Action Items
- Sources

## Import Safely

Every invocation, dry-run included, must choose exactly one delivery intent:
`--send-ready-email` (notify the member after the content write succeeds) or
`--no-ready-email` (save content only, leave readiness untouched). The importer
refuses to run without one of them.

Dry-run first:

```bash
python scripts/import_sprint_plan_markdown.py \
  --sprint <sprint-slug> \
  --email member@example.com \
  --source .tmp/plans/member-plan.md \
  --create-if-missing \
  --no-ready-email \
  --dry-run
```

Check the parsed payload: goal, week count, checkpoint count, resources, deliverables, next steps, and internal notes. A dry run never creates, patches, shares, or notifies.

Then import. Use `--send-ready-email` when the plan is reviewed and the member should be notified now:

```bash
python scripts/import_sprint_plan_markdown.py \
  --sprint <sprint-slug> \
  --email member@example.com \
  --source .tmp/plans/member-plan.md \
  --create-if-missing \
  --send-ready-email
```

Use `--no-ready-email` when the plan still needs review; share it later from
`/studio/plans/<id>/` or with `uv run asl plans send-ready <id>`.

Delivery runs only after the content PATCH succeeds, and it is idempotent:
re-running after an ambiguous outcome reports `already_sent` instead of
notifying twice. A `failed_retryable` result exits non-zero and means the
content is saved but the plan is still unshared.

Verify after write with `uv run asl plans get <id>` or rerun the importer dry-run and inspect the existing plan. Confirm `shared_at` is set when you intended to notify the member — an unshared plan still shows the member the "Your plan is being prepared" card.

## Guardrails

- Never include confidential CRM notes in member-facing plan sections.
- Preserve the member's own wording in internal notes when useful.
- Do not invent sprint enrollment or identity; confirm email and sprint first.
- If onboarding is missing or draft, do not create a full plan unless the user explicitly says to proceed anyway.
- If an existing plan exists, update it only after inspecting it.
- Use `.tmp/` for local draft plan files.
- Never re-send a plan-ready email to "make sure" it arrived. Default delivery is idempotent and reports `already_sent`/`already_shared`; only the confirmed Studio `Re-share with member` action notifies a member a second time.
