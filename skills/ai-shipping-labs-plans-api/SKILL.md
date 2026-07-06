---
name: ai-shipping-labs-plans-api
description: Work with the AI Shipping Labs member Plans API from local tools or agents. Use when a member wants to list their own sprint plans, fetch one plan, download plan Markdown, or update checkpoint, deliverable, and next-step progress through /member-api/v1 using a member-owned API key.
---

# AI Shipping Labs Plans API

Use the member API via the `asl` CLI (member scope). The CLI handles the base URL and token resolution.

## Discovering commands

```bash
uv run asl member-api --help
uv run asl member-api plans --help
```

## Key Handling

- Ask the user for a member API key when no local key is available.
- Prefer reading `AI_SHIPPING_LABS_MEMBER_API_KEY` from the local environment.
- Never hard-code a key. Never write a key into repo files, logs, issue comments, commits, or generated docs.
- Never ask the user to commit or store a key in the repository.

## Allowed Operations

- List plans: `uv run asl member-api plans list`
- Fetch one plan: `uv run asl member-api plans get <plan_id>`
- Download Markdown: `uv run asl member-api plans markdown <plan_id>`
- Update progress: `uv run asl member-api plans progress <plan_id> --data '{"checkpoints":[{"id":123,"done":true}],...}'`

Use the progress command only to set `done` on existing checkpoints, deliverables, and next steps. The `--data` payload accepts arrays of `{"id": N, "done": true/false}` for each key.

## Forbidden Surfaces

Do not call `/api/`, `/studio/`, Django admin, CRM, staff APIs, operator APIs, or staff/operator documentation. Do not infer, request, or expose internal notes, CRM notes, onboarding answers, staff context, or other members' data.

## Error Handling

1. Resolve the key from `AI_SHIPPING_LABS_MEMBER_API_KEY` or ask the user to provide it privately.
2. Treat `401` as a missing, revoked, or invalid key; ask the user for a fresh key.
3. Treat `404` as "this key owner cannot access that plan".
4. Treat `422` as a validation error and fix the payload before retrying.

## Contributions

Invite improvements through PRs against `skills/ai-shipping-labs-plans-api/` in the GitHub repository.
