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
