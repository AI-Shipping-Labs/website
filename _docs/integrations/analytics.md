# Analytics integration setup

This page documents every setting registered in
`integrations/settings_registry.py` under the `analytics` group. Each
section follows the same template — Purpose, Without it, Where to find
it, Prereqs, Rotation, Test vs live.

The platform supports a lightweight pageview-tracking hook for Google
Analytics 4. Deeper analytics (signup funnels, campaign attribution,
audience verification) live in Studio's own analytics screens and do
not depend on this group.

## GOOGLE_ANALYTICS_ID

Purpose: Google Analytics 4 measurement ID injected into the GA loader
script in `templates/base.html`. When set, every public page renders the
standard `gtag.js` snippet that reports pageviews to the configured GA4
property. The value is exposed to templates as `google_analytics_id` by
`website/context_processors.py:site_context`.

Without it: No GA loader is emitted — pages render with no
`googletagmanager.com` script tag and no `gtag` call. Browsers make no
network call to Google. This is the default for fresh installs, local
dev, and CI: there is no DB row, no Django setting, and no env var
shipped, so GA stays disabled unless an operator deliberately turns it
on through Studio.

Where to find it:

- Sign in to Google Analytics: `https://analytics.google.com`.
- Open Admin (gear icon, bottom-left) > Data Streams.
- Click your web data stream.
- Copy the "Measurement ID" field — it looks like `G-XXXXXXXXXX`.

Paste that value into Studio > Settings > Analytics >
`GOOGLE_ANALYTICS_ID` and save.

Prereqs:

- A Google Analytics 4 property with a web data stream pointed at this
  site's domain. GA4 (not Universal Analytics, which Google retired in
  2023).
- The measurement ID only turns the direct `gtag.js` loader on or off.
  Event naming, custom dimensions, key events, and optional GTM wiring
  are documented below.

Rotation: Rotating the measurement ID means pointing the site at a
different GA property. Paste the new `G-...` value into Studio and save.
The change takes effect on the next request — there is no deploy
required. The old property stops receiving traffic immediately; the new
one starts.

Production currently uses `G-HXSHF376NY`. Rotating it is a Studio
Settings change, not a deploy.

Test vs live: GA does not distinguish test from live properties at the
measurement-ID layer — a property is a property. To keep dev/staging
traffic out of the production GA property, either:

- Leave the setting blank in non-production deploys (recommended — no
  GA loader is emitted at all), or
- Create a separate GA4 property for staging and paste its measurement
  ID into the staging environment.

Local development and the CI test suite leave the setting blank by
default; no test setup is required to suppress GA during
`manage.py test`.

## `aslab_aid`, `login_state`, and `member_tier`

The direct `gtag.js` bootstrap in `templates/base.html` now sets:

- `user_id = aslab_aid` when the `aslab_aid` cookie is present and
  parses as a UUID4.
- `user_properties.aslab_aid = <uuid>` on the same condition.
- `user_properties.login_state = anonymous|authenticated` on every page
  render.
- `user_properties.member_tier = free|basic|main|premium` on
  authenticated renders when the user has a tier row.

The same UUID is stored on
`analytics.UserAttribution.anonymous_id`, so GA exports can be joined
back to the DB on `aslab_aid` for attribution analysis.

The cookie itself is `httponly=True`, so JavaScript never reads it
directly; `website.context_processors.site_context` validates it and
renders only the UUID into the inline GA bootstrap. Do not send email
addresses, names, Django user IDs, Stripe customer IDs, or raw session
IDs to GA. `aslab_aid` is the only cross-system join key.

## Signup Funnel Contract

The client handlers are safe to render even when GA is disabled. When
`GOOGLE_ANALYTICS_ID` is blank there is no loader and no request to
Google; the handlers simply no-op because `window.gtag` is absent.

### Event names and required parameters

| Event | Surface | Trigger | Required parameters |
|---|---|---|---|
| `signup_start` | Newsletter subscribe forms (`templates/includes/subscribe_form.html`, footer newsletter form) | Submit begins, before `/api/subscribe` | `method='newsletter'`, `signup_kind='newsletter'`, `entry_path`, `login_state` |
| `signup_start` | Email account signup (`static/js/accounts/inline-register.js`) | Password validation passes, before `/api/register` | `method='email'`, `signup_kind='account'`, `entry_path`, `login_state` |
| `signup_start` | OAuth signup buttons (`templates/accounts/includes/_oauth_providers.html`) | Click, before navigation to provider | `method='oauth'`, `provider`, `signup_kind='account'`, `entry_path`, `login_state` |
| `sign_up` | Newsletter subscribe success | Successful `/api/subscribe` response | `method='newsletter'`, `signup_kind='newsletter'`, `entry_path`, `login_state` |
| `sign_up` | Email account signup success | Successful `/api/register` response | `method='email'`, `signup_kind='account'`, `entry_path`, `login_state` |
| `sign_up` | Brand-new OAuth signup only | First post-OAuth render after `social_account_added` creates the new social identity | `method='oauth'`, `provider`, `signup_kind='account'`, `login_state='authenticated'` |
| `event_register` | Live event registration (`static/js/events/event_detail.js`) | XHR success before reload | `event_slug`, `login_state` |
| `course_enroll` | Course enroll endpoint (`content/views/courses.py:enroll_course`) | Next-page render after server-side redirect | `course_slug`, `login_state='authenticated'` |
| `purchase` | Stripe checkout return (`templates/content/dashboard.html`) | `?checkout=success` on `/account/` | `currency='EUR'`, `login_state='authenticated'` plus optional `value` and `items[{tier,billing_period}]` |

Interpretation of signup conversions:

- Newsletter-only signup: `method='newsletter'` and
  `signup_kind='newsletter'`
- Full account signup: `method='email'` or `method='oauth'` and
  `signup_kind='account'`

Returning OAuth logins and existing-account OAuth provider linking do
not emit `sign_up`.

### GA4 custom dimensions and user properties

Register these in GA4 Admin so Explorations and standard reports can
segment the funnel fields:

- User-scoped properties: `aslab_aid`, `login_state`, `member_tier`
- Event-scoped dimensions: `signup_kind`, `method`, `provider`,
  `entry_path`

If operators want `login_state` inside event reports as well as
user-scoped reporting, register an event-scoped `login_state` dimension
too; the event handlers already include it in their parameter payloads.

### Key-event / conversion setup

- Mark `sign_up` as the completed signup conversion (GA4 "Key event").
- Use `signup_start` for funnel-step analysis only; do not mark it as
  the completed signup conversion.
- Keep `event_register`, `course_enroll`, and `purchase` as separate
  downstream conversion events if the property already relies on them.

### Stripe Payment Link success-URL query parameters

For the `purchase` event to record `value`, `currency`, and `items`,
Stripe Payment Links must be configured so the success URL appends
the tier slug, amount, and billing period as query-string parameters:

```
https://aishippinglabs.com/account?checkout=success&tier={tier_slug}&value={amount}&billing={monthly|yearly}
```

Stripe Payment Links support success-URL templating in the dashboard
("After payment" > "Custom URL"). Use the literal slug
(`basic` / `pro` / `enterprise`), the integer or decimal price in EUR
(e.g. `29`), and `monthly` or `yearly` for the billing period. Missing
parameters still fire a `purchase` event but without `value` /
`items`, which is less useful for revenue reporting in GA.

### Server-side session flag (Pattern C)

Course enroll and OAuth signup both complete on a server-side redirect
to a fresh page. They use a one-shot session flag instead of a
client-side hook:

1. The server view (or signal handler) writes
   `request.session['gtag_event_pending'] = {'event': '<name>',
   'params': {...}}`.
2. `website.context_processors.site_context` pops the key on the next
   render and exposes it as `gtag_pending_event`.
3. `templates/base.html` (inside the existing `{% if google_analytics_id %}`
   block) emits a `gtag('event', ...)` call wired to the popped data.
4. Popping (not just reading) makes it one-shot: a second page render
   in the same session never re-fires the event.

The event name is validated against `^[A-Za-z][A-Za-z0-9_]{0,39}$` and
the params dict is JSON-serialised in the context processor before
template rendering, so the inline script is safe against arbitrary
content sneaking into the page.

## Optional GTM Setup

Do not add a Google Tag Manager container script to the app just to
capture these events. The website already emits the direct `gtag.js`
loader from `templates/base.html`, and double-installing pageview tags
or duplicate GA4 event tags will inflate the numbers.

If the GA4 property is managed from GTM for operator reasons, mirror the
existing event names in GTM rather than inventing new ones:

- Trigger names / dataLayer event names: `signup_start`, `sign_up`,
  `event_register`, `course_enroll`, `purchase`
- Map the parameters above directly (`method`, `signup_kind`,
  `provider`, `entry_path`, `login_state`, etc.)
- Reuse the existing measurement ID; do not fire a second GA4 pageview
  tag on top of the direct `gtag.js` bootstrap

If GTM cannot be configured without duplicating the direct `gtag.js`
events, leave GTM out of the path and keep the direct loader as the
single source of truth.

## Production / Staging Checklist

- Production: set the live GA4 measurement ID in Studio >
  Settings > Analytics > `GOOGLE_ANALYTICS_ID`.
- Staging / dev: either leave `GOOGLE_ANALYTICS_ID` blank or set a
  separate non-production GA4 property.
- Never point staging/dev at the production measurement ID.
- After changing the measurement ID, verify in GA4 Realtime or
  DebugView that `signup_start` and `sign_up` arrive with the documented
  parameters before relying on the reports.
