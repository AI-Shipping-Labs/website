# Maven cohort auto-onboarding

Auto-onboards Maven cohort enrollees into the AI Shipping Labs community
(issue #960). When a member enrolls in a Maven cohort, a webhook to
`POST /api/webhooks/maven` resolves/creates their account, grants a long-lived
`main` tier override, invites them to Slack, and sends a course-framed welcome
email. A cohort removal sends a staff heads-up but never auto-revokes access.

The whole feature is off by default (`MAVEN_ENROLLMENT_ENABLED`). It is
payment-independent (instructors free-enroll people), idempotent under Maven
retries, and consent-respecting (no marketing-newsletter opt-in).

## Settings

All four settings live in the `Maven` group in Studio settings
(`/studio/settings/`). Read via `get_config` / `is_enabled`, never raw env.

### MAVEN_ENROLLMENT_ENABLED

Master toggle (boolean, default `false`). When off, the webhook returns
`200 {"status":"disabled"}` and does no account/override/invite/email work.

### MAVEN_WEBHOOK_SHARED_SECRET

Shared secret (secret string) that authenticates inbound webhook calls. Maven
exposes no signing secret, so this is the verification path. Generate a long
random token (e.g. `openssl rand -hex 32`) and paste it here. When blank, the
endpoint rejects every request with `403`, even when the feature is enabled.

### MAVEN_OVERRIDE_TIER_SLUG

Tier slug granted as the override (string, default `main`). Validated against
`Tier`; a free / level-0 slug is rejected and falls back to `main` (logged).

### MAVEN_OVERRIDE_DURATION_DAYS

Override lifetime in days (default `3650`, ~10 years, matching the manual
contact-import practice). An existing longer override is never shortened.

## Webhook setup

The endpoint is `POST https://aishippinglabs.com/api/webhooks/maven`. It is
CSRF-exempt and POST-only.

Authentication accepts the shared secret in EITHER:

- the query string: `…/api/webhooks/maven?secret=<MAVEN_WEBHOOK_SHARED_SECRET>`
  (paste this full URL into Maven/Zapier directly), OR
- an `X-Maven-Secret: <secret>` request header (preferred when Zapier is the
  intermediary).

Steps:

1. Generate a secret and paste it into Studio settings
   (`MAVEN_WEBHOOK_SHARED_SECRET`). Turn on `MAVEN_ENROLLMENT_ENABLED`.
2. In Maven (or a Zapier "Webhooks by Zapier → POST" step) register the webhook
   URL with the secret. Maven fires `user_cohort.enrolled` on the instructor
   "Enroll for Free" action (no Stripe needed) and `user_cohort.removed` on
   cohort removal.
3. Maven's guidance: after adding a webhook, wait ~2 minutes, then test by
   enrolling as a student would.

The endpoint acts only on `user_cohort.enrolled` (onboarding) and
`user_cohort.removed` (staff heads-up). Any other type — including
`payment.success` — is acknowledged with `200 {"status":"ignored"}` and does
nothing. Keying onboarding off `user_cohort.enrolled` only is the dedupe
against the Stripe-side `payment.success` for paid enrollments.

## Behavior

`user_cohort.enrolled`:

- Resolves the account (primary login, then email alias) or creates a Free
  imported account (`signup_source=imported`, `email_verified=False`). Imported
  accounts are NOT added to the marketing newsletter (the campaign audience
  requires `email_verified=True`).
- Grants or extends a single active `main` tier override (never stacks; never
  shortens a longer existing override). The grant is recorded in
  `CommunityAuditLog` (`action="maven_enrollment_override"`).
- Invites them to Slack (idempotent — no-op if already in the workspace).
- Sends the course-framed `maven_welcome` email (transactional; from
  `welcome@`; carries a transparent notice + an opt-out link + reply-to-remove
  line).
- Already-a-member enrollees (active access + already in Slack) get nothing
  visible — no welcome email, no staff note, no re-invite — but the override is
  still silently refreshed/extended if it lapsed or would expire before the
  cohort.

`user_cohort.removed`:

- Makes NO change to the override, access, or Slack membership.
- Sends a staff heads-up (same recipients/style as the paid-signup
  notification) naming the user, user ID, a clickable Studio link, the cohort
  (and course), and suggested manual actions. A human decides.
- An email that resolves to no account is handled gracefully (lighter
  "unknown user" note, no error).

Idempotency: a `MavenEnrollmentEvent` dedupe row (normalized email + cohort +
event type) is written only after terminal success. Repeat deliveries return
`200 {"status":"already_processed"}`; a transient failure returns `500` (no
row) so the sender retries.

## Testing without a live cohort: `replay_maven_event`

The `replay_maven_event` management command feeds a sample payload through the
SAME handler the webhook uses, so it exercises the real flow.

Dry-run first (no writes; reports intended actions):

```bash
uv run python manage.py replay_maven_event \
    --event user_cohort.enrolled --email me@example.com --dry-run
```

Then for real (idempotent — a second run reports `already_processed`):

```bash
uv run python manage.py replay_maven_event \
    --event user_cohort.enrolled --email me@example.com
```

Replay a removal:

```bash
uv run python manage.py replay_maven_event \
    --event user_cohort.removed --email me@example.com --cohort "Spring 2026"
```

Supply a full sample body (file path or inline JSON):

```bash
uv run python manage.py replay_maven_event --payload ./sample.json
uv run python manage.py replay_maven_event \
    --payload '{"event": "user_cohort.enrolled", "email": "me@example.com"}'
```

## Testing live

The owner can also test end to end by free-enrolling his own account plus a few
test accounts into a real Maven test cohort and watching the flow run: account
created, override granted, Slack invite, welcome email. Maven guidance: after
adding the webhook, wait ~2 minutes, then enroll as a student would.
