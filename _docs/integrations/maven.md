# Maven cohort auto-onboarding

Auto-onboards Maven cohort enrollees into the AI Shipping Labs community
(issue #960). When a member enrolls in a Maven cohort, a webhook to
`POST /api/webhooks/maven` resolves/creates their account, grants a long-lived
`main` tier override, invites them to Slack, and sends a course-framed welcome
email. A cohort removal sends a staff heads-up but never auto-revokes access.

The whole feature is off by default (`MAVEN_ENROLLMENT_ENABLED`). It is
payment-independent (instructors free-enroll people), idempotent under Maven
retries, and consent-respecting: enrolling never subscribes anyone to the
marketing newsletter. Account creation leaves the enrollee marketing-excluded,
and the welcome email offers a one-click verify-and-subscribe opt-in they have
to choose (issue #1593).

## Settings

All five settings live in the `Maven` group in Studio settings
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

Override lifetime in days (default `1825`, five years). An explicit valid
Studio or environment value remains authoritative. An existing longer Maven
or unrelated entitlement is never shortened.

### MAVEN_COURSE_SLACK_CHANNEL

Slack channel name shown in the `maven_welcome` email (optional, no default),
e.g. `#ai-engineering-buildcamp`. It names where the cohort actually talks, so
a new enrollee knows exactly where to go. When blank, the welcome sentence
reads cleanly without it and no channel is named — that is the shipping
default, and it is editable from Studio afterwards with no redeploy.

## Webhook setup

The endpoint is `POST https://aishippinglabs.com/api/webhooks/maven`. It is
CSRF-exempt and POST-only.

Authentication accepts the shared secret in EITHER (header-first is the
production recommendation, because query strings are more likely to appear in
proxy and browser logs):

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

## Accepted payload shapes

Maven publishes no payload contract, so intake is deliberately tolerant. All
of the following resolve to the same enrollee, the same `identity_hash`, and
therefore dedupe against each other.

| Field | Read from |
|---|---|
| Event type | `event`, then `type`, then `event_type` |
| Email | top-level `email`, then `user.email` / `student.email` / `member.email` |
| Course | `course` (string or `{name,title,slug,id}` object), `course_id` / `courseId` |
| Cohort | `cohort` (string or object), `cohort_id` / `cohortId` |
| First / last name | `first_name` / `last_name` on `user` / `student` / `member`, else top-level `first_name` / `last_name`, else a single `name` / `full_name` split on the first space |

Envelopes: when the top-level object carries no resolvable email but has a
dict under `data` or `payload` that does, that inner object is unwrapped
(exactly one level). Outer keys win where present and non-empty; everything
else comes from the inner object. So this:

```json
{"event": "user_cohort.enrolled",
 "data": {"email": "sam@example.com", "course": "Buildcamp", "cohort": "Q1"}}
```

is processed identically to the flat equivalent.

Names are PII. They are written only to the `User` row, and only into fields
that are still blank — a name the member set themselves is never overwritten.
They are never written into `MavenEnrollmentEvent.payload` and never into a
log line.

Rejected or unrecognised deliveries are logged with key NAMES only:

- `400 missing_email` logs a warning naming the error code and the sorted
  top-level key names of the payload — never any value.
- `400 invalid_json` logs a warning with the body byte length and content type
  only.
- An unrecognised event type logs at INFO with the normalized event-type
  string (not PII, and the most useful value when Maven's naming differs).

## Slack step statuses

The `slack` step reports what actually happened, and never claims a delivery
it could not make:

| Invite outcome | `slack_status` | Ledger note |
|---|---|---|
| User found in Slack and joined at least one community channel | `succeeded` | empty |
| User found in Slack but joined no community channel | `failed` | "Enrollee is in the Slack workspace but joined no community channel: `<channel>: <slack error>`", retried within the normal bound |
| User not in the Slack workspace | `skipped` | "Enrollee is not in the Slack workspace; the join link was delivered in the welcome email." |
| Slack API raised out of the invite call | `failed` | safe exception class, retried within the normal bound |

`succeeded` requires an actual channel join, never merely that a Slack user id
resolved. `add_to_channels` turns every per-channel Slack error into
`{"ok": False}`, so a bot that is not in the community channel, a wrong channel
id, or an exhausted rate-limit retry would otherwise be recorded as a delivery
that never happened. Those cases are `failed` with the channel errors visible on
`/studio/maven-events/<pk>/`, which is an operator-fixable Slack configuration
problem rather than a member problem.

A cold Maven enrollee gets exactly one email: `maven_welcome`, which carries
the Slack join link. The generic `community_invite` email is suppressed for
Maven (`invite(user, send_invite_email=False)`) so the enrollee is not hit
with two welcomes seconds apart.

`/community/slack` is `@login_required` and Main-gated. The welcome email no
longer walks the enrollee through a numbered 1/2/3 sequence (issue #1593): it
offers sign-in and set-a-password together, then the Slack join link. A signed-
out click on that link redirects through login with `next=/community/slack`
preserved, so it is one extra hop rather than a dead end.

## Behavior

`user_cohort.enrolled`:

- Resolves the account (primary login, then email alias) or creates a Free
  imported account (`signup_source=imported`, `email_verified=False`). A newly
  created account is marketing-excluded — `unsubscribed=True` and
  `email_preferences={"newsletter": False, "maven_emails": True}` — and stays
  that way until the enrollee opts in themselves. Existing accounts are
  resolved and returned untouched; no branch writes `unsubscribed` or the
  newsletter preference on an account this flow did not create.
- The newsletter is genuine opt-in (issue #1593). The welcome email's footer
  says "if you want to hear from us … verify your email", and the link behind
  that sentence is `newsletter_opt_in_url` →
  `/api/verify-and-subscribe?token=`, token action `verify_and_subscribe`. One
  click sets `email_verified=True`, `unsubscribed=False`, and
  `email_preferences["newsletter"] = True`, clears `verification_expires_at`,
  and lands on a page that states plainly what just happened and offers a
  no-login unsubscribe link next to it.
  - `maven_welcome` is in `EMAIL_TYPES_WITHOUT_VERIFY_FOOTER`
    (`email_app/services/email_service.py`). Without that exemption
    `EmailService` appends its generic "your email is not verified — to
    verify it, click here" footer for unverified recipients, pointing at
    `/api/verify-email`. That put two links for one verb in the same message,
    and the more directive of the two verifies WITHOUT subscribing and says
    nothing about the newsletter, so a reader who took it was silently not
    subscribed. The footer token also lives 7 days against the opt-in's 30, so
    the exemption is what keeps the consent-bearing link the only "verify"
    instruction in the email. Regression coverage asserts on the HTML handed
    to `_send_ses`, not on the template — the footer does not exist at
    template level, which is how this shipped unnoticed.
  - It is a SIBLING endpoint of `/api/verify-email`, not an intent flag on it.
    The token's action name is the consent record, so an ordinary
    `verify_email` token can never subscribe anyone however the endpoints are
    later refactored, and a forwarded or tampered query string cannot turn
    verification into consent.
  - The converse is deliberate too: signing in with OAuth verifies the address
    (`accounts/signals.py` trusts the provider) and does NOT subscribe them.
    Proving you control a mailbox is not asking to be marketed to, so
    verification and subscription are never coupled in that direction.
  - Maven-asserted addresses are never auto-verified on enrollment.
    Verification is an ownership signal proven by the member acting on a link
    we sent; Maven telling us an address exists is not us confirming it.
  - Because an enrollee who ignores the email is neither verified nor
    subscribed, they appear in no campaign audience at all — neither the
    default `verified_only` nor `everyone`, since `unsubscribed=True` excludes
    them unconditionally at `eligible_campaign_recipients`. That is the
    intended outcome, not a gap.
- Grants or extends a source-specific `main` entitlement. It never lowers,
  replaces, or shortens a stronger base/staff/billing grant; Maven access keeps
  its own expiry and becomes effective if a temporary stronger grant expires.
  The grant is recorded in
  `CommunityAuditLog` (`action="maven_enrollment_override"`).
- Invites them to Slack (idempotent — no-op if already in the workspace).
- Sends the course-framed `maven_welcome` email (transactional; from
  `welcome@`; carries a transparent notice + the newsletter opt-in + a scoped
  course-email opt-out + a reply-to-remove line). The two tokened links are
  different things and are labelled as such: `newsletter_opt_in_url` is the
  verify-and-subscribe opt-in described above, and `opt_out_url` is the
  Maven-scoped course-email opt-out (`/api/maven-email-opt-out?token=`, action
  `maven_email_opt_out`, sets `email_preferences["maven_emails"] = False`
  only, and touches neither `unsubscribed` nor the newsletter preference).
  Neither affects access, and Account can change either afterwards.
  - The email offers OAuth ("sign in with Google, GitHub, or Slack") alongside
    "set a password". That is safe on an imported account because
    `SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True`
    (`website/settings.py`), so a first OAuth login on a matching email links
    to the existing account instead of creating a duplicate.
  - Staff receives a hidden copy of the exact enrollee-facing welcome, BCC'd
    to `STAFF_SIGNUP_NOTIFY_EMAIL` — the same mechanism and the same setting
    as the Stripe paid-signup welcome (issue #1570). An unset value is a clean
    no-op, and a malformed value is validated away rather than allowed to make
    SES reject the enrollee's primary To. This is additive: the structured
    `maven_enrollment_notification` staff heads-up is unchanged. The
    `already_member` case skips the welcome entirely, so there is nothing to
    copy there and the heads-up covers it.
- After the entitlement succeeds, sends one independent internal staff
  enrollment heads-up to the configured staff mailbox and/or optional Slack
  channel. One successful destination completes the step; total delivery
  failure remains retryable, while no usable destination is terminally
  skipped. The notice links to the canonical Studio member and occurrence.
- Already-a-member enrollees (active access + already in Slack) get nothing
  member-visible — no welcome email and no re-invite — but staff still receives
  the one internal enrollment heads-up for the new occurrence. The override is
  silently refreshed/extended if it lapsed or would expire before the cohort.

`user_cohort.removed`:

- Makes NO change to the override, access, or Slack membership.
- Sends a staff heads-up (same recipients/style as the paid-signup
  notification) naming the user, user ID, a clickable Studio link, the cohort
  (and course), and suggested manual actions. A human decides.
- An email that resolves to no account is handled gracefully (lighter
  "unknown user" note, no error).

Lifecycle and idempotency: identity is a SHA-256 hash of normalized email plus
course and cohort identity. Provider IDs are preferred; normalized labels are
the fallback. Thus identically named cohorts in different courses do not
collide. One active `MavenEnrollmentEvent` occurrence is admitted under a
database constraint. Removal closes it without revocation; a later enrollment
creates a genuine new occurrence.

The entitlement, enrollment staff heads-up, Slack invite, welcome, and removal
notification each persist their own status, attempted/completed timestamps,
bounded attempt count (three automatic attempts), and a safe error class. A
five-minute scheduled recovery job retries pending, failed, or stale-running
work only while the selected step has fewer than three attempts. Successful and
skipped steps are never repeated.

An entitlement failure returns HTTP 500 while another automatic attempt is
available so Maven can redeliver safely. When any incomplete step reaches the
three-attempt ceiling, the webhook acknowledges the occurrence with HTTP 200
and the exact response `{"status": "manual_intervention_required"}`. The
acknowledgement prevents endless provider redelivery; it does not claim that
enrollment work completed. Later duplicate deliveries keep the same attempt and
side-effect counts.

Studio treats an exhausted failed step as needing attention immediately. A
failed step below the ceiling or a running step also needs attention when its
last attempt or completion has remained unchanged for at least 15 minutes.
Successful and skipped steps never qualify. The Studio dashboard shows a
critical `Maven enrollments need attention` item with the distinct occurrence
count. It links to `/studio/maven-events/?status=needs_attention`; the Maven
occurrence list also supports `status=failed` for all current failures.

To recover an occurrence, open its detail page, identify the affected step,
and fix the underlying provider or configuration cause first. Then use
`Retry safely` for that step. The staff-authenticated POST is CSRF-protected and
audited. A manual attempt may exceed the automatic three-attempt ceiling. When
an entitlement retry succeeds, the eligible notification, Slack, and welcome
steps resume once in dependency order; already successful or skipped work is
left untouched. Studio reports the persisted result as recovered, skipped,
failed again, or already running.

## Operator occurrence API

Remote support can inspect the same occurrence ledger and force one safe retry
with a staff-owned operator token. A signed-in browser session alone does not
authenticate these JSON routes. Send the exact header
`Authorization: Token <key>`; missing, invalid, inactive-owner, and non-staff
credentials return JSON `401` responses.

The three slashless routes are:

| Method | Route | Result |
|---|---|---|
| `GET` | `/api/integrations/maven/occurrences` | Filtered occurrence summaries |
| `GET` | `/api/integrations/maven/occurrences/<occurrence_id>` | One occurrence and all five current steps |
| `POST` | `/api/integrations/maven/occurrences/<occurrence_id>/steps/<step>/retry` | One forced safe retry and the refreshed occurrence |

List filters combine with AND. `email` is a case-insensitive exact lookup that
matches the short-lived occurrence email and, when the canonical primary/alias
resolver finds an account, every occurrence linked to that user. `course` and
`cohort` match a label substring or their exact provider key. `lifecycle`
accepts `active`, `removed`, or `legacy`; `status` accepts `all`, `failed`, or
`needs_attention`; and `failed_step` accepts `override`, `notification`,
`slack`, `welcome`, or `removal`.

Pages default to `limit=50&offset=0`. Positive limits above 200 are clamped to
200, and `total_count` reports all filtered rows while `count` reports rows in
the current page. Use increasing offsets to reach older occurrences:

```bash
curl -sS \
  -H "Authorization: Token $API_TOKEN" \
  "https://aishippinglabs.com/api/integrations/maven/occurrences?email=sam%40example.com&status=failed&limit=50&offset=0"
```

Diagnose an occurrence before retrying it:

```bash
curl -sS \
  -H "Authorization: Token $API_TOKEN" \
  "https://aishippinglabs.com/api/integrations/maven/occurrences/123"
```

The detail response always includes `override`, `notification`, `slack`,
`welcome`, and `removal` in dependency order. Each row reports status,
attempts, timestamps, whether it needs attention, and a safe error class or
controlled reason. Unsafe legacy errors appear as `last_error: "redacted"`
with `error_redacted: true`.

After fixing the provider or configuration cause, retry only the affected
step. The URL supplies every option, so no request body is needed:

```bash
curl -sS -X POST \
  -H "Authorization: Token $API_TOKEN" \
  "https://aishippinglabs.com/api/integrations/maven/occurrences/123/steps/welcome/retry"
```

An attempted provider outcome returns `200` with `retry.outcome` set to the
persisted `succeeded`, `failed`, or `skipped` state. A caught provider failure
therefore remains a truthful `200` with `outcome=failed`. A fresh running lease
returns `409 maven_step_in_progress`; a step already persisted as `succeeded`
or `skipped` returns `409 maven_step_not_retryable`. A successful forced
`override` retry resumes only currently eligible downstream enrollment steps
once in their normal order.

The API never returns webhook payloads, dedupe or identity hashes, names,
Slack IDs, provider bodies, audit details, or token values. After the 30-day
retention task sets `payload_redacted_at`, `occurrence_email` remains empty and
is never reconstructed from the linked account. The separately returned
`user.email` is the current canonical account email retained for staff account
support. The full contract is also available under the Maven Integrations
section at `/api/docs`.

## Data minimization and retention

The ledger never stores the webhook secret and stores only operational event,
course, cohort, and provider-ID metadata from a payload. Dedupe keys are
hashed. The email field exists for short-lived operations and legacy raw
payloads may exist from the first implementation. The default scheduler runs
the redaction task daily at 03:20 UTC. Operators can also run it manually:

```bash
uv run python manage.py redact_maven_enrollment_pii
```

It redacts email and payload fields once an occurrence is older than 30 days.

## Rollback and incident operations

Turn `MAVEN_ENROLLMENT_ENABLED` off first. The endpoint then acknowledges with
`disabled` and performs no work. Do not revoke Maven grants or Slack access as
part of rollback. Inspect failed steps in Studio, fix the provider/configuration
cause, and retry only that step. Logs and ledger errors contain occurrence IDs
and exception classes, never payloads, email addresses, tokens, or secrets.

## Testing without a live cohort: `replay_maven_event`

The `replay_maven_event` management command feeds a sample payload through the
SAME handler the webhook uses, so it exercises the real flow.

`--course` and `--cohort` are required whenever `--payload` is not supplied.
They have no defaults: both land in the enrollee's subject line, so a
forgotten flag raises a `CommandError` naming the missing flag rather than
mailing a real person about a placeholder course. A real run prints the
resolved recipient, course, and cohort before the handler runs.

Dry-run first (no writes; reports intended actions):

```bash
uv run python manage.py replay_maven_event \
    --event user_cohort.enrolled --email me@example.com \
    --course "LLM Zoomcamp" --cohort "Spring 2026" --dry-run
```

Then for real (idempotent — a second run reports `already_processed`):

```bash
uv run python manage.py replay_maven_event \
    --event user_cohort.enrolled --email me@example.com \
    --course "LLM Zoomcamp" --cohort "Spring 2026"
```

Replay a removal:

```bash
uv run python manage.py replay_maven_event \
    --event user_cohort.removed --email me@example.com \
    --course "LLM Zoomcamp" --cohort "Spring 2026"
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

## Backfilling already-enrolled members

People who enrolled before the webhook was registered are onboarded by
replaying their enrollment through the same authenticated production API the
live webhook uses. No separate endpoint and no shell access are required.

One request per person:

```bash
curl -sS -X POST https://aishippinglabs.com/api/webhooks/maven \
  -H "X-Maven-Secret: $MAVEN_WEBHOOK_SHARED_SECRET" \
  -H "Content-Type: application/json" \
  -d '{
        "event": "user_cohort.enrolled",
        "email": "sam@example.com",
        "first_name": "Sam",
        "last_name": "Rivera",
        "course": "AI Engineering Buildcamp: From RAG to Agents",
        "cohort": "Cohort 1"
      }'
```

Name mapping from a Maven roster CSV:

| CSV column | Payload field |
|---|---|
| `preferred_name` when present, else the first token of `full_name` | `first_name` |
| the remainder of `full_name` after the first token | `last_name` |

The call is idempotent: a repeat returns `already_processed` and never
re-onboards or re-emails. A `200` with `onboarded` means the occurrence was
created and its steps ran.

Verify each person with the existing authenticated API:

| Check | Endpoint |
|---|---|
| Account exists, tier override applied | `GET /api/users/{email}` |
| The `maven_welcome` email was sent | `GET /api/users/{email}/email-log` |
| SES delivered it (or it bounced) | `GET /api/users/{email}/ses-events` |
| Slack workspace membership | `GET /api/users/{email}/slack-membership/check` |

Step-level detail (including a `skipped` slack step and its reason) is on
`/studio/maven-events/<pk>/`.
