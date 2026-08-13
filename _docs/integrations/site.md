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
`website/context_processors.py:128:_build_env_mismatch_payload`. Comma- or
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

## PRIVACY_REQUEST_EMAIL

Purpose: Validated team mailbox that receives account-deletion requests
submitted by signed-in members from the Privacy & data card on `/account/`.
The platform sends one transactional message To this address and visibly Cc's
the member's current login email. The message identifies the member by login
email and Support ID and links to the existing Studio member detail.

Default: `team@aishippinglabs.com`.

Without a valid value: Runtime validation rejects malformed overrides and the
request page returns a truthful retryable error with a direct
`mailto:team@aishippinglabs.com` fallback. No request is marked received and no
account state changes. A corrected setting lets the member retry the same
durable request without creating duplicate accepted mail.

Where to find it: This is operator intent. Use the shared mailbox responsible
for privacy requests rather than an individual address.

Prereqs: Amazon SES transactional delivery must be configured and enabled.
The sender domain must be verified; the recipient does not need separate SES
verification once the AWS account is out of sandbox.

Rotation: Safe to change at any time. New requests and failed-delivery retries
use the current validated value; an already accepted request is not resent.

Test vs live: Use a non-production team mailbox when exercising real SES in a
non-production environment. Local and automated tests keep SES disabled or
mock the send path.

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

## STAFF_SIGNUP_NOTIFY_EMAIL

Purpose: Single staff mailbox used for two paid-signup email routes
(Basic and above):

- Hidden BCC on the actual member-facing paid welcome/invite email.
- Separate structured internal paid-signup heads-up email addressed To
  staff.

Read by `community/services/staff_notifications.py::notify_paid_signup`.
Distinct from `PAYMENT_NOTIFICATION_EMAIL` — the latter is the
short operator audit ping; this one drives the founder-led
high-touch onboarding loop.

Without it (blank): The co-founder welcome still goes to the new
user without any staff CC/BCC, and the structured staff heads-up
email is skipped. The Slack post still fires if
`STAFF_SIGNUP_NOTIFY_CHANNEL_ID` is set.

Invalid value: Studio rejects a malformed email address before saving any
setting in the Site group. Runtime delivery also validates values supplied by
the environment, imports, or legacy rows; an invalid value is omitted from the
BCC and structured heads-up so SES can still deliver the member welcome. The
operator log names the setting key but never prints the rejected value.

Reply rule: Do not use this setting as Reply-To and do not visible-CC
staff on paid welcomes. Member replies go only to
`SES_WELCOME_REPLY_TO_EMAIL` so there is one monitored conversation
path.

Where to find it: This is operator intent — pick the mailbox the
founders read. A shared list address (`founders@<your-domain>`) so
the alert is not tied to one human.

Prereqs: Same as `PAYMENT_NOTIFICATION_EMAIL` — SES configured per
`ses.md`; recipient need not be SES-verified.

Rotation: Safe to change at any time. The next paid checkout uses
the new value.

Test vs live: n/a. Use per-environment values if non-production
test checkouts should route somewhere else.

## CRM_EXPORT_MAX_LIMIT

Purpose: Hard ceiling on the page size for the CRM export endpoint
(`GET /api/crm/export`, issue #1079). The requested `limit` query
param is clamped to this value so a single call cannot pull an
unbounded per-user aggregate. Read by
`api/views/crm_export.py:_export_max_limit` via
`get_config("CRM_EXPORT_MAX_LIMIT", 200)`.

Default: `200`.

Without it (blank): Falls back to `200`. A blank, non-numeric, or
non-positive override is ignored and the default is used.

Where to find it: This is operator intent — pick the largest page
size you are comfortable serving in one call. Larger values make the
deeply-nested aggregate response heavier; raise it only if a trusted
analyst job needs bigger pages.

Prereqs: None beyond a staff-owned API token to call the endpoint.

Rotation: Safe to change at any time. The next export request uses
the new value (no redeploy).

Test vs live: n/a. Use per-environment values if a staging analyst
job needs a different ceiling.

## ONBOARDING_REMINDER_ENABLED

Purpose: Master switch for the one-week onboarding reminder sweep (issue
#1133). When on, a daily job emails paid members who received their
onboarding-link welcome but have not completed onboarding after
`ONBOARDING_REMINDER_DELAY_DAYS`. When off, the sweep is a no-op (no
emails, no logs).

Default: `true` (on).

Without it: Defaults on; switchable without a redeploy.

Where to find it: Studio integration settings (Site group). Boolean toggle.

Prereqs: The daily reminder job must be scheduled; SES must be configured to
send the reminder email.

Rotation: Safe to flip at any time.

Test vs live: Configure independently in each deployment.

## ONBOARDING_REMINDER_DELAY_DAYS

Purpose: Days after the onboarding-link welcome email before the reminder
is due (issue #1133). A member whose earliest welcome is older than this and
who has not onboarded is reminded once.

Default: `7`.

Without it: A blank, non-numeric, or non-positive override falls back to 7.

Where to find it: Studio integration settings (Site group). Set a positive
integer.

Prereqs: `ONBOARDING_REMINDER_ENABLED` must be on for the delay to matter.

Rotation: Safe to change; the next sweep uses the new window.

Test vs live: Configure independently in each deployment.

## SPRINT_BADGE_WINDOW_DAYS

Purpose: Window in days around a sprint start / end that flips the
date-derived sprint badge to "Starting soon" (within this many days before
start) and "Ending soon" (within this many days of end). A larger window
surfaces the soon-states earlier.

Default: `7`.

Without it: A blank, non-numeric, or non-positive override falls back to 7.

Where to find it: Studio integration settings (Site group). Set a positive
integer.

Prereqs: None.

Rotation: Safe to change; badge computation is per-request.

Test vs live: Configure independently in each deployment.

## SPRINT_END_AUTO_DISTRIBUTE_FEEDBACK_ENABLED

Purpose: When on, the daily sprint-end recap job distributes attached
sprint feedback requests before sending member recaps, so the recap can link
to each member feedback form.

Default: `false` (off).

Without it: Defaults off for staff-controlled distribution.

Where to find it: Studio integration settings (Site group). Boolean toggle.

Prereqs: Sprint feedback requests must be attached to the sprint; the
sprint-end recap job must be scheduled.

Rotation: Safe to flip at any time.

Test vs live: Configure independently in each deployment.

## SOCIAL_YOUTUBE_URL

Purpose: Public YouTube channel URL rendered as a YouTube icon in the
footer social row (issue #1356). Read by
`website.context_processors.site_context` via
`get_config("SOCIAL_YOUTUBE_URL", "")` and exposed to templates as
`footer_social.youtube`.

Default: empty (no default handle).

Without it (blank): The YouTube icon does not render in the footer; the
rest of the social row (Join Slack and any other configured icons) is
unaffected.

Where to find it: Your community's public YouTube channel URL.

Prereqs: None.

Rotation: Safe to change at any time; the next page render uses the new
value (no redeploy).

Test vs live: Set per-environment values if staging should point at a
different channel.

## SOCIAL_LINKEDIN_URL

Purpose: Public LinkedIn page URL rendered as a LinkedIn icon in the
footer social row (issue #1356). Read by
`website.context_processors.site_context` via
`get_config("SOCIAL_LINKEDIN_URL", "")` and exposed to templates as
`footer_social.linkedin`.

Default: empty (no default handle).

Without it (blank): The LinkedIn icon does not render in the footer.

Where to find it: Your community's public LinkedIn page URL.

Prereqs: None.

Rotation: Safe to change at any time (no redeploy).

Test vs live: Set per-environment values if needed.

## SOCIAL_GITHUB_URL

Purpose: Public GitHub organisation URL rendered as a GitHub icon in the
footer social row (issue #1356). Read by
`website.context_processors.site_context` via
`get_config("SOCIAL_GITHUB_URL", "")` and exposed to templates as
`footer_social.github`.

Default: empty (no default handle).

Without it (blank): The GitHub icon does not render in the footer.

Where to find it: Your community's public GitHub organisation URL.

Prereqs: None.

Rotation: Safe to change at any time (no redeploy).

Test vs live: Set per-environment values if needed.

## SOCIAL_X_URL

Purpose: Public X (formerly Twitter) profile URL rendered as an X icon
in the footer social row (issue #1356). Read by
`website.context_processors.site_context` via
`get_config("SOCIAL_X_URL", "")` and exposed to templates as
`footer_social.x`.

Default: empty (no default handle).

Without it (blank): The X icon does not render in the footer.

Where to find it: Your community's public X profile URL.

Prereqs: None.

Rotation: Safe to change at any time (no redeploy).

Test vs live: Set per-environment values if needed.
