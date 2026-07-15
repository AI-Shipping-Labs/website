# Event triggers (outbound webhooks)

The `triggers` subsystem turns an emitted event into a signed outbound
webhook POST to an external handler. v0 credits is the first partner; each
future partnership is a new subscription row plus (optionally) a widget
row, with no core code changes (issue #1070).

## TRIGGERS_ENABLED

Master switch for the whole subsystem.

- Type: boolean (`true` / `false`).
- Default: `false`.
- When off: `emit_event` records nothing and dispatches no webhooks, and
  every claim widget shows a "claims are paused" state.
- Turn on once at least one `TriggerSubscription` points at a live
  handler.

Set it in Studio (`Operations -> Settings`, `Event triggers` group), or via
the `TRIGGERS_ENABLED` environment variable as a fallback.

## Subscriptions

A `TriggerSubscription` (Studio: `Operations -> Trigger subscriptions`)
maps an emitted event to an external handler:

- `event_type` — `custom` in v1 (widget-emitted events).
- `property_filter` — an exact-match JSON map. ALL keys must equal the
  emitted event's `properties` for the subscription to fire. An empty `{}`
  matches every event of that type. Example: `{"name": "v0_workshop"}`.
- `target_url` — an HTTPS handler on port 443. Literal/private/loopback/
  link-local/reserved addresses and hostnames resolving to any such address
  are rejected. Delivery pins the validated public peer IP while preserving
  TLS SNI/certificate and Host validation; redirects are never followed.
- `secret` — the HMAC signing secret shared with the handler. Write-only:
  it is encrypted at rest, masked everywhere, and never returned by the API.
  Rotations increment a visible secret version and retain the prior encrypted
  version for a 24-hour operator grace window.
- `is_active` — deactivate (never delete) to stop a subscription firing.

## Signature scheme

Each delivery POSTs the envelope (see below) with these headers:

- `X-AISL-Signature: sha256=<hex>` where
  `<hex> = hmac_sha256(secret, "<timestamp>.<raw_body>")`.
- `X-AISL-Timestamp: <unix-seconds>` — the same timestamp used in the
  signed string (so the handler can reject replays outside a tolerance
  window).
- `X-AISL-Event-Id: <envelope_id>` — the `evt_<uuid>` id for the handler's
  own dedup.
- `X-AISL-Secret-Version: <integer>` — the snapshotted signing-key version.

This mirrors the inbound GitHub webhook verification helper. The handler
recomputes the HMAC over `"<X-AISL-Timestamp>.<raw_body>"` and compares
with `hmac.compare_digest`.

## Envelope format

```json
{
  "event": "v0_workshop",
  "id": "evt_8f3c...",
  "occurred_at": "2026-06-23T12:00:00Z",
  "data": {
    "user_id": 123,
    "email": "member@example.com",
    "name": "Member Name",
    "min_level": 5,
    "properties": { "name": "v0_workshop" }
  }
}
```

## Observability

- `Operations -> Event emissions` — read-only log of recorded claims (user,
  event, envelope id, time).
- `Operations -> Webhook deliveries` — read-only log of each outbound
  attempt (status, HTTP code, success/failure), filterable by subscription
  and success.

Old `WebhookDelivery` rows are pruned by the daily
`cleanup-webhook-deliveries` scheduled job (30-day retention), reusing the
same wiring as the inbound webhook-log cleanup.

Each emission/subscription pair also owns a durable delivery job. Its target,
secret version, envelope id, `occurred_at`, and raw body are immutable. The
database leases one worker at a time, records at most four attempts (one plus
three retries with bounded backoff), and suppresses work after success. A
minute-level recovery schedule wakes due/expired leases independently of
django-q's global retry settings. Deactivating a subscription immediately
pauses queued attempts; reactivation lets the recovery schedule resume them.

## Authenticated API

Staff-gated, using either `Authorization: Token <key>` or a staff browser
session (unsafe session methods also require CSRF), all under `/api/`:

- `GET/POST /api/triggers/subscriptions`, `GET/PATCH /api/triggers/subscriptions/<id>`
- `GET/POST /api/triggers/widgets`, `GET/PATCH /api/triggers/widgets/<id>`
- `GET /api/triggers/emissions` (filter by `user`, `event_name`)
- `GET /api/triggers/deliveries` (filter by `subscription`, `succeeded`)

There is no DELETE endpoint — deactivate via `is_active`. Subscription and
widget secrets are accepted on write but never returned (responses expose
`has_secret` instead).

## Author surface

The content-embeddable claim widget is documented for authors in
`_docs/event-widgets.md`.

## Out of scope (infra repo)

The campaign fulfilment (code pool, SES send, per-partner dedup) lives in a
separate AWS Lambda tracked in `ai-shipping-labs-infra#12`, not in this
repo.
