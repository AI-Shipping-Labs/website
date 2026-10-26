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

Seconds between scheduled campaign batches. Zero schedules all batches
without staggering.

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
