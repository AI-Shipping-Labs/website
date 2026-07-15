# Calendly

Calendly powers Request a call Phase 2 (#884) for Alexey. Valeria remains on
the plain Google appointment link because that scheduler has no equivalent API.

## Flow

1. A staff member configures a personal access token or uses **Connect
   Calendly**. OAuth uses one-time state, PKCE S256, and the minimum
   `scheduled_events:read webhooks:write` scopes.
2. OAuth access/rotating refresh tokens are stored as secret integration
   settings. The platform validates `/users/me`, discovers the organization,
   and idempotently finds or creates the organization webhook subscription.
3. `POST /api/webhooks/calendly` requires a valid, current HMAC signature.
   Accepted payloads receive a durable replay fingerprint and are recorded in
   `WebhookLog` before processing.
4. `invitee.created` records a `BookedCall`, resolves primary or alias email to
   the canonical active member, and increments matched host capacity.
   `invitee.canceled` decrements capacity. Cancel-before-create produces a
   terminal tombstone so late delivery cannot resurrect a canceled event.
5. Matched calls appear on the CRM record. An unmatched scheduling URL still
   creates a nullable-host record for diagnosis, but changes no host capacity.

Processing failures return 500 for provider retry and remain visible/replayable;
they are never acknowledged and silently dropped. Automatic replay runs every
five minutes. Manual recovery is available with:

```
uv run python manage.py retry_calendly_webhooks --limit 100
```

Webhook logs are also visible in Django admin.

## Settings

### calendly_access_token

Secret personal or OAuth access token. OAuth tokens refresh automatically before
their two-hour expiry.

### calendly_refresh_token

Managed OAuth secret. Calendly refresh tokens are single-use; every successful
refresh atomically replaces this value. Do not edit it manually.

### managed_oauth_state

`CALENDLY_ACCESS_TOKEN_EXPIRES_AT`, `CALENDLY_CONNECTED_USER_URI`,
`CALENDLY_ORGANIZATION_URI`, and `CALENDLY_WEBHOOK_SUBSCRIPTION_URI` are managed
diagnostic values written by Connect Calendly / Verify subscription.

### calendly_webhook_signing_key

Secret Calendly OAuth-app signing key. It is mandatory in every environment;
an unset key rejects every webhook.

### calendly_oauth_client_id

OAuth application client ID. Register
`{SITE_BASE_URL}/studio/integrations/calendly/callback` as the exact redirect.

### calendly_oauth_client_secret

OAuth application client secret.

### calendly_webhook_tolerance_seconds

Maximum accepted signature age or future clock skew, default 300 seconds.

### calendly_webhook_retention_days

Processed raw delivery payloads are pruned after this many days, default 30.
Failed deliveries remain until recovered so failures are not silently lost.

## Operator checks

- **Connect Calendly** performs authorization, identity validation, and
  subscription provisioning.
- **Verify subscription** is an idempotent staff POST action for token and
  subscription health.
- OAuth failure/expiry directs the operator to reconnect; invalid rotating
  refresh credentials are cleared rather than retried indefinitely.
- A real OAuth round trip and real booking remain the two `[HUMAN]` criteria on
  #884. Do not mark them complete from mocked tests.
