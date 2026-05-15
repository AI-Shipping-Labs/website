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
- Subscribe to exactly these 5 events:
  - `checkout.session.completed`
  - `customer.subscription.updated`
  - `customer.subscription.deleted`
  - `invoice.payment_failed`
  - `customer.updated`

  Other events (e.g. `invoice.paid`, `invoice.payment_succeeded`) are
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

Test vs live: Test-mode and live-mode portals have separate URLs and
separate configurations. Match the mode to your `STRIPE_SECRET_KEY`
otherwise the link will load a portal that has no record of the
customer's subscription.

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
