# API Reference

The machine-readable endpoint catalogue lives at `/api/docs` (Swagger UI, staff only) or in [`_docs/openapi.json`](openapi.json). Per-feature walkthroughs:

- Sprint plans export: [`_docs/api-export-sprint-plans.md`](api-export-sprint-plans.md)

This document focuses on the User Management API surface (issue #764).

## Authentication

Every API endpoint accepts `Authorization: Token <key>` (not `Bearer`). The token must belong to a staff user. Mint tokens from Studio under `/studio/tokens/`.

```bash
export API_TOKEN="<your-staff-token>"
```

## User Management API

Programmatic access to the operator-only questions that today need a Studio session or `manage.py shell`. Read endpoints surface user state, SES history, and email-log; writes are narrow and audited.

### Read: single-user state

```bash
curl -sL -H "Authorization: Token $API_TOKEN" \
  https://aishippinglabs.com/api/users/alice@example.com | python3 -m json.tool
```

Returns email, tier, unsubscribed flag, bounce state, tags, and identity fields. 404 with `{"error": "User not found", "code": "user_not_found"}` for unknown emails.

### Read: search / list

```bash
curl -sL -H "Authorization: Token $API_TOKEN" \
  "https://aishippinglabs.com/api/users?q=cus_AAA"
```

`q` matches email, first/last name, `stripe_customer_id`, `slack_user_id`, and substring inside tags. `limit` defaults to 50 and clamps to 200. `since` accepts an ISO-8601 datetime and filters on `date_joined`.

### Read: SES events for a user

```bash
curl -sL -H "Authorization: Token $API_TOKEN" \
  "https://aishippinglabs.com/api/users/bouncing@example.com/ses-events?type=bounce_permanent"
```

Filters on `SesEvent.user_id` (not `recipient_email`) so the history survives email renames. `raw_payload` is deliberately excluded -- the Studio surface owns the deep-dive.

### Read: outbound email log

```bash
curl -sL -H "Authorization: Token $API_TOKEN" \
  "https://aishippinglabs.com/api/users/alice@example.com/email-log?kind=campaign"
```

Each row carries the raw timing fields plus a derived `disposition` field summarising the strongest signal (`sent < delivered < opened < clicked < bounced < complained`).

### Write: unsubscribe a user

```bash
curl -sL -X PATCH \
  -H "Authorization: Token $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"unsubscribed": true}' \
  https://aishippinglabs.com/api/users/alice@example.com
```

Idempotent: re-issuing the same PATCH returns 200 with the same payload, and still writes an audit row (operator intent is auditable even when the state didn't change).

### Write: manually verify a user

```bash
curl -sL -X PATCH \
  -H "Authorization: Token $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"email_verified": true}' \
  https://aishippinglabs.com/api/users/lost@example.com
```

Clears `verification_expires_at` so the purge task cannot reclaim the row. Setting `email_verified: false` is rejected with 422 `verification_demote_forbidden`.

### Write: add or remove a tag

```bash
# Add (idempotent)
curl -sL -X POST \
  -H "Authorization: Token $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tag": "wave-2"}' \
  https://aishippinglabs.com/api/users/alice@example.com/tags

# Remove (idempotent)
curl -sL -X DELETE \
  -H "Authorization: Token $API_TOKEN" \
  https://aishippinglabs.com/api/users/alice@example.com/tags/wave-2
```

Tags are normalised via `accounts.utils.tags.normalize_tag`; empty input after normalisation returns 422 `invalid_tag`.

## Not exposed (Studio-only)

By design, the API does NOT expose:

- `DELETE /api/users/<email>` -- destructive; Studio only.
- Email rename -- PII change with cascading effects on Stripe / Slack.
- Password change or reset -- Studio reset flow only.
- Tier change -- Stripe webhooks own this; manual upgrades go through `TierOverride` (separate audit trail in Studio).

## Audit trail

Every write (`PATCH`, tag POST, tag DELETE) appends one `CommunityAuditLog` row whose `user` FK is the SUBJECT user and whose `details` text contains `actor_token=<token name or masked key>`. Browse audit history in Studio under the user-detail audit tab.
