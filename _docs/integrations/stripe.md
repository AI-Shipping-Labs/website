# Stripe integration setup

This page documents every Stripe-related setting registered in
`integrations/settings_registry.py` (the `stripe` group). Each section
follows the same template — Purpose, Without it, Where to find it,
Prereqs, Rotation, Test vs live — so an operator can answer "do I need
to set this right now, or can I defer it?" without leaving the page.

Direct deep-link URLs are intentionally written in code blocks so they
do not render as clickable links. Copy them into the browser.

## STRIPE_SECRET_KEY

Purpose: Server-side Stripe API key used by every outbound call the
platform makes — checkout-session creation, customer lookup, subscription
sync, Payment Link generation, and the Customer Portal redirect. The
`payments` and `accounts` apps all read it through
`integrations.config.get_config('STRIPE_SECRET_KEY')`.

Without it: Checkout fails the moment a tier-upgrade CTA or course
purchase button is clicked — the platform cannot create a Stripe
Checkout Session, so the user lands on a generic error page instead of
the Stripe-hosted payment form. Studio's "Sync from Stripe" action on
the user profile (`/studio/users/<id>/sync-from-stripe/`) also fails,
and reconciliation jobs that fetch subscription state from Stripe stop
making progress. Existing paid users keep their tier (state is in the
DB), but no new purchases are possible.

Where to find it:

- Direct link:

  ```
  https://dashboard.stripe.com/apikeys
  ```

  Test mode:

  ```
  https://dashboard.stripe.com/test/apikeys
  ```

- Click "Reveal" on the "Secret key" row and copy the `sk_live_...`
  (live) or `sk_test_...` (test) value.

Prereqs: A Stripe account. No additional Stripe-side configuration is
required to use the secret key for charges — Connect is not used.

Rotation: Safe to rotate, but with a brief window of new-checkout
failure.

1. In the Stripe Dashboard, click "Roll key" on the secret key row.
   Stripe shows the new `sk_..._...` once. Copy it.
2. Update this setting via Studio (Integration settings > Stripe >
   `STRIPE_SECRET_KEY`) or via `POST /api/integrations/settings`.
3. Between the moment Stripe issues the new key and the moment you save
   it here, outbound Stripe API calls fail with `invalid_api_key`.
   In-progress browser sessions on the Stripe Checkout page already
   created with the old key continue to work — Stripe does not retract
   sessions when the key rotates.

Test vs live: The key prefix encodes the mode.

- `sk_test_...` — test mode. Pairs with a `STRIPE_WEBHOOK_SECRET` from a
  test-mode webhook endpoint and a `STRIPE_CUSTOMER_PORTAL_URL` from the
  test-mode Customer Portal.
- `sk_live_...` — live mode. Pairs with a `STRIPE_WEBHOOK_SECRET` from a
  live-mode webhook endpoint and the live Customer Portal URL.

Mixing modes (e.g. live `STRIPE_SECRET_KEY` with a test-mode webhook
secret) silently drops all incoming webhooks because the signing secret
won't match the live-mode signatures.

The membership fulfillment handler also compares the Checkout Session's
`livemode` boolean with this key prefix. A test Session delivered to a live
deployment (or the reverse) is quarantined as `stripe_mode_mismatch`; it never
grants access. Sessions must also report `status=complete` before identity or
Price validation begins. A complete `payment_status=unpaid` Session is safely
reserved as `awaiting_payment`; access is granted only after Stripe sends
`checkout.session.async_payment_succeeded` with the same Session now reporting
`payment_status=paid`.

## STRIPE_WEBHOOK_SECRET

Purpose: Stripe signs every webhook delivery with this secret. The
platform's webhook handler at `payments/views/webhooks.py:67` rejects
any event whose signature doesn't verify (via
`payments/services/webhooks.verify_webhook_signature`), so without a
correct value the platform cannot react to payments, subscription
changes, or customer edits.

Without it: `checkout.session.completed` events get rejected — paid
users complete checkout in Stripe but the platform never advances their
tier, never records the `stripe_customer_id`, never fires the community
invite. `customer.subscription.updated/deleted` and
`invoice.payment_failed` are also dropped, so tier expiry and lapse
detection silently stop. Existing paid users keep their tier (state is
in the DB), but no new state transitions happen.

Where to find it:

- Direct link (live):

  ```
  https://dashboard.stripe.com/webhooks
  ```

  Test mode:

  ```
  https://dashboard.stripe.com/test/webhooks
  ```

- Click into your endpoint, then "Signing secret", then "Click to
  reveal", and copy the `whsec_...` value.

Prereqs: You must create a webhook endpoint first.

- Direct link to create:

  ```
  https://dashboard.stripe.com/webhooks/create
  ```

- Endpoint URL on this platform: `https://<host>/api/webhooks/payments`
  (e.g. `https://aishippinglabs.com/api/webhooks/payments` in
  production).
- Event destination scope: "Your account" — Stripe Connect is not used
  here.
- Payload style: Snapshot (the classic v1 envelope). Thin events are not
  supported by the handler.
- API version: leave as the Stripe default at creation time. The handler
  reads `type`, `id`, and `data` only.
  - Subscribe to exactly these 11 events:
  - `checkout.session.completed`
  - `checkout.session.async_payment_succeeded`
  - `checkout.session.async_payment_failed`
  - `customer.subscription.updated`
  - `customer.subscription.deleted`
  - `invoice.payment_failed`
  - `invoice.paid`
  - `customer.updated`
  - `charge.refunded`
  - `charge.dispute.created`
  - `charge.dispute.closed`

  Other events (e.g. `invoice.payment_succeeded`) are
  not handled and add log/audit noise without enabling any platform
  behavior. Don't subscribe speculatively.

Rotation: Safe to rotate.

1. In the Stripe Dashboard, click "Roll secret" on the endpoint.
2. Stripe shows a new `whsec_...`. Copy it.
3. Update this setting via Studio (Integration settings > Stripe >
   `STRIPE_WEBHOOK_SECRET`) or via `POST /api/integrations/settings`.
4. During the window between Stripe showing the new secret and you
   saving it here, webhook signature checks fail and Stripe will retry.
   Total outage window is typically 10-30 seconds if you move quickly.

Test vs live: Test-mode and live-mode webhook endpoints are entirely
separate — each has its own signing secret. If you change Stripe modes,
you also change the signing secret. The platform's `STRIPE_SECRET_KEY`
mode (`sk_test_` vs `sk_live_`) and `STRIPE_WEBHOOK_SECRET`'s associated
endpoint must match — otherwise events are dropped silently.

## STRIPE_PAYMENT_LINKS

Purpose: Complete JSON matrix of the public Stripe Payment Links used by
the anonymous homepage and `/membership`. A Studio or API override takes
effect on the next request without a deploy.

The value must be valid JSON with exactly these tiers and billing periods;
replace every example URL with its matching live or test Payment Link:

```json
{
  "basic": {"monthly": "https://buy.stripe.com/...", "annual": "https://buy.stripe.com/..."},
  "main": {"monthly": "https://buy.stripe.com/...", "annual": "https://buy.stripe.com/..."},
  "premium": {"monthly": "https://buy.stripe.com/...", "annual": "https://buy.stripe.com/..."}
}
```

Without it: The links bundled in Django settings remain active. Invalid,
incomplete, or extra JSON fields are rejected as a whole and also fall back
to that complete matrix, so checkout never mixes old and new links.

Where to find it: Stripe Dashboard > More > Payment links. Copy the link
for each tier/period combination. Test and live modes have separate links;
do not mix them in one matrix.

Rotation: Create or activate all six replacement links first, save the full
matrix in one Studio/API update, verify both public surfaces, then deactivate
the old links. Clearing the override restores the Django-settings fallback.

## STRIPE_CUSTOMER_PORTAL_URL

Purpose: Public URL of the Stripe-hosted Customer Portal where members
manage their own subscription — change plan, update card, download
invoices, or cancel. The account page renders a "Manage subscription"
button that links to this URL with the user's Stripe customer ID
appended, so the operator does not have to build per-user portal
sessions server-side.

Without it: The "Manage subscription" CTA on the account page is hidden.
Paid members keep their subscription (Stripe still bills them) but they
cannot self-serve a card update or a plan change — every such request
becomes an operator support ticket.

Where to find it:

- Direct link (live):

  ```
  https://dashboard.stripe.com/settings/billing/portal
  ```

  Test mode:

  ```
  https://dashboard.stripe.com/test/settings/billing/portal
  ```

- Click "Activate" if the portal has never been configured, then copy
  the "Login link" shown on the configuration page. It looks like
  `https://billing.stripe.com/p/login/<id>`.

Prereqs: The Customer Portal must be configured (functionality enabled,
allowed plans selected, branding set) before Stripe exposes a Login
link. Configuration lives at the same dashboard URL.

Rotation: Stripe regenerates the URL when the portal is deactivated and
re-activated. It is otherwise stable. Routine rotation is not necessary.
If you do rotate it, paste the new URL into Studio — there is no
intermediate signing step.

Payment-grace email validates the configured value as the stable
`https://billing.stripe.com/p/login/<id>` form with no credentials, query, or
fragment. A `/p/session/` URL is a bearer session and is deliberately rejected.

Test vs live: Test-mode and live-mode portals have separate URLs and
separate configurations. Match the mode to your `STRIPE_SECRET_KEY`
otherwise the link will load a portal that has no record of the
customer’s subscription.

## STRIPE_MONTHLY_PAYMENT_GRACE_MODE

Purpose: controls the staged monthly failed-payment policy. Allowed values are
`observe` and `enforce`; invalid values fail safe to `observe`. The default is
`observe`.

In observe mode, an exact automatically collected, unpaid one-month membership
invoice on a uniquely owned `past_due|unpaid` subscription starts a durable
grace and sends only the initial member/team messages. No warning, Free email,
or access change occurs. In enforce mode, the reminder and expiry policy runs.
The first enforcement sweep stamps each previously observed grace once and
gives it at least a fresh 168-hour enforcement window; toggling modes cannot
reset that stamp.

Rollout: deploy in `observe`, review the Payment grace filter in Studio and the
read-only API cohort, then set `enforce`. Roll back by returning to `observe`;
existing grace and delivery audit rows remain available and initial failure
diagnostics continue. Do not delete grace rows to roll back.

## PAYMENT_FAILURE_TEAM_EMAIL

Purpose: single validated recipient for the initial `[Payments] Member payment
failed` diagnostic. Default: `team@aishippinglabs.com`. Blank or invalid values
do not redirect mail elsewhere: the team delivery is omitted and a staff-only
configuration error is recorded while the member path continues.

## Monthly payment grace state contract

The policy is exactly 168 hours from the earliest authoritative Stripe failure
timestamp: webhook event `created`, or invoice `created` when the daily 04:45
UTC discovery task finds a missed webhook after the 04:30 reconciliation. A
retry for the same invoice never resets the timestamp. The 15-minute sweep
sends one warning at T-48 hours and re-fetches the exact subscription and
invoice at expiry before changing state.

Only `interval=month`, `interval_count=1`, `charge_automatically`, one mapped
paid membership item, unpaid invoice, and `past_due|unpaid` qualify. Annual,
multi-month, weekly/daily, one-time, manual-collection, trial-only,
unknown/mixed/multiple price, missing/ambiguous history, and
`incomplete|incomplete_expired|paused` remain review-only. A scheduled
cancellation remains paid through `current_period_end`/`cancel_at` and never
creates grace by itself.

`invoice.paid` or a matching active/trialing subscription update recovers grace
atomically and suppresses unsent warning/expiry work. Recovery matches the
verified event's test/live mode and stores its stable event ID and `created`
timestamp; an opposite-mode or ambiguous match changes no grace/member state
and remains visible in webhook diagnostics. At expiry, uncertainty,
ownership ambiguity, manual tier/subscription changes, or Stripe errors become
`review` without access mutation. A successful expiry changes only the base
tier to Free, retains safe Stripe recovery IDs and learning progress, writes
the distinct `stripe:lapsed` state, and removes community access only if the
effective tier falls below Main. Active `TierOverride` rows are never changed;
Studio, API, audit, and email distinguish a Free base tier from continuing
courtesy effective access.

Approved subjects are: `Payment failed — please retry your AI Shipping Labs
payment`, `Payment needed to keep your paid membership`, and `Your AI Shipping
Labs account is now Free`; the operator subject is `[Payments] Member payment
failed`. The initial member message deliberately contains no grace, deadline,
access-loss, downgrade, or Free-account language. All member messages use only
the stable validated `STRIPE_CUSTOMER_PORTAL_URL`; a missing/invalid URL is
omitted with reply-for-help copy and a staff-visible error. Delivery claims
expire after 15 minutes only before transport starts. Immediately before the
irreversible SES call, a transactionally fenced `transport_started_at` marker
prevents a reclaimed/late worker from also sending. A caught transport failure
clears that marker and uses bounded exponential backoff. If a worker disappears
after transport begins and the result is unknowable, automatic resend is
suppressed and the delivery becomes a staff-visible failed/unknown outcome;
operators investigate instead of risking duplicate mail. The durable
grace/kind/recipient plus `EmailLog.dedupe_key` also prevents replay spam.

Incident recovery: repair ownership/configuration in Stripe or Studio, keep
the policy in observe if broad uncertainty exists, then let reconciliation and
the sweep re-evaluate. Use Stripe as payment truth and `TierOverride` for
explicit courtesy access; there is intentionally no force-expire, extend, or
mark-paid endpoint.

## STRIPE_DASHBOARD_ACCOUNT_ID

Purpose: Stripe account ID (the `acct_...` prefix) used to build
dashboard deep-links so Studio operators can click straight from a user
profile into that user's Stripe customer page. Used only for outbound
link construction in Studio — not for any API call.

Without it: The Stripe icon next to a user in Studio still renders, but
is not clickable. Everything else (checkout, webhooks, subscription
sync) continues to work because they do not depend on this value.

Where to find it:

- Direct link to your account home (the URL bar shows the account ID):

  ```
  https://dashboard.stripe.com/settings/account
  ```

- The account ID appears in any Stripe dashboard URL after the host,
  e.g. `https://dashboard.stripe.com/acct_1T1mfGB7mZrgL7H5/dashboard`.
  Copy the `acct_...` segment.

Prereqs: None beyond having a Stripe account.

Rotation: The account ID is permanent for the lifetime of the Stripe
account. There is no rotation. If your organisation migrates to a new
Stripe account, update this value once.

Test vs live: n/a. The account ID is the same in test and live mode for
a given Stripe account — Stripe's `/test/` URL prefix only swaps which
data set the dashboard shows.

## STRIPE_WEBHOOK_EXPECTED_URL

Purpose: The exact webhook callback URL the endpoint verifier (issue #1314)
expects Stripe to target. The default is the production URL
`https://aishippinglabs.com/api/webhooks/payments`. Override it on
non-production environments so the verifier checks that environment's own host
instead of production.

Where it is used: Studio > Payments > `Stripe webhooks`
(`/studio/payments/stripe-webhooks/`) and the staff-token API
`POST /api/payments/stripe-webhooks/verify`. The verifier reads Stripe webhook
endpoints in the same mode as `STRIPE_SECRET_KEY` and confirms exactly one
enabled snapshot endpoint targets this URL with the eleven required events.

Test vs live: n/a to the value itself; the mode comes from the configured key.
The verifier reports `key_mode` (test/live) separately from the URL check.

## Cancellation webhook verification and replay runbook

This runbook is the safe procedure for confirming and repairing Stripe
cancellation callbacks. Cohort-wide reconciliation of cancellations whose event
ID is unknown is issue #1308, not this procedure.

1. In the live-mode Stripe Dashboard, create or open the endpoint for
   `https://aishippinglabs.com/api/webhooks/payments`. Choose "Your account",
   Snapshot (classic) payloads, and enable exactly the eleven documented events
   (`checkout.session.completed`, `checkout.session.async_payment_succeeded`,
   `checkout.session.async_payment_failed`, `customer.subscription.updated`,
   `customer.subscription.deleted`, `invoice.payment_failed`, `invoice.paid`,
   `customer.updated`, `charge.refunded`, `charge.dispute.created`,
   `charge.dispute.closed`). Copy that endpoint's live `whsec_...` into Studio's
   `STRIPE_WEBHOOK_SECRET`, and confirm the configured `sk_live_...` belongs to
   the same account/mode.
2. Open Studio > Payments > `Stripe webhooks` and click
   `Verify Stripe configuration`. Resolve any URL, status, mode, missing-event,
   duplicate-endpoint, or API-permission finding. Treat `Configured` and
   `Verified by delivery` as separate signing-secret signals — the website can
   never claim the signing secret matches from endpoint metadata alone; only a
   real signature-verified delivery proves it.
3. Use Stripe's "Send test webhook" only to prove transport and signature
   capture. A synthetic cancellation event with a dummy subscription id must not
   change any member; it appears as an `unmatched_user` attempt, which is
   expected.
4. Test real membership state transitions end-to-end only in Stripe test mode,
   against a non-production/test member and a test-mode endpoint. Never cancel a
   live member just to test the callback.
5. For an existing live event, inspect its event ID first (Studio `Inspect
   event`, read-only). Use Stripe Dashboard "Resend" only when there is no
   processed terminal local event; a processed event is idempotently ignored.
6. For missed or `failed_permanent` cancellation evidence, fix the
   identity/configuration cause first, then call
   `POST /api/payments/stripe-webhooks/replay` in dry-run mode (the default) and
   verify the exact member and proposed transition. Only when the preview is
   unambiguous, repeat with `dry_run=false` and
   `confirm=replay_cancellation_event`. Record the operator decision. Repeating
   the confirmed request is idempotent. Reconciliation issue #1308 remains the
   safety net for events whose Stripe event ID is unknown.
7. Rollback: disable only the broken endpoint or restore the prior signing
   secret/configuration; never disable Stripe billing. Repair member tiers only
   through confirmed replay or reconciliation — never by hand-editing a raw
   webhook or attempt row.

## Delayed Checkout settlement runbook

Stripe payment methods with delayed notification complete Checkout before funds
settle. The signed `checkout.session.completed` delivery records a validated
`awaiting_payment` fulfillment and grants no membership or course access. A
later signed `checkout.session.async_payment_succeeded` revalidates the same
Session, Price, account binding, customer/subscription ownership, mode, and paid
state before using the normal exactly-once fulfillment transaction. A signed
`checkout.session.async_payment_failed` records `payment_failed`, changes no
access, and sends at most one transactional retry message to a safely resolved
local account. Webhook billing email alone never authorizes that failure email.
The configured restricted Stripe key must permit Checkout Session reads so an
unexpanded one-time course Session can be retrieved for exact Price validation.

Safe test procedure:

1. Use Stripe test mode, a test member, a test Payment Link/Checkout Session,
   and a test endpoint subscribed to all eleven events. Never enable or disable a
   live payment method as part of application testing.
2. Deliver a signed complete/unpaid fixture and confirm the fulfillment is
   `awaiting_payment`, with no tier/course access, conversion, welcome, or paid
   signup notification.
3. Deliver the signed paid async-success fixture and confirm the same Session is
   fulfilled exactly once. Separately test async failure and confirm one retry
   email and no access change. Mock Stripe and SES in automated tests.
4. For an incident, filter Studio's `All handled events` history or
   `GET /api/payments/stripe-webhooks/deliveries?event_type=...` by either async
   event. Fix the configuration/identity cause, then use Stripe Dashboard
   `Resend`; there is no website replay or manual-entitlement endpoint for
   async Checkout events. Terminal event and Session-level idempotency make a
   resend safe.
5. A human with Stripe Dashboard access must separately record which delayed
   methods are enabled in live mode and confirm the production endpoint has both
   async events enabled. This application runbook is read-only with respect to
   live Stripe configuration.
### Refund and dispute review runbook

`charge.refunded`, `charge.dispute.created`, and `charge.dispute.closed` are
review signals, not entitlement authorities. A signature-valid callback is
classified as a full/partial refund, opened dispute, or closed won/lost
dispute; it terminates as `review_required`, records only bounded safe Stripe
event/customer/subscription/charge/invoice/dispute identifiers, and sends one
`mail_admins` alert for that event ID. Exact redelivery records
`already_processed` and sends no duplicate alert. No raw payload, signature,
secret, receipt URL, payment-method/card data, or member email is stored on the
delivery-attempt row.

Partial refunds may be adjustments, refunds do not cancel active
subscriptions, and disputes are provisional until closed. Therefore these
callbacks never change base/effective tier, `TierOverride`, pending tier,
billing dates, tags, community access, `CourseAccess`, progress, or monthly
payment grace. No automatic member email is sent. After reviewing the Stripe
charge/dispute and member context, cancel the membership subscription in
Stripe if access should end; the verified `customer.subscription.deleted`
callback remains the sole automated subscription-end transition.

Inspect evidence at `/studio/payments/stripe-webhooks/` or through the
staff-token status/deliveries API. The confirmed replay API remains
cancellation-only: resend a refund/dispute from Stripe after correcting a
transient lookup/configuration problem. A transport, authentication, or
configuration failure returns HTTP 500, records `failed_transient`, writes no
terminal `WebhookEvent`, and remains retryable.

## AUTHENTICATED_CHECKOUT_BINDING_ENABLED

Purpose: Emergency kill switch for authenticated membership checkout.
The default is `true`. Set it to `false` in Studio to stop issuing new
opaque checkout bindings while leaving anonymous Payment Links, webhook
quarantine, and operator recovery available.

Rollback: Disable this setting before rolling back application code. Existing
opaque references then expire naturally and remain visible in the payment
mismatch audit instead of falling back to email or numeric identity.

## CHECKOUT_BINDING_TTL_MINUTES

Purpose: Lifetime of a server-issued authenticated checkout binding. The
default is 120 minutes; runtime values are clamped between 5 and 1440 minutes.
Shorter values reduce replay opportunity while longer values give members more
time to finish Stripe-hosted checkout.

## LEGACY_NUMERIC_CHECKOUT_REFERENCE_ENABLED

Purpose: Temporary migration switch for old Payment Links that still send a
numeric local user ID. While enabled, the webhook accepts one only when the
Stripe billing email already resolves to that exact canonical account. New
pricing links never emit numeric references. This switch cannot extend access
past `LEGACY_NUMERIC_CHECKOUT_REFERENCE_CUTOFF`; numeric references are then
quarantined for operator review even if the switch was accidentally left on.

## LEGACY_NUMERIC_CHECKOUT_REFERENCE_CUTOFF

Purpose: Enforced UTC end of the numeric-reference migration window. The
default is `2026-08-01T00:00:00Z`. At or after the cutoff, or when the value is
missing/malformed, every numeric reference is quarantined. Set the enabled
switch to `false` once old Payment Link sessions have drained; do not move the
cutoff without a separately reviewed migration decision.

## Legacy Stripe alias recovery

Older checkout behavior could create `stripe_relay` authentication aliases
from a billing email. Audit these rows before removing or merging anything:

```bash
uv run python manage.py audit_stripe_checkout_aliases
```

The command is read-only. For every alias it prints the canonical member,
linked OAuth provider/UID and provider email, Checkout Session fulfillment
status, payment-mismatch rows, and the matching audit-log context.

Recovery procedure:

1. Confirm the canonical account and inspect every linked OAuth identity. An
   alias that matches an OAuth provider email may still be a valid login path.
2. Open each reported Checkout Session and mismatch in Stripe/Studio. Confirm
   the customer, subscription, paid Price, and final fulfillment outcome.
3. If two accounts genuinely belong to one person, use the existing account
   merge preview first; do not reassign subscriptions or aliases manually.
4. Remove an alias only after the member has another verified login identity
   and the Checkout/audit evidence shows the alias was system-created billing
   data rather than an intended authentication address.
5. Record the operator decision in the mismatch resolution note. `resolved`
   means the review is closed, not that a repair occurred; the fulfillment
   record remains the authority for whether access was granted or quarantined.
   `ignored` means reviewed and intentionally retained.

Rollback: the audit command never writes. If the evidence is incomplete, leave
the alias and mismatch unchanged, keep fulfillment quarantined, and escalate
with the printed Session, OAuth, and audit identifiers.

## Subscription reconciliation (live Stripe vs website access)

Recurring, cohort-wide reconciliation compares live Stripe subscription truth
against local website membership for every current or former Stripe subscriber
across Basic, Main, and Premium. It is separate from two neighbouring tools:

- `/api/users/payment-mismatches` and Studio Payment mismatches report checkout
  identity conflicts (who paid vs which account was entitled).
- The daily 03:30 UTC Stripe customer import discovers wholly unlinked Stripe
  customers. Reconciliation does not replace it; a Stripe customer with no
  local Stripe identifiers and no paid local tier stays an import/identity job.

### Cohort and source of truth

The cohort is every local user with any Stripe-membership indicator: a
non-empty `stripe_customer_id` or `subscription_id`, a paid base tier, or a
`stripe:active` / `stripe:churned` / `stripe:plan-*` tag. Live truth is read by
retrieving the stored `subscription_id` (or listing the customer's
subscriptions with `status=all`) and resolving the tier through the shared
metadata -> configured price -> amount/interval resolver. A missing lookup or
the absence of an `active` result is never treated as a cancellation.

### State contract

- `active`/`trialing`, not cancelling: entitled now. In-sync rows are counted
  but store no finding. Stale tier/subscription metadata is
  `active_metadata_drift` with action `repair_active_metadata`.
- `active`/`trialing` with `cancel_at_period_end=true`: `scheduled_cancellation`.
  Keep the paid tier + `subscription_id` through the period end; the only apply
  is repairing `pending_tier=free` and the access-ending date.
- `canceled` while still paid locally: `ended_subscription_still_entitled` with
  action `revert_to_free`. When no processed deletion webhook exists it is
  `suspected_missed_subscription_deleted` (same action). Apply uses the shared
  ended-subscription transition: base tier Free; subscription metadata and
  pending tier cleared; Stripe tags churned; community removal follows
  EFFECTIVE access, so an active `TierOverride` survives.
- `past_due` / `unpaid`: `dunning_grace`. Access is retained (matching
  `invoice.payment_failed`); these NEVER collapse into "no active subscription"
  or count as churn.
- `incomplete` / `incomplete_expired` / `paused`: `non_entitled_status_review`.
- No subscription/history for a linked or paid user: `missing_stripe_subscription`.
- Simultaneous `stripe:active` + `stripe:churned`: `inconsistent_stripe_status_tags`.
- Two local users sharing a customer/subscription id: `duplicate_stripe_ownership`
  — reported for identity cleanup, never eligible for apply.

### Cadence

The `stripe-subscription-reconciliation-daily` schedule runs at 04:30 UTC (after
the 03:30 import). It is always diagnostic/read-only, prevents concurrent runs,
and recovers a stuck `running` run after `STRIPE_RECONCILIATION_STALE_MINUTES`.
Admins are alerted only for new/changed actionable findings and after three
consecutive failed runs.

### Read-only check and confirmed apply

For day-to-day operator use, prefer the in-repo CLI. This is the single primary
read-only paying-users-versus-Stripe report:

```bash
uv run asl tier-reconcile run
```

It calls `POST /api/payments/tier-reconcile/runs` exactly once and waits by
polling the run-detail endpoint. It follows every findings `next_cursor`, then
prints the persisted report. It doesn't query Stripe directly, reimplement the
cohort/classifier, or change membership access.

Use these commands to start and resume a long check, show persisted output, and
list newest-first history:

```bash
uv run asl tier-reconcile run --no-wait
uv run asl tier-reconcile wait <run-id>
uv run asl tier-reconcile show <run-id>
uv run asl tier-reconcile list --page 1 --page-size 100
uv run asl tier-reconcile list --all-pages
```

`run` and `wait` poll every 2 seconds for up to 15 minutes by default. Positive
`--poll-interval` and `--timeout` values override those defaults. A timeout
exits 3 and Ctrl-C stops local polling only. Both print the run ID and resume
command because the server-side job continues. The client doesn't retry an
ambiguous enqueue failure, preventing accidental duplicate runs.

`show`, `wait`, and the completed result of `run` accept the server-owned
`--filter all|actionable|scheduled|warnings`, `--tier
basic|main|premium|free`, and unrestricted `--classification VALUE` filters.
They combine filters with AND and collect all pages by following the exact
returned cursor. Use `--page N --page-size 1..500` for one bounded page, and
don't combine an explicit page with `--all-pages`. `list` defaults to page 1
and page size 100. `--all-pages` follows history cursors. JSON/raw returns one
combined document with the run, evidence summary, ordered findings, and fetched
pagination metadata.

The report commands default to `--format table`. `--format json` is pretty and
`--format raw` is compact. Poll progress uses stderr so structured stdout stays
parseable. Table output masks email local parts and omits Stripe IDs. JSON/raw
redacts `email`, `stripe_customer_id`, `current_subscription_id`, and
`stripe_subscription_id`, then sets `pii_redacted: true`. `--include-pii`
reveals canonical emails/identifiers and sets the marker false. Store and share
that output only in an approved location. Report output never contains tokens,
authorization headers, `.env` contents, or Stripe payloads.

Reports exit 0 even with scheduled, actionable, or warning findings. Automation
may request exit 4 after output with `--fail-on actionable|warning|any`
(default `never`). API/auth/network/not-found and failed runs exit 1. Invalid
options exit 2, and wait timeouts exit 3. An empty completed report says all
checked members are in sync. Queued/running reports are labelled non-final.
Failed reports show only the run ID and secret-free server error, not partial
findings presented as success.

The CLI compares only canonical API fields. These include the website base
tier, exact Stripe subscription evidence, classification, action, outcome,
message, and webhook evidence. `past_due`/`unpaid` remain exact dunning states,
not canceled, "not paying", downgraded, or churned. The CLI doesn't fabricate
in-sync detail rows. Those members contribute to `cohort`/`ok` counts because
#1308 intentionally persists only non-OK findings.

The CLI also doesn't infer effective tier, override state, grace expiry,
notification delivery, or downgrade state. Issue #1413 exclusively owns the
future seven-day failed-payment grace policy. This report will show future
canonical fields only after the API supplies them.

The old `scripts/tier_reconcile_prod.sh` entry point now delegates to the
read-only `uv run asl tier-reconcile run` command. It contains no HTTP/token
parsing or apply prompt.

Diagnostic (read-only), no writes:

```text
POST /api/payments/tier-reconcile/runs      # enqueue a full cohort run (202)
GET  /api/payments/tier-reconcile/runs      # run history
GET  /api/payments/tier-reconcile/runs/<id> # run detail + findings (filters)
POST /api/payments/tier-reconcile           # omit dry_run -> preview only
```

The `runs/<id>` detail endpoint filters findings by `classification`, `tier`
(`basic|main|premium|free`), and `filter` (`actionable|scheduled|warnings|all`),
and paginates via `page` / `page_size` / `next_cursor`:

```
# Main-tier ended subscriptions in one run
curl -H "Authorization: Token $API_TOKEN" \
  "$BASE/api/payments/tier-reconcile/runs/<id>?classification=ended_subscription_still_entitled&tier=main"

# next page of findings
curl -H "Authorization: Token $API_TOKEN" \
  "$BASE/api/payments/tier-reconcile/runs/<id>?page=2"
```

An unknown `tier`/`classification` returns 422 with the field in
`details.field`. All three `runs` endpoints (and the apply endpoint) are
staff-token-only: a missing/non-staff token returns 401 before any Stripe call
or task enqueue, and no run is created. The synchronous `GET
/api/payments/tier-reconcile/diagnostics` endpoint stays backward-compatible and
only accepts `email` and `include=ok` — the tier/classification/cursor filters
live on the `runs` endpoints and the Studio report, not on `diagnostics`. All
`runs` endpoints and their parameters are documented in `_docs/openapi.json`.

A write requires BOTH `dry_run=false` AND `confirm="apply_stripe_truth"` and
targets explicit emails:

```
POST /api/payments/tier-reconcile
{ "emails": ["someone@example.com"], "dry_run": false,
  "confirm": "apply_stripe_truth" }
```

Only deterministic drift is applied (active/trialing repair,
scheduled-cancellation metadata, confirmed `canceled` reversion). Every
warning/review/duplicate classification is skipped, apply is idempotent, and
each actual change writes a secret-free audit event.

This confirmed `asl tier-reconcile apply --data ...` path remains intentionally
separate from `run`/`list`/`show`/`wait`. No reporting command prompts for,
shortcuts to, or automatically performs an apply.

Studio: `/studio/payments/subscription-reconciliation/` shows the latest run,
summary counts, filters, and links to member and Stripe records. `Check all
Stripe subscriptions` enqueues a read-only run; there is no Studio bulk-apply.

## STRIPE_RECONCILIATION_STALE_MINUTES

Minutes after which a stuck `running` reconciliation run is marked failed so the
next daily run can start. Default 120, clamped to a 5-minute floor.
