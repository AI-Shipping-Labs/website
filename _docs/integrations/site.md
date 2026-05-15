# Site integration setup

This page documents every setting registered in
`integrations/settings_registry.py` under the `site` group. Each
section follows the same template — Purpose, Without it, Where to
find it, Prereqs, Rotation, Test vs live.

Unlike the other integration groups, these settings configure the
platform itself rather than a third-party service. They control link
generation, host-mismatch detection, default timezone display, and
operator-side notifications. There is no external dashboard to visit
for these values.

## SITE_BASE_URL

Purpose: Canonical absolute URL of this deploy
(e.g. `https://aishippinglabs.com`). Used everywhere the platform
needs to generate a full link rather than a relative path:

- OAuth callback URLs (Google/GitHub sign-in providers redirect here).
- Calendar invites embed it as the event landing-page URL
  (`events/services/calendar_invite.py`).
- Email templates render absolute links so they keep working when the
  user clicks from outside the browser session.
- UTM-campaign normalization
  (`integrations/models/utm_campaign.py:131`) prefixes site-relative
  paths with this value.
- Host-mismatch banner detection
  (`website/context_processors.py:168`) compares this URL to the
  request host.

Resolution order: DB-stored `IntegrationSetting` > environment
variable `SITE_BASE_URL` > Django setting `SITE_BASE_URL` (defaults
to `https://aishippinglabs.com`).

Without it: Falls back to the Django settings default. If that
default doesn't match the deploy's actual public hostname, OAuth
callbacks fail (Google/GitHub reject the mismatched redirect URI),
email links point at the wrong host, and the host-mismatch banner
shows on every request.

Where to find it: This is operator intent — set it to the canonical
HTTPS URL where this deploy is reachable, including the protocol but
no trailing slash:

```
https://aishippinglabs.com
```

For staging/dev environments, use the actual hostname
(e.g. `https://staging.aishippinglabs.com`).

Prereqs:
- The hostname must resolve to this deploy's load balancer.
- HTTPS must be working (the platform sets several security headers
  that assume HTTPS).
- Any OAuth provider used for sign-in must list the corresponding
  callback URL (typically `<SITE_BASE_URL>/accounts/<provider>/login/callback/`)
  as an authorised redirect URI.

Rotation: Stable for the lifetime of the deploy. Update when you:

- Migrate to a new domain (re-register OAuth callbacks at the new
  host first, then update this setting).
- Promote a staging environment to production (rare).

Window of impact: changing this value mid-deploy invalidates any
in-flight OAuth flow (the callback URL no longer matches) and breaks
the host-mismatch banner for a few seconds while caches drain.

Test vs live: n/a. One value per environment. Pin to the
environment's public hostname.

## SITE_BASE_URL_ALIASES

Purpose: Additional hostnames the platform recognises as "this
deploy" so they do not trigger the host-mismatch banner. Read by
`website/context_processors.py:104:host_mismatch_context`. Comma- or
whitespace-separated (newlines work too because the field is
multiline).

Use cases:
- A short link domain (`alab.community`) routed to the same backend.
- An apex + www variant (`aishippinglabs.com` and
  `www.aishippinglabs.com`) where only one is canonical.
- A region-specific CDN domain.

Without it: Empty list — only the host portion of `SITE_BASE_URL`
itself is treated as canonical. Any other host the request comes in
on shows the host-mismatch banner, asking the user to switch to the
canonical URL.

Where to find it: This is operator intent — list every alternate
hostname routed to this deploy. One per line works best:

```
www.aishippinglabs.com
alab.community
```

Prereqs: Each listed host must actually route to this backend (DNS +
load balancer config). Listing a host here does not make it work —
it only suppresses the banner.

Rotation: Update whenever you add or remove an alias domain. Stale
entries are harmless — they just suppress the banner for a host
that no longer routes here.

Test vs live: n/a. One list per environment. Production lists prod
aliases, staging lists staging aliases.

## EVENT_DISPLAY_TIMEZONE

Purpose: Default IANA timezone for public event times when the
browser cannot provide one. Read by
`events/services/display_time.py:get_event_display_timezone`. Used
when rendering server-side event lists (e.g. in emails, RSS feeds,
calendar invites) where there is no browser context.

Default: `Europe/Berlin`.

Without it: Falls back to `Europe/Berlin` (the constant
`DEFAULT_EVENT_DISPLAY_TIMEZONE`). Acceptable for European
audiences; misleading for events run primarily for a US audience.

Where to find it: This is operator intent — pick the IANA timezone
name (e.g. `Europe/Berlin`, `America/New_York`, `Asia/Tokyo`) that
matches the cohort's primary location. The official list is at:

```
https://en.wikipedia.org/wiki/List_of_tz_database_time_zones
```

Prereqs: Must be a valid IANA timezone string. The display helper
falls back to `Europe/Berlin` silently on invalid names — there is
no user-visible error.

Rotation: Update when your audience shifts (e.g. a new cohort runs in
a different region). No restart required; the new value takes effect
on the next request.

Test vs live: n/a. One value per environment.

## PAYMENT_NOTIFICATION_EMAIL

Purpose: Operator email address that receives an internal
notification whenever a Stripe checkout completes — a new paid
signup, tier upgrade, or course purchase. Read by
`payments/services/webhook_handlers.py:210`. Best-effort: if the
email service is unavailable, the webhook still processes the
payment and updates the user's tier; the notification is dropped
silently.

Optional. Leave blank to disable internal notifications entirely —
there is no hard-coded default, so a blank setting means nobody is
notified.

Without it (blank): No internal notification fires on checkout
completion. The user still receives their own receipt from Stripe,
and their tier still updates on the platform side. Only the
operator loses real-time visibility into new paid signups.

Where to find it: This is operator intent — set it to whichever
inbox should receive these alerts. Typically a shared
ops/notifications mailbox (`ops@<your-domain>`,
`payments@<your-domain>`) so the alert is not tied to one human.

Prereqs:
- The platform must have SES configured (see `ses.md`) — internal
  notifications use the same `SES_TRANSACTIONAL_FROM_EMAIL` sender
  as account email.
- The recipient address need not be SES-verified (SES only requires
  the sender to be verified, not the recipient, once out of sandbox).

Rotation: Safe to change at any time. The next checkout-complete
event uses the new value.

Test vs live: n/a. Use a per-environment value if you want
non-production checkout test events routed to a different inbox.
