---
name: ai-shipping-labs-plans-api
description: Build, read, and iterate a member's own AI Shipping Labs sprint plan through the member Plans API. Use when a member wants to list their plans, fetch one plan, download plan Markdown, toggle checkpoint / deliverable / next-step progress, or reshape a plan end to end (edit narrative fields; add, rename, reorder, move, or remove weeks, checkpoints, deliverables, next steps, resources, and week notes) through /member-api/v1 using a member-owned API key.
---

# AI Shipping Labs Plans API

Use the member API only. The base URL is:

```text
https://aishippinglabs.com/member-api/v1
```

Authenticate every request with:

```text
Authorization: Token <asl_member_...>
```

All requests and responses are JSON. The key belongs to one member and only ever reaches that member's own plans.

## Key Handling

- Ask the user for a member API key when no local key is available.
- Prefer reading `AI_SHIPPING_LABS_MEMBER_API_KEY` from the local environment.
- Never hard-code a key.
- Never write a key into repo files, logs, issue comments, commits, or generated docs.
- Never ask the user to commit or store a key in the repository.

## Scopes

A key carries a set of scopes; new keys are created with all three by default.

- `plans:read` - read the owner's plans.
- `plans:write_progress` - toggle `done` on existing items via `PATCH .../progress`.
- `plans:write` - edit plan content (narrative fields and every collection below).

The scopes are checked independently. A content-editing request made with a key that lacks `plans:write` returns `401`.

## Plan Data Model

A plan is owned by one member for one sprint. Its shape:

- Plan narrative fields: `title` (max 280), `goal` (max 280), `summary` (`current_situation`, `goal`, `main_gap`, `weekly_hours`, `why_this_plan`), `focus` (`main`, `supporting` list of strings), `accountability`, and `visibility` (`private` or `cohort`; `public` is reserved).
- `weeks`: ordered weekly blocks. Each week has a `week_number` (positive, unique per plan), a `theme`, a `position`, at most one `note`, and a list of `checkpoints`.
- `checkpoints`: bullets that belong to a week. Each has a `description`, a `position`, and a `done_at` timestamp (null until done). Move one to another week by setting its `week_id`.
- `deliverables`, `next_steps`, `resources`: plan-level collections. Deliverables and next steps have a `description`, `position`, and `done_at`. Next steps also have a `kind` of `pre_sprint` (default) or `next_step`. Resources have a `title` (max 300), optional `url` (must be valid, max 600), `note`, and `position`.

Identifiers: every row has an integer `id`. Ordering within a collection is by `position` ascending (ties break by id / week number), so reorder by setting `position`. There is no bulk-reorder endpoint yet.

## Allowed Operations

Read:

- List plans: `GET /member-api/v1/plans`.
- Fetch one plan: `GET /member-api/v1/plans/{plan_id}`.
- Download Markdown: `GET /member-api/v1/plans/{plan_id}/markdown`.

Toggle progress only (`plans:write_progress`):

- `PATCH /member-api/v1/plans/{plan_id}/progress` sets `done` on existing checkpoints, deliverables, and next steps. It never creates, deletes, reorders, renames, moves, or edits descriptions.

```json
{
  "checkpoints": [{"id": 123, "done": true}],
  "deliverables": [{"id": 456, "done": false}],
  "next_steps": [{"id": 789, "done": true}]
}
```

Edit plan content (`plans:write`):

- Narrative: `PATCH /member-api/v1/plans/{plan_id}` - partial update of `title`, `goal`, `summary.*`, `focus.*`, `accountability`, `visibility`. Only supplied keys change; returns the full plan detail.
- Weeks: `POST /member-api/v1/plans/{plan_id}/weeks`, `PATCH /member-api/v1/plans/{plan_id}/weeks/{week_id}`, `DELETE /member-api/v1/plans/{plan_id}/weeks/{week_id}` (delete cascades the week's checkpoints and note).
- Checkpoints: `POST /member-api/v1/plans/{plan_id}/weeks/{week_id}/checkpoints`, `PATCH /member-api/v1/plans/{plan_id}/checkpoints/{checkpoint_id}` (supply `week_id` to move it to another week of the same plan), `DELETE /member-api/v1/plans/{plan_id}/checkpoints/{checkpoint_id}`.
- Deliverables: `POST /member-api/v1/plans/{plan_id}/deliverables`, `PATCH /member-api/v1/plans/{plan_id}/deliverables/{deliverable_id}`, `DELETE /member-api/v1/plans/{plan_id}/deliverables/{deliverable_id}`.
- Next steps: `POST /member-api/v1/plans/{plan_id}/next-steps`, `PATCH /member-api/v1/plans/{plan_id}/next-steps/{next_step_id}`, `DELETE /member-api/v1/plans/{plan_id}/next-steps/{next_step_id}`.
- Resources: `POST /member-api/v1/plans/{plan_id}/resources`, `PATCH /member-api/v1/plans/{plan_id}/resources/{resource_id}`, `DELETE /member-api/v1/plans/{plan_id}/resources/{resource_id}`.
- Week note: `PUT /member-api/v1/plans/{plan_id}/weeks/{week_id}/note` (upsert, `body` required, records the owner as author), `DELETE /member-api/v1/plans/{plan_id}/weeks/{week_id}/note`.

Create payloads: a week needs `week_number`; a checkpoint / deliverable / next step needs `description`; a resource needs `title`; a week note needs `body`. `position` and `done` are optional on create.

## Payload Shapes

Edit narrative fields:

```json
{
  "title": "Ship an eval harness",
  "goal": "A working evaluation harness",
  "summary": {"goal": "A reusable eval harness", "weekly_hours": "6h"},
  "focus": {"main": "Evaluation", "supporting": ["Tracing", "CI"]},
  "accountability": "Weekly demo",
  "visibility": "cohort"
}
```

Create a checkpoint (POST to a week):

```json
{"description": "Draft the eval rubric", "position": 0, "done": false}
```

Move a checkpoint to another week and mark it done (PATCH):

```json
{"week_id": 35, "done": true}
```

Create a next step:

```json
{"description": "Read the tracing docs", "kind": "pre_sprint"}
```

Upsert a week note:

```json
{"body": "Shipped the harness; blocked on flaky evals."}
```

## Building A Good Plan

- Structure weeks around themes that build on each other (for a 6-week sprint: discovery, build, harden, ship). Give each week a short, concrete `theme`.
- Keep 2 to 5 checkpoints per week - small, verifiable, outcome-oriented ("Wire the eval harness to CI"), not vague ("work on evals").
- Phrase deliverables as the tangible artifacts the sprint produces ("A reusable eval harness with 20 cases").
- Use `next_steps` with `kind: pre_sprint` for setup the member does before the sprint starts, and `kind: next_step` for follow-ups after it ends.
- Add resources for the links and references the member will actually open, with a short `note` on why each matters.
- Set `position` deliberately so the plan reads top to bottom in the order the member will work.

## Iterate Loop

Reshape a plan in a read-modify-write loop until it matches what the member wants:

1. `GET /member-api/v1/plans/{plan_id}` to see the current structure and every `id`.
2. Decide the smallest set of changes (edit narrative, add/rename/move/remove items).
3. Apply them with the `plans:write` calls above. Each write is atomic, so a rejected request leaves the plan untouched.
4. `GET` the plan again to confirm the result, then repeat.

## Forbidden Surfaces

Do not call `/api/`, `/studio/`, Django admin, CRM, staff APIs, operator APIs, or staff/operator documentation. Do not infer, request, or expose internal notes, CRM notes, onboarding answers, staff context, or other members' data. Only edit the plan the member asked you to work on.

## Workflow

1. Resolve the key from `AI_SHIPPING_LABS_MEMBER_API_KEY` or ask the user to provide it privately.
2. Set `BASE_URL=https://aishippinglabs.com/member-api/v1`.
3. Make the requested member API call with `Authorization: Token <key>`.
4. Before editing items, fetch the plan so you know the current ids and order.
5. Treat `401` as a missing, revoked, or under-scoped key; ask the user for a fresh key.
6. Treat `404` as "this key owner cannot access that plan".
7. Treat `422` as a validation error (bad field, duplicate `week_number`, `public` visibility, invalid `kind`, invalid `url`, or a child id from another plan); fix the payload and retry.

## Examples

List plans:

```bash
curl -sS \
  -H "Authorization: Token $AI_SHIPPING_LABS_MEMBER_API_KEY" \
  https://aishippinglabs.com/member-api/v1/plans
```

Add a week, then a checkpoint in it:

```bash
curl -sS -X POST \
  -H "Authorization: Token $AI_SHIPPING_LABS_MEMBER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"week_number": 1, "theme": "Discovery"}' \
  https://aishippinglabs.com/member-api/v1/plans/12/weeks

curl -sS -X POST \
  -H "Authorization: Token $AI_SHIPPING_LABS_MEMBER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"description": "Draft the eval rubric"}' \
  https://aishippinglabs.com/member-api/v1/plans/12/weeks/34/checkpoints
```

Download Markdown:

```bash
curl -sS \
  -H "Authorization: Token $AI_SHIPPING_LABS_MEMBER_API_KEY" \
  -o plan.md \
  https://aishippinglabs.com/member-api/v1/plans/12/markdown
```

## Contributions

Invite improvements through PRs against `skills/ai-shipping-labs-plans-api/` in the GitHub repository.
