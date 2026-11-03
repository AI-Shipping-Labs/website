# Relay email

Relay is the sole owner of outbound email source, layout rendering, immutable
template versions, and provider delivery. The website supplies recipient
context, consent decisions, idempotency keys, and audit relationships.

Configure `RELAY_BASE_URL` and `RELAY_API_KEY` as process environment secrets.
Production startup checks fail when either is missing. Operators edit Relay
templates in Studio at `/studio/email-templates/`; the list, draft save,
publish, version history, and preview actions call Relay directly and have no
local source or rendering fallback.

All 61 named website templates and `campaign-broadcast` are pinned to immutable
version 2 in `email_app/relay_versions.py`. `raw_transactional` remains a
read-only version-1 compatibility template. Published Markdown templates use
`markdown_layout_context=true`, which lets Relay render the validated
`footer_note`, `verify_email_url`, and `unsubscribe_url` values and produce the
matching plaintext part.

## RELAY_TRANSACTIONAL_FROM_EMAIL

Sender for required account and service email. It must match a sender
registered in Relay. The default is `noreply@aishippinglabs.com`.

## RELAY_PROMOTIONAL_FROM_EMAIL

Sender for campaigns, newsletters, and marketing email. It must match a sender
registered in Relay. The default is `content@aishippinglabs.com`.

## RELAY_CALENDAR_FROM_EMAIL

Sender and iCalendar `ORGANIZER` for event calendar invites (registration,
series, reschedule, cancellation, calendar feed). Defaults to
`AI Shipping Labs <content@aishippinglabs.com>`. The local part must be a sender
id registered for the Relay client (`content`). Do not use `alexey@`: it is not
a Relay sender and invites must come from content@ (decision 2026-11-02). If Relay returns the specific
`from_email=not_configured` validation error, event-registration sends retry
once with the configured promotional sender and the same idempotency key. Other
Relay errors are not retried. Avoid `noreply@`: Gmail only auto-adds invites
from senders the invitee has interacted with.

## Calendar sender incident, 2026-11-01

Website commit `b54f14bba` changed the default calendar sender to
`Alexey Grigorev <alexey@aishippinglabs.com>`, which Relay resolves as sender
id `alexey`. Relay production client 4 (`aisl-website`) had only the sender IDs
`noreply`, `content`, and `welcome`; `alexey` was absent. Relay rejected the
request with HTTP 400:

```json
{"error":{"code":"validation_error","fields":{"from_email":"not_configured"}}}
```

The failure matched four production `POST /api/transactional/send` 400 log
entries at 03:05 UTC and a read-only `dry_run=true` replay of all five failed
registrations. The affected registrations were 2064–2068 for events 67, 68,
69, and 70. No messages were accepted between 2026-10-31 16:23 UTC and the
incident investigation. Separate Relay 404 responses for `template_not_found`
were not the cause of these registration failures.

Commit `6b5c2a92a` added the narrow fallback: when Relay returns that exact
400 field error for a calendar sender, the website retries once using `content`
and the same idempotency key. A warning is logged and the Relay health summary
reports the fallback for up to 24 hours. Once Relay registers `alexey`, the
preferred sender succeeds and the fallback is not used.

Before promoting a release, run the sender contract preflight with the target
environment's Relay credentials:

```bash
uv run python manage.py check_relay_health --check-ready --check-senders --json
```

The preflight reads the authenticated Relay client sender policy from
`GET /api/client/senders` and fails if any sender ID selected by this website
is missing. It does not change Relay configuration.

For the five failed registrations, the prepared retry command defaults to a
network-free dry run and reuses the event-registration idempotency keys:

```bash
uv run python manage.py retry_incident_event_registration_emails
```

Only run with `--send --approval-note "resend approved by Alexey"` after that
exact approval note has been added to intake `ca96488a`. The command is limited
to registrations 2064–2068, skips already accepted deliveries, and refuses any
record whose registration, delivery state, or event-registration key differs
from the incident record.

## RELAY_WELCOME_FROM_EMAIL

Sender for welcome email types. Welcome messages remain transactional. The
default is `welcome@aishippinglabs.com`.

## RELAY_WELCOME_REPLY_TO_EMAIL

Optional monitored Reply-To mailbox for welcome messages. Blank omits the
header. The default is `welcome@aishippinglabs.com`.

## RELAY_UNSUBSCRIBE_EMAIL

Optional mailto address added beside the HTTPS one-click unsubscribe URL in
campaign `List-Unsubscribe` headers.

## EMAIL_BATCH_SIZE

Number of recipients in each campaign task. Invalid or non-positive values
fall back to 200.

## CAMPAIGN_DELIVERY_MAX_ATTEMPTS

Maximum attempts for a definitively failed campaign delivery. Ambiguous Relay
outcomes are not automatically retried.

## CAMPAIGN_BATCH_INTERVAL_SECONDS

Requested spacing between scheduled campaign batches. The effective interval
is at least 300 seconds and at least the configured batch size multiplied by
the 1.5-second per-send delay. Lower values, including zero, are clamped to
that minimum. The initial fan-out and automatic recovery use the same spacing.

## CAMPAIGN_TEST_RECIPIENTS

Optional addresses suggested in the campaign test-send form. Test sends still
use the same pinned Relay template and rendering contract as campaign sends.

## RECORDING_AVAILABLE_SUBJECT_TEMPLATE

Subject prefilled into an operator-reviewed recording campaign draft.
`{event_title}` is replaced with the event title.

## RECORDING_AVAILABLE_BODY_TEMPLATE

Markdown body prefilled into an operator-reviewed recording campaign draft.
`{event_title}` and `{recording_url}` are replaced before the draft opens.

## Migration from legacy sender keys

Migration `integrations.0035_rename_relay_sender_settings` copies effective
non-empty `SES_*` sender values to their `RELAY_*` equivalents while preserving
existing neutral overrides. A general `SES_FROM_EMAIL` value fills each
missing From-address setting. The guarded email migration removes the legacy
rows only after confirming there are no unpublished local template overrides,
so a failed deploy can still roll back to code that reads the old keys.
