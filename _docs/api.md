# API Reference

The machine-readable endpoint catalogue lives at `/api/docs` (Swagger UI, staff only) or in [`_docs/openapi.json`](openapi.json). Per-feature walkthroughs:

- Sprint plans export: [`_docs/api-export-sprint-plans.md`](api-export-sprint-plans.md)

This document focuses on the User Management API surface (issue #764).

## Authentication

Every API endpoint accepts `Authorization: Token <key>` (not `Bearer`). The token must belong to a staff user. Mint tokens from Studio under `/studio/api-tokens/`.

```bash
export API_TOKEN="<your-staff-token>"
```

## User Management API

Programmatic access to the operator-only questions that today need a Studio session or `manage.py shell`. Read endpoints surface user state, SES history, and email-log; writes are narrow and audited.

## Shared comments API

Staff operators can discover shared first-party discussions across course
units, workshop tutorial pages, sprint plans, and Book Club notes. The list is
flat, newest first, and includes replies as separate rows. Unknown owner UUIDs
remain visible with `content_type=unknown` and `context=null` for audit, but
cannot receive API replies. Every row carries `moderation_state` (`visible` or
`hidden`, issue #1894): hidden rows left the public thread but keep their
votes and replies, and `?moderation_state=visible|hidden` filters one state
(default remains both, so operators can find what to restore).

```bash
curl -sL -H "Authorization: Token $API_TOKEN" \
  "https://aishippinglabs.com/api/comments?course_slug=aihero&module_slug=day-1&unit_slug=frontmatter&unanswered=true&limit=100"

curl -sL -H "Authorization: Token $API_TOKEN" \
  "https://aishippinglabs.com/api/comments?moderation_state=hidden&limit=100"
```

Generic filters include `content_type`, `content_id`, `kind`, `parent_id`,
`author_email`, `since`, `until`, `unanswered`, `limit`, and `offset`.
Owner-specific filters for different thread types cannot be mixed. Timestamp
filters require RFC 3339 offsets; invalid and contradictory filters return
`422 validation_error` rather than an unfiltered result.

A homework unit with the homework stepper enabled binds Q&A to the stepper
page (issues #1897 and #1925): `intro` and every question step own one derived
thread each with a live composer; `learning-in-public` shows no Q&A; `review`
shows the unit's own `content_id` thread read-only, as the `Earlier homework
discussion` archive (pre-#1897 whole-homework comments, early intro comments,
and the comments once posted on the retired `review` / `learning-in-public`
step threads). A `course_slug`/`module_slug`/`unit_slug` filter returns the
unit thread and every homework step thread of that unit, all as
`content_type=course_unit`. Each homework-step row's `context` adds
`homework_step` (the public step slug; the unit thread of a stepper unit
reports `review`, and a non-stepper unit thread omits the field) and a `url`
that opens that step's canonical path plus `#qa-section`; `title` appends the
step's sidebar label. Isolate one step with the `homework_step` query
parameter, which requires the course/module/unit triple: `intro` and a
question id return that step's thread, `review` returns the unit thread, and
`learning-in-public` returns an empty list:

```bash
curl -sL -H "Authorization: Token $API_TOKEN" \
  "https://aishippinglabs.com/api/comments?course_slug=ai-buildcamp&module_slug=foundation&unit_slug=homework&homework_step=q2-reflect"
```

The browser comments API (`GET`/`POST /api/comments/<content_id>`) is
unchanged: stepper pages simply mount the step's own thread UUID.

Post one direct reply with a staff token and a caller-chosen idempotency key:

```bash
curl -sL -X POST \
  -H "Authorization: Token $API_TOKEN" \
  -H "Idempotency-Key: agent-run-20260908-412" \
  -H "Content-Type: application/json" \
  -d '{"body":"Here is the answer."}' \
  https://aishippinglabs.com/api/comments/412/replies
```

The token owner is always the author. Bodies are plain text: HTML and Markdown
are stored and returned verbatim and remain escaped on browser comment
surfaces. A new reply returns `201` with `idempotent_replay=false`; repeating
the same parent and normalized body with the same token and key returns the
original reply with `200` and `idempotent_replay=true`. Reusing the key for a
different request returns `409 idempotency_key_reused`. After an uncertain
network result, retry explicitly with the same key.

### Staff moderation: edit, hide, restore (issue #1894)

Three staff-token routes moderate one comment or reply in place. None of them
uses `Idempotency-Key` and none sends a notification or creates a row.

`POST /api/comments/{id}/edit` replaces the body (`{"body": "..."}` only):
trimmed, non-empty, at most 10,000 code points, stored verbatim and still
escaped as plain text on the public thread. The row keeps its id, votes,
author, and parent; the public thread shows a muted `Edited` marker after the
timestamp. Hidden comments are editable through the token (for example to
strip a spoiler before restoring).

`POST /api/comments/{id}/hide` soft-hides the comment from every public
listing for every viewer. The row, its votes, and its API-reply audit records
stay intact; hiding a top-level comment also removes its nested replies from
the public thread unless they were independently hidden. Idempotent: hiding an
already hidden row succeeds and stays hidden.

`POST /api/comments/{id}/restore` makes a hidden comment visible again;
replies that were not independently hidden come back with it. Idempotent.
There is no restore capability on the public thread or through a browser
session -- this route is operator-token only.

```bash
curl -sL -X POST -H "Authorization: Token $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"body":"Please discuss the approach without posting the numbers."}' \
  https://aishippinglabs.com/api/comments/412/edit

curl -sL -X POST -H "Authorization: Token $API_TOKEN" \
  https://aishippinglabs.com/api/comments/412/hide

curl -sL -X POST -H "Authorization: Token $API_TOKEN" \
  https://aishippinglabs.com/api/comments/412/restore
```

Errors: unknown ids return `404 comment_not_found`; blank, oversized, or
extra fields return `422 validation_error`; missing or non-staff tokens
return `401`. The same `/edit` and `/hide` paths also accept a staff browser
session with CSRF (that is how the public Q&A thread's Edit and Delete
buttons work, issue #1894); a member session gets `403`, and hidden comments
return `404` on session calls so their existence does not leak through the
public routes. `restore` rejects browser sessions with `401`. CLI
equivalents: `uv run asl comments edit|hide|restore`.

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

### Read and rename the contact-tag namespace

List every in-use contact tag with its user count, sorted by name:

```bash
curl -sL -H "Authorization: Token $API_TOKEN" \
  https://aishippinglabs.com/api/contact-tags
```

The response shape is `{"tags": [{"name": "paid", "user_count": 2}],
"count": 1}`. Unused tag rows are removed, so the list only contains tags
currently assigned to users.

Rename one tag across all users with the same normalization and merge behavior
as Studio:

```bash
curl -sL -X POST \
  -H "Authorization: Token $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"new": "paid"}' \
  https://aishippinglabs.com/api/contact-tags/paid-user/rename
```

The response is `{"old": "paid-user", "new": "paid", "affected": 2}`.
A new name that normalizes to empty returns 422. Both endpoints require a
staff token and return 401 otherwise. Global tag deletion remains available
only through `/studio/tags/`; there is no contact-tag delete API.

### Write: grant a tier override (bulk)

```bash
curl -sL -X POST \
  -H "Authorization: Token $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"emails": ["a@example.com", "b@example.com"], "tier": "main"}' \
  https://aishippinglabs.com/api/tier-overrides
```

Grants a long-lived `TierOverride` (10-year expiry) to each listed email, deactivating any existing active override for that user (issue #833). `tier` is optional and defaults to `main`. Staff token required. This mirrors the Studio contact-import override but is an explicitly-named endpoint so the privileged grant is discoverable and cannot be triggered by accident. Route: `api/urls.py` `tier-overrides` -> `api_tier_overrides_grant`.

## Subscription reconciliation API

Staff-token-only endpoints that compare live Stripe subscription truth with
website access for the whole Stripe cohort (issue #1308). Read endpoints never
change member state; writes stay on the apply endpoint behind an explicit
confirmation. Full state contract, cadence, and classifications:
`_docs/integrations/stripe.md#subscription-reconciliation-live-stripe-vs-website-access`.

### Read: synchronous single/cohort live check (backward-compatible)

```bash
curl -sL -H "Authorization: Token $API_TOKEN" \
  "https://aishippinglabs.com/api/payments/tier-reconcile/diagnostics?email=someone@example.com"
```

`GET /api/payments/tier-reconcile/diagnostics` stays backward-compatible: it
only accepts `email` (single-user search) and `include=ok`. It does NOT accept
`tier`, `classification`, or cursor params, and it silently ignores any unknown
query params rather than returning 422. Tier/classification filtering and
pagination live on the persisted `runs` endpoints below and on the Studio
report, because that is where operators browse findings.

### Read: run history

```bash
curl -sL -H "Authorization: Token $API_TOKEN" \
  "https://aishippinglabs.com/api/payments/tier-reconcile/runs?page=1&page_size=100"
```

Returns completed/running/queued/failed runs newest-first with summary counts.
`next_cursor` is the next page number when more rows remain, else `null`.

### Read: run detail with filtered findings

```bash
# Filter one run's findings to Main-tier rows
curl -sL -H "Authorization: Token $API_TOKEN" \
  "https://aishippinglabs.com/api/payments/tier-reconcile/runs/<run_uuid>?classification=ended_subscription_still_entitled&tier=main"

# Page through findings via next_cursor
curl -sL -H "Authorization: Token $API_TOKEN" \
  "https://aishippinglabs.com/api/payments/tier-reconcile/runs/<run_uuid>?page=2&page_size=100"
```

`classification`, `tier` (`basic|main|premium|free`), and `filter`
(`actionable|scheduled|warnings|all`) combine as AND. An unknown `tier` or
`classification` returns the project-standard 422 with the offending field in
`details.field` (e.g. `{"code":"validation_error","details":{"field":"tier"}}`).

### Write: enqueue a read-only cohort run

```bash
curl -sL -X POST -H "Authorization: Token $API_TOKEN" \
  https://aishippinglabs.com/api/payments/tier-reconcile/runs
```

Returns `202` with `run_id` and `status`. This endpoint accepts no apply mode —
a run is always diagnostic and never changes member access.

### Write: confirmed apply

Applying deterministic drift requires `POST /api/payments/tier-reconcile` with
both `dry_run=false` and `confirm="apply_stripe_truth"` and explicit `emails`;
omitting `dry_run` previews without writes. See the Stripe integration doc for
the full apply contract.

All reconciliation endpoints require a staff token: a missing or non-staff token returns
`401` before any Stripe call, DB query, or task enqueue (no run is created).

## Stripe webhook diagnostics API

The existing staff-token-only diagnostics surface covers all eleven required
snapshot webhook events: the original six, two delayed-Checkout events, and
three refund/dispute review events. `POST /api/payments/stripe-webhooks/verify`
performs a read-only endpoint check. The status endpoint
(`GET /api/payments/stripe-webhooks/status`) returns the latest check and
canonical `required_events` list, preserves `cancellation_attempt_counts`, and
adds separate `refund_review_attempt_count` and `dispute_review_attempt_count`
totals.

`GET /api/payments/stripe-webhooks/deliveries` lists secret-free delivery
evidence. In addition to existing event/customer/subscription fields, each row
includes bounded `stripe_charge_id`, `stripe_invoice_id`, and
`stripe_dispute_id`. Filters accept `event_type`, `outcome` (including
`review_required`), `stripe_event_id`, `customer_id`, `subscription_id`,
`charge_id`, `invoice_id`, `dispute_id`, pagination, and the existing
`cancellation=true` scope. Authentication occurs before any operational read.
Responses never expose raw Stripe payloads, signatures, API/signing secrets,
receipt URLs, payment-method/card data, or member email from the attempt model.

`POST /api/payments/stripe-webhooks/replay` remains cancellation-only. It does
not resolve, cancel, or replay refund/dispute events and never changes member
access for them. Resend those events from Stripe after diagnosis; terminal
event-ID idempotency prevents duplicate alerts. If staff review determines
membership should end, cancel the subscription in Stripe and let the verified
`customer.subscription.deleted` callback apply the authoritative transition.

## Monthly payment-grace API

The staff-token-only read API exposes no payment mutation:

```bash
curl -sL -H "Authorization: Token $API_TOKEN" \
  "https://aishippinglabs.com/api/payments/payment-graces?status=active&tier=main&delivery_status=failed&page=1&page_size=100"

curl -sL -H "Authorization: Token $API_TOKEN" \
  "https://aishippinglabs.com/api/payments/payment-graces/<grace_uuid>"
```

The collection supports `status`, `user`, `email`, base `tier`, `interval`,
`source`, `delivery_status`, `started_from`, `started_to`, `page`, and
`page_size`. Filters combine as AND; invalid enumerations/dates/pagination use
the project-standard 422 with `details.field`. Rows expose stable safe Stripe
IDs, original/current base tier, effective tier, source/status, original and
rollout-safe deadlines, checks/review state, and delivery outcomes including
transport-start evidence for a crash-ambiguous send. Missing,
invalid, or non-staff tokens return 401 before payment-grace data is read.

Reconciliation run findings include the same stable latest-invoice,
interval/count, grace classification/action/timestamps, and base/effective-tier
fields. There is intentionally no API to mark paid, force expiry, or extend
grace; payment truth is repaired in Stripe and courtesy access uses
`TierOverride`.

## Content lookup API

`GET /api/content/<content_id>` resolves any synced content row by its
frontmatter `content_id` UUID and returns metadata, source provenance, stored
markdown, and stored HTML. It uses the same lookup and "is public" rule as the
public `/c/<uuid>` share link. It is read-only (other methods return `405`),
staff-token only (member API keys and non-staff tokens get `401`), and returns
drafts, unpublished, and tier-gated rows in full.

```bash
curl -sL -H "Authorization: Token $API_TOKEN" \
  "https://aishippinglabs.com/api/content/7c9e6679-7425-40de-944b-e07fc1f90ae7"

# Metadata only: omits markdown/html/homework_* and returns markdown_length/html_length
curl -sL -H "Authorization: Token $API_TOKEN" \
  "https://aishippinglabs.com/api/content/7c9e6679-7425-40de-944b-e07fc1f90ae7?include_body=false"
```

CLI equivalent: `uv run asl content get <uuid> [--no-body] [--body markdown|html]`.

| Field | Notes |
|-------|-------|
| `type` | `article`, `project`, `tutorial`, `download`, `workshop`, `workshop_page`, `marketing_page`, `event`, `course`, `course_module`, `course_unit` |
| `url` / `share_url` | Absolute canonical URL and absolute `/c/<uuid>` link |
| `slug` | Row slug; `public_path` for marketing pages |
| `is_public` | Whether `/c/<uuid>` would redirect anonymous visitors |
| `required_level` | Effective level (units and workshop pages resolve inheritance); null when the model has none |
| `context` | Parent slugs for units, modules, and workshop pages; `{}` otherwise |
| `source` | `repo`, `path`, `commit`, `github_url`; each null when unknown |
| `updated_at` | ISO-8601; null for course modules and units |

Body field mapping:

| Type | `markdown` | `html` |
|------|------------|--------|
| `article`, `project`, `tutorial`, `marketing_page` | `content_markdown` | `content_html` |
| `workshop`, `event`, `course` | `description` | `description_html` |
| `workshop_page` | `body` | `body_html` |
| `course_module` | `overview` | `overview_html` |
| `course_unit` | `body` | `body_html`, plus `homework_markdown` / `homework_html` |
| `download` | `description` | null |

`include_body` accepts `true`/`1` (default) or `false`/`0`; any other value
returns `422 validation_error`.

`source.commit` is the row's own stored commit, i.e. the commit of the last
sync that wrote this row. It is not the content source's latest synced commit
(`asl sync sources`), so a row that was unchanged in later syncs keeps an older
commit. For course modules and units, `source.repo` comes from the owning
course. `github_url` is built only when repo, path, and commit are all known.

Errors: unknown UUID returns `404 content_not_found`. A UUID used by more than
one row returns `409 content_id_ambiguous` with a `matches` list of `type`,
`id`, `title`, and `url` for each colliding row.

## Pods API

Staff-token endpoints for pods (issue #1918): small groups inside a dated
course cohort. Pairing logic stays in your own tooling; the site never pairs
people automatically. Every write follows the same rules as the member pages
and Studio: capacity, the waiting list (a freed seat moves the oldest
waitlisted request back to `pending`, nobody is auto-approved), and the leave
rules. Errors use the canonical `{"error", "code"}` envelope; a missing or
non-staff token returns `401`.

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/courses/<slug>/cohorts/<key>/members` | Pairing roster: every enrolled user with `tier`, `tags`, `preferred_timezone`, Slack flags, `crm`, `availability`, `pod_ids`, `open_request_pod_ids`. `limit` (default 200, max 500) and `offset`. `404 unknown_course` / `unknown_cohort` |
| GET | `/api/pods` | Pod summaries; filters `course`, `cohort` (needs `course`), `status`; `limit`/`offset` |
| POST | `/api/pods` | Create (`source=api`), `201`. Idempotent: a non-archived pod with the same name (case-insensitive) in the cohort is returned with `200` and only new emails are added; its settings and owner stay unchanged (use PATCH). `results` buckets: `added`, `not_enrolled`, `unknown_user`, `already_member`, `over_capacity`. Self-paced cohort: `422 validation_error` |
| GET | `/api/pods/<id>` | Settings, members, open requests with waiting-list position, `slack_channel_url`, `meeting_url`, `meetings` (every meeting that is not cancelled), `suggested_slots` (UTC) |
| PATCH | `/api/pods/<id>` | `name`, `purpose`, `max_members` (`422` below member count), `meeting_count` (`422` below the meetings already planned or held), `meeting_minutes`, `status`, `owner_email` (a member or `null`), `slack_channel_url`, `meeting_url` (the pod call link: https only, max 500, empty clears) |
| POST | `/api/pods/<id>/members` | `{"emails": [...]}`; idempotent; same result buckets; closes added users' open requests as `approved` |
| DELETE | `/api/pods/<id>/members/<email>` | Leave rules apply; `204`; `404 not_a_member` |
| GET | `/api/pods/<id>/requests` | Every request, optional `status` filter. Each request has `status`, `decided_at`, `decided_by`, and `stale_alerted_at` (when the daily staff Slack alert first reported it as stale, else `null`) |
| POST | `/api/pods/<id>/requests/<request_id>/approve` | Staff override; `409 pod_full`, `409 request_not_open` |
| POST | `/api/pods/<id>/requests/<request_id>/decline` | Staff override; `409 request_not_open` |
| GET | `/api/pods/<id>/meetings` | Every meeting (issue #1919), optional `status` filter (`proposed`, `scheduled`, `held`, `cancelled`). Each has `id`, `number` (`null` when cancelled or expired), `starts_at`, `ends_at`, `duration_minutes`, `timezone`, `status`, `expired`, `series_id`, `created_via`, `proposed_by`, `moved_by`, `moved_at`, `previous_starts_at`, `reminder_sent_at`, `responses` (`email`, `response`) |
| POST | `/api/pods/<id>/meetings` | Schedule agreed meetings (`created_via=api`), notifies members. `starts_at` (ISO 8601 with offset, quarter hour, 1 hour to 180 days ahead), `timezone` (IANA), `repeat_weekly` (default `false`), `count` (default 1, or every remaining meeting with `repeat_weekly`). `201`; `409 meeting_limit_reached`; `422 validation_error` for a bad time, an overlap or a bad field |
| PATCH | `/api/pods/<id>/meetings/<meeting_id>` | Either `starts_at` (+ optional `timezone`, default the meeting's zone, and `move_later`) to move, or `status` (`scheduled`, `held`, `cancelled`) as a staff override. Returns the changed meetings. `409 meeting_not_movable` (past, held or cancelled), `409 meeting_limit_reached`, `404 unknown_meeting` (another pod's meeting), `422 validation_error` |

There is no pod DELETE: archive with `PATCH {"status": "archived"}` so the
request history is kept. There is no meeting DELETE either: cancel with
`PATCH {"status": "cancelled"}` so the history stays.

Meeting times are a UTC instant plus the IANA zone they were set in. Weekly
repeats keep the same local wall-clock time in that zone per date, so a
series at 18:00 Berlin stays at 18:00 Berlin across the October clock change
(16:00 UTC before, 17:00 UTC after).

```bash
# Roster for pairing (Cohort 4 of the Buildcamp course)
curl -sL -H "Authorization: Token $API_TOKEN" \
  "https://aishippinglabs.com/api/courses/ai-buildcamp/cohorts/4/members?limit=200"

# Create a pod for two students your tooling paired
curl -sL -X POST -H "Authorization: Token $API_TOKEN" -H "Content-Type: application/json" \
  -d '{"course_slug": "ai-buildcamp", "cohort_key": "4", "name": "Agents pod",
       "purpose": "Ship an agent demo", "owner_email": "anna@example.com",
       "member_emails": ["anna@example.com", "raj@example.com"]}' \
  "https://aishippinglabs.com/api/pods"

# Raise the size limit (moves waitlisted requests back to pending)
curl -sL -X PATCH -H "Authorization: Token $API_TOKEN" -H "Content-Type: application/json" \
  -d '{"max_members": 5}' "https://aishippinglabs.com/api/pods/7"

# Re-pair: remove one member, add another
curl -sL -X DELETE -H "Authorization: Token $API_TOKEN" \
  "https://aishippinglabs.com/api/pods/7/members/raj@example.com"
curl -sL -X POST -H "Authorization: Token $API_TOKEN" -H "Content-Type: application/json" \
  -d '{"emails": ["mike@example.com"]}' "https://aishippinglabs.com/api/pods/7/members"

# Approve a stale request as staff
curl -sL -X POST -H "Authorization: Token $API_TOKEN" \
  "https://aishippinglabs.com/api/pods/7/requests/12/approve"

# Schedule the pod's remaining meetings weekly at 18:00 Berlin time
curl -sL -X POST -H "Authorization: Token $API_TOKEN" -H "Content-Type: application/json" \
  -d '{"starts_at": "2026-10-13T18:00:00+02:00", "timezone": "Europe/Berlin", "repeat_weekly": true}' \
  "https://aishippinglabs.com/api/pods/7/meetings"

# Move meeting 31 and the later meetings of its series to Thursdays 17:00 Berlin
curl -sL -X PATCH -H "Authorization: Token $API_TOKEN" -H "Content-Type: application/json" \
  -d '{"starts_at": "2026-10-15T17:00:00+02:00", "timezone": "Europe/Berlin", "move_later": true}' \
  "https://aishippinglabs.com/api/pods/7/meetings/31"

# Mark a past meeting as held
curl -sL -X PATCH -H "Authorization: Token $API_TOKEN" -H "Content-Type: application/json" \
  -d '{"status": "held"}' "https://aishippinglabs.com/api/pods/7/meetings/30"
```

Settings live in the `Pods` IntegrationSetting group; see
[`_docs/integrations/pods.md`](integrations/pods.md).

## Not exposed (Studio-only)

By design, the API does NOT expose:

- `DELETE /api/users/<email>` -- destructive; Studio only.
- Email rename -- PII change with cascading effects on Stripe / Slack.
- Password change or reset -- Studio reset flow only.
- Automatic tier change from payments -- Stripe webhooks own the paid-subscription lifecycle. Manual/bulk grants ARE exposed via `POST /api/tier-overrides` (see above), which writes an audited `TierOverride`.

## Audit trail

Every write (`PATCH`, tag POST, tag DELETE) appends one `CommunityAuditLog` row whose `user` FK is the SUBJECT user and whose `details` text contains `actor_token=<token name or masked key>`. Browse audit history in Studio under the user-detail audit tab.
