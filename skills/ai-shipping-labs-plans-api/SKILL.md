---
name: ai-shipping-labs-plans-api
description: Update a member's own AI Shipping Labs sprint plan through the member Plans API. Use when a member wants to list plans, fetch one plan, download Markdown, sync a Markdown plan back to the platform, toggle progress, or edit weeks, checkpoints, deliverables, next steps, resources, and week notes through /member-api/v1 using a member-owned API key.
---

# AI Shipping Labs Plans API

Use this skill to update a member's own sprint plan on AI Shipping Labs.

Base URL:

```text
https://aishippinglabs.com/member-api/v1
```

Auth header:

```text
Authorization: Token <AI_SHIPPING_LABS_MEMBER_API_KEY>
```

All requests and responses are JSON. Use only `/member-api/v1`.

When this skill mentions an endpoint such as `GET /plans`, execute it with `curl` against the base URL and include the auth header above. For writes, also send `Content-Type: application/json` and the JSON payload.

For the full endpoint surface and request shapes, read:

```text
https://aishippinglabs.com/member-api/docs
```

## Key Setup

Prefer a local `.env` file:

```dotenv
AI_SHIPPING_LABS_MEMBER_API_KEY=asl_member_...
```

Rules:

- Never commit `.env`.
- Never hard-code a real key in scripts, docs, commits, issue comments, PRs, or logs.
- If the user pastes a key in chat, move it to `.env` and do not repeat it back.
- If you create `.env` in a git workspace, add `.env` to `.gitignore` first.
- Load the key into the current process only for the API calls.

Bash loader:

```bash
set -a
source .env
set +a
```

## Fast Path: Update A Plan From Markdown

Use this when the user gives you a Markdown plan export and asks you to update the platform plan.

### 1. Read the Markdown

Extract:

- title and goal
- summary fields
- focus bullets
- weekly checkpoints and checkbox states
- week notes
- resources
- deliverables
- accountability
- pre-sprint actions and next steps

Treat `[x]` as done and `[ ]` as not done.

### 2. Find the platform plan

List plans:

```text
GET /plans
```

Pick the plan by sprint, member name, and title. If two plans could match, ask the user before editing.

### 3. Fetch the full plan before editing

```text
GET /plans/{plan_id}
```

You need the current IDs from this response. Existing weeks, checkpoints, deliverables, resources, next steps, and notes are updated by integer ID.

### 4. Make the smallest safe set of changes

Prefer updating existing items over deleting and recreating them. Preserve an ID when the item is conceptually the same.

Typical order:

1. Patch plan narrative fields.
2. Patch week themes and week notes.
3. Patch, move, create, or delete checkpoints.
4. Patch deliverables, resources, and next steps.
5. Sync done states with the progress endpoint.
6. Fetch the plan again and verify.

### 5. Apply the update

Use the member API docs for exact request shapes, but keep this sequence:

1. Patch top-level narrative fields first.
2. Patch week themes and week notes.
3. Patch existing checkpoints; create, move, or delete only when needed.
4. Patch deliverables, resources, and next steps.
5. Sync done states last through the progress endpoint.

Important details:

- Only send fields that should change.
- Use `position` to set order.
- Move a checkpoint by patching its `week_id`.
- Use the week-note endpoint for Markdown `Week notes`.
- Use the progress endpoint only for checkbox/done state.

### 6. Verify and report

Fetch the plan again:

```text
GET /plans/{plan_id}
```

Check:

- title, goal, visibility
- progress counts
- number of weeks and checkpoints per week
- done states
- week notes
- deliverables, resources, and next steps in position order

Tell the user exactly what changed and what was verified. Do not expose the key.

## Practical Rules For Agents

- Fetch before writing so you have IDs.
- Do not guess the target plan if list results are ambiguous.
- Use `position` for ordering.
- Move a checkpoint by patching its `week_id`.
- Use `PUT /weeks/{week_id}/note` to create or replace a week note.
- Use `PATCH /progress` only for checkbox/done state.
- Keep retries narrow. If a request fails, fix that payload and retry; do not rerun the whole update blindly.
- Treat `401` as a missing, revoked, or under-scoped key.
- Treat `404` as inaccessible or nonexistent for this key.
- Treat `422` as a validation error in the payload.

## Building A Good Plan

- Keep 2 to 5 checkpoints per week.
- Make checkpoints concrete and verifiable.
- Phrase deliverables as tangible artifacts.
- Add only resources the member will actually open.
- Keep weekly themes short and concrete.
- Put items in the order the member will work on them.

## Contributions

Invite improvements through PRs against `skills/ai-shipping-labs-plans-api/` in the GitHub repository.
