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
- No additional Google-side configuration is required — the platform
  uses GA's default measurement (autocollected events: `page_view`,
  `session_start`, etc.). Conversion events and custom user properties
  are out of scope for this setting (see issue #771 "Out of scope").

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

## `aslab_aid` user_property and user_id binding

Every GA event now carries the `aslab_aid` user_property and (on the
page-load `gtag('config', ...)` call) `user_id`. Both values come from
the `aslab_aid` cookie set by `analytics.middleware.CampaignTrackingMiddleware`
and validated in `website.context_processors.site_context`. The same
UUID is stored on `analytics.UserAttribution.anonymous_id`, so GA
exports can be joined to our DB on this column for source-attributed
funnel analysis.

The cookie is `httponly=True`, so JavaScript cannot read it. The value
is rendered server-side into the inline GA loader script inside
`templates/base.html`. Bot, admin, and static-asset requests skip the
middleware (no cookie set), so the template guards on
`{% if aslab_anon_id %}` to avoid emitting empty values.

## Conversion events

The platform fires the following `gtag('event', ...)` calls. All are
gated on `GOOGLE_ANALYTICS_ID` being set — when GA is disabled, none
of these events render or execute.

| Event | Surface | Trigger | Parameters |
|---|---|---|---|
| `sign_up` | Newsletter subscribe form (`templates/includes/subscribe_form.html`) | XHR success branch | `method: 'newsletter'` |
| `sign_up` | Email signup form (`static/js/accounts/inline-register.js`) | XHR success branch | `method: 'email'` |
| `sign_up` | OAuth signup (Google / GitHub / Slack) | `accounts.signals.set_signup_source_oauth_on_social_signup` (only fires on `social_account_added`, i.e. brand-new social account link) | `method: 'oauth'`, `provider: '<google|github|slack>'` |
| `event_register` | Live event registration (`static/js/events/event_detail.js`) | XHR success, before page reload (uses `event_callback`) | `event_slug: '<slug>'` |
| `course_enroll` | Course enroll endpoint (`content/views/courses.py:enroll_course`) | Next-page render after server-side redirect (session flag, one-shot) | `course_slug: '<slug>'` |
| `purchase` | Stripe checkout return (`templates/content/dashboard.html`) | `?checkout=success` query string on `/account/` | `value: <amount>`, `currency: 'EUR'`, `items: [{ tier: '<slug>', billing_period: '<period>' }]` |

Find each event in GA Reports > Engagement > Events. The
`aslab_aid` user_property is set on the GA session before any event
fires, so every conversion above carries it automatically.

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
