---
name: ai-shipping-labs-plans-api
description: Work with the AI Shipping Labs member Plans API from local tools or agents. Use when a member wants to list their own sprint plans, fetch one plan, download plan Markdown, or update checkpoint, deliverable, and next-step progress through /member-api/v1 using a member-owned API key.
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

## Key Handling

- Ask the user for a member API key when no local key is available.
- Prefer reading `AI_SHIPPING_LABS_MEMBER_API_KEY` from the local environment.
- Never hard-code a key.
- Never write a key into repo files, logs, issue comments, commits, or generated docs.
- Never ask the user to commit or store a key in the repository.

## Allowed Operations

- List plans: `GET /member-api/v1/plans`.
- Fetch one plan: `GET /member-api/v1/plans/{plan_id}`.
- Download Markdown: `GET /member-api/v1/plans/{plan_id}/markdown`.
- Update progress: `PATCH /member-api/v1/plans/{plan_id}/progress`.

Use `PATCH /progress` only to set `done` on existing checkpoints, deliverables, and next steps:

```json
{
  "checkpoints": [{"id": 123, "done": true}],
  "deliverables": [{"id": 456, "done": false}],
  "next_steps": [{"id": 789, "done": true}]
}
```

## Forbidden Surfaces

Do not call `/api/`, `/studio/`, Django admin, CRM, staff APIs, operator APIs, or staff/operator documentation. Do not infer, request, or expose internal notes, CRM notes, onboarding answers, staff context, or other members' data.

## Workflow

1. Resolve the key from `AI_SHIPPING_LABS_MEMBER_API_KEY` or ask the user to provide it privately.
2. Set `BASE_URL=https://aishippinglabs.com/member-api/v1`.
3. Make the requested member API call with `Authorization: Token <key>`.
4. For progress updates, fetch the plan first when item IDs are unknown.
5. Treat `401` as a missing, revoked, or invalid key; ask the user for a fresh key.
6. Treat `404` as "this key owner cannot access that plan".
7. Treat `422` as a validation error and fix the payload before retrying.

## Examples

List plans:

```bash
curl -sS \
  -H "Authorization: Token $AI_SHIPPING_LABS_MEMBER_API_KEY" \
  https://aishippinglabs.com/member-api/v1/plans
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
