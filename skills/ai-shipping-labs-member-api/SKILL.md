---
name: ai-shipping-labs-member-api
description: Catalog for the AI Shipping Labs member API. Use to discover which family fits the task (Plans, Book Club, or Events), then read its reference doc. Covers shared auth with a member-owned API key against /member-api/v1 — a member acting on their own data only, never Studio or staff endpoints.
---

# AI Shipping Labs Member API

This is the catalog for the AI Shipping Labs member API. The member API lets a
member act on their own data with a member-owned API key. It is limited to the
key owner: it never exposes Studio, staff, CRM, onboarding, or other members'
data.

Pick the family that matches the task and read its reference doc:

| Family | What it does | Sub-skill |
|--------|--------------|-----------|
| Plans | List, fetch, download, and edit the member's own sprint plans — weeks, checkpoints, deliverables, next steps, resources, week notes, and progress. | [`plans.md`](plans.md) |
| Book Club | Read and update the member's own Book Club activity — per-chapter read state, per-chapter notes (markdown), reading progress for a book, and the reader profile. | [`books.md`](books.md) |
| Events | Discover accessible events, fetch event detail, and register the key owner for one session or a whole series. | [`events.md`](events.md) |

Everything below is shared by every family.

Base URL:

```text
https://aishippinglabs.com/member-api/v1
```

Auth header:

```text
Authorization: Token <asl_member_...>
```

Use the value from `AI_SHIPPING_LABS_MEMBER_API_KEY` in place of the
placeholder when making requests.

All requests and responses are JSON. Use only `/member-api/v1`.

For the full endpoint surface and request shapes across every family, read:

```text
https://aishippinglabs.com/member-api/docs
```

Members create and manage their keys at `/account/#api-keys`.
Every active key has the same deployed member API capabilities. Members do not choose permissions and existing keys automatically work with newly added endpoint families.

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

## Safe API Surface

Every family stays inside `/member-api/v1` and acts only on the key owner's own
data. Do not call `/api/`, `/studio/`, Django admin, or staff-only endpoints
from any member-API skill. Member API keys cannot access CRM notes, onboarding answers, staff context, cohort teammates' plans, or other members' data.

## Error Handling (all families)

- Treat `401` as a missing, malformed, or revoked key.
- Treat `404` as inaccessible or nonexistent for this key.
- Treat `422` as a validation error in the payload.
- Keep retries narrow: fix the failing payload and retry that one request; do
  not rerun the whole workflow blindly.

## Contributions

Invite improvements through PRs against `skills/ai-shipping-labs-member-api/` in the GitHub repository.
