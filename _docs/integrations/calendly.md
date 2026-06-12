# Calendly

Calendly powers the deepened "Request a call" integration (issue #884, Phase 2).
Phase 1 (#870) shipped a plain configurable scheduler link per host. Phase 2
captures booked calls back into the CRM and auto-maintains host availability.

This integration is Calendly-specific and applies to the Alexey host only.
Valeria stays on the plain configurable Google appointment-scheduling link from
Phase 1 because Google appointment scheduling has no equivalent booking API.

## How it works

1. Alexey configures a Calendly host access token (or authorizes via the
   optional OAuth flow). The platform uses that token to register a webhook
   subscription for `invitee.created` and `invitee.canceled`.
2. When a member books a call, Calendly POSTs `invitee.created` to
   `/api/webhooks/calendly`. The platform verifies the signature, matches the
   invitee email to a member, records a `BookedCall` row, and increments the
   host's `current_load` so `/request-a-call` availability reflects reality.
3. On `invitee.canceled`, the matching `BookedCall` is marked canceled and the
   host's `current_load` is decremented.
4. The booked call appears on the member's CRM record (issue #871).

Webhook handling is best-effort and signature-verified: a webhook failure logs
and returns 200 (so Calendly does not retry forever) and never corrupts CRM
data — capacity is only ever changed inside the same transaction that records
or cancels the call, and double-deliveries are idempotent on the Calendly event
URI.

## Settings

### calendly_access_token

Calendly host access token (a personal access token or an OAuth access token)
used to read scheduled events and create the webhook subscription. Get a
personal token from Calendly > Integrations > API & Webhooks. Without it the
platform cannot register the booked-call webhook or fetch event details.

### calendly_webhook_signing_key

Signing key Calendly returns when the webhook subscription is created. Verifies
that `invitee.created` / `invitee.canceled` callbacks really came from Calendly
via the `Calendly-Webhook-Signature` header (HMAC-SHA256 over `t=<timestamp>`
plus the raw body). When blank, webhook calls are rejected in production but
allowed locally for replay.

### calendly_oauth_client_id

Calendly OAuth app client ID. Used for the optional authorize-Calendly flow that
mints a host access token without pasting a personal token. Get it from Calendly
> Integrations > OAuth applications. The redirect URI to register is
`{SITE_BASE_URL}/studio/integrations/calendly/callback`.

### calendly_oauth_client_secret

Calendly OAuth app client secret paired with the client ID above. Required only
for the authorize flow.

### calendly_webhook_validation_enabled

Set true to require a valid `Calendly-Webhook-Signature` header on the
booked-call webhook (recommended in production). When false, signatures are not
enforced so local replay works without the signing key.

## Notes

- Webhook endpoint: `POST /api/webhooks/calendly`
- OAuth connect (staff-only): `GET /studio/integrations/calendly/connect`
- OAuth callback (staff-only): `GET /studio/integrations/calendly/callback`
- The OAuth round-trip against the real Calendly account is verified manually
  ([HUMAN] criteria on issue #884).
