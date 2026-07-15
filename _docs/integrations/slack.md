# Slack integration setup

This page documents every Slack-related setting registered in
`integrations/settings_registry.py` (the `slack` group). Each section
follows the same template — Purpose, Without it, Where to find it,
Prereqs, Rotation, Test vs live — so an operator can answer "do I need
to set this right now, or can I defer it?" without leaving the page.

The platform integrates with Slack as a bot user: it posts
announcements, reacts to community signals, and renders deep links
into the workspace. Routing is environment-aware
(`community/slack_config.py`) — the active routing mode is picked by
`SLACK_ENVIRONMENT`, which selects between the production, development,
or test channel-id keys.

Direct deep-link URLs are intentionally written in code blocks so they
do not render as clickable links. Copy them into the browser.

## SLACK_ENABLED

Purpose: Master kill-switch for the Slack integration. Read by
`community/services/slack.py:slack_api_enabled` (via
`integrations/config.py:is_enabled`) before any outbound bot call.
When false, the platform skips Slack posting entirely and falls back to
email-only flows in `community/tasks/hooks.py` (e.g. invite send). Off
by default so dev and test environments stay silent until an operator
opts in.

Without it (when false): No Slack messages are posted, no community
events are consumed, and invite hooks fall back to surfacing
`SLACK_INVITE_URL` in email. Everything else on the platform continues
to work — Slack is non-critical, by design.

Where to find it: Studio-only setting. Set it to `true` to turn on
Slack. There is no Slack dashboard to consult — the value is operator
intent expressed locally.

Prereqs: `SLACK_BOT_TOKEN` must also be set; `slack_api_enabled` returns
false when either condition is missing. Setting `SLACK_ENABLED=true`
without a bot token is a no-op for outbound posts.

Rotation: n/a. This is a boolean operator switch, not a credential.
Toggle it off and back on without consequence — the next post will
re-check both this flag and the bot token.

Test vs live: n/a. The same flag applies to all environments. Use
`SLACK_ENVIRONMENT` to pick which channel set the bot writes to.

## SLACK_ENVIRONMENT

Purpose: Selects which channel-id keys the bot uses. Read by
`community/slack_config.py:get_slack_environment`. Three valid values:
- `production` — uses `SLACK_ANNOUNCEMENTS_CHANNEL_ID` and
  `SLACK_COMMUNITY_CHANNEL_IDS`.
- `development` — uses `SLACK_DEV_ANNOUNCEMENTS_CHANNEL_ID` and
  `SLACK_DEV_COMMUNITY_CHANNEL_IDS`.
- `test` — uses `SLACK_TEST_ANNOUNCEMENTS_CHANNEL_ID` and
  `SLACK_TEST_COMMUNITY_CHANNEL_IDS`.

Any unknown value silently falls back to `development`, so the
production channel is never reached by accident.

Without it: Defaults to `development`. The bot posts to the dev
channels only, never to production. Production traffic is silenced
until the value is explicitly set to `production`.

Where to find it: Studio-only setting. There is no Slack dashboard to
consult — pick the environment name that matches the deploy this
process is running in.

Prereqs: The channel-id keys for the chosen environment must be set or
the announcements code paths log "no channel configured" and skip the
post.

Rotation: n/a. Set it once per environment.

Test vs live: This setting is exactly the test-vs-live switch — flipping
the value swaps the bot's target channels between the three channel
sets defined elsewhere in the registry.

## SLACK_BOT_TOKEN

Purpose: Bot user OAuth token (the `xoxb-...` prefix). Used by
`community/services/slack.py:SlackCommunityService` to authenticate
every API call — `chat.postMessage`, `users.info`, `conversations.history`,
etc. Without it the bot cannot post or read.

Without it: `slack_api_enabled` returns false, and the bot is
effectively disabled — same observable behavior as `SLACK_ENABLED=false`.
Outbound announcements are skipped silently, inbound community-signal
consumers receive no events.

Where to find it:

- Direct link to your Slack app list:

  ```
  https://api.slack.com/apps
  ```

- Open the app, then "OAuth & Permissions" in the left sidebar.
- Under "OAuth Tokens for Your Workspace", copy the "Bot User OAuth
  Token" (the one prefixed `xoxb-`). The separate user token needed only
  for channel-thread replies is documented under
  `SLACK_PLAN_SPRINTS_USER_TOKEN`.

Prereqs:
- A Slack app installed to your workspace.
- Bot token scopes that match what the platform calls. Required:
  - `chat:write` — post announcements and staff heads-up messages.
  - `chat:write.public` — post to public channels without an explicit invite.
  - `channels:read` — list and resolve public channel IDs.
  - `channels:history` — read message history in PUBLIC channels. REQUIRED
    for the `#plan-sprints` history discovery call.
  - `groups:read` — list and resolve PRIVATE channel IDs.
  - `groups:history` — read message history in PRIVATE channels. REQUIRED
    instead of `channels:history` IF `#plan-sprints` is a private channel.
  - `users:read` — resolve Slack user IDs to members.
  - `users:read.email` — match Slack users to platform accounts by email.
  - `reactions:read` — capture community reaction signals.

  Missing scopes manifest as `missing_scope` errors at the Slack API. In
  particular, the currently-granted set (`users:read`, `users:read.email`,
  `channels:read`, `chat:write`) is MISSING `channels:history` /
  `groups:history`, so the daily `#plan-sprints` ingest cannot read
  messages until that scope is added.
- The bot user must be invited to every channel listed in
  `SLACK_*_COMMUNITY_CHANNEL_IDS` and `SLACK_*_ANNOUNCEMENTS_CHANNEL_ID`,
  or `chat.postMessage` returns `not_in_channel`. The bot must likewise be
  invited to `#plan-sprints` (see `SLACK_PLAN_SPRINTS_CHANNEL_ID` below) or
  the history reads return `not_in_channel`.

Rotation: Safe to rotate, but requires a re-install in some cases.

1. In the Slack app config, click "OAuth & Permissions" > "Reinstall to
   Workspace" if you've changed scopes; otherwise click "Rotate Token"
   under the bot token row. Slack shows the new `xoxb-...` once. After any
   scope change a reinstall is mandatory; if the reinstall issues a new
   token, the `SLACK_BOT_TOKEN` update in the next step is also required.
2. Update this setting via Studio (Integration settings > Slack >
   `SLACK_BOT_TOKEN`) or via `POST /api/integrations/settings`.
3. Window of impact: until the new value is saved, all bot API calls
   fail with `invalid_auth`. Posts queued while the token is bad simply
   error and are dropped — Slack does not retain a server-side queue
   for them.

Test vs live: n/a. One token per workspace. Use separate Slack
workspaces (and apps) for development and test, and store each
workspace's token in the corresponding environment's `SLACK_BOT_TOKEN`.

## SLACK_COMMUNITY_CHANNEL_IDS

Purpose: Comma-separated list of Slack channel IDs the bot watches for
community signals — mentions, reactions, threads — used by the
community analytics pipeline. Used only when `SLACK_ENVIRONMENT` is
`production`.

Without it: No production community signals are captured. Reactions,
mentions, and threads in those channels do not produce platform-side
events. Existing data is unaffected; only new signal capture stops.

Where to find it:
- In Slack, right-click each channel > "View channel details" > scroll
  to the bottom — the channel ID is shown there (looks like `C01ABC234`).
- Or use the API:

  ```
  https://api.slack.com/methods/conversations.list/test
  ```

  with a token that has `channels:read`, then pick the channel IDs you
  want to monitor.

Prereqs: The bot must be a member of each listed channel. For private
channels, the bot also needs the `groups:read` scope and a manual
invite.

Rotation: Channel IDs are permanent for the lifetime of the channel. If
you replace a channel, update the comma-separated list and restart the
listener (the worker process re-reads on each iteration).

Test vs live: This key is the live (production) list. Use
`SLACK_DEV_COMMUNITY_CHANNEL_IDS` for development and
`SLACK_TEST_COMMUNITY_CHANNEL_IDS` for test mode.

## SLACK_ANNOUNCEMENTS_CHANNEL_ID

Purpose: Single channel ID where the bot posts new content and event
announcements when `SLACK_ENVIRONMENT=production`. Read by
`community/slack_config.py:get_slack_announcements_channel_id`.

Without it: Announcement code paths log "no channel configured" and
skip the post. Content still publishes to the website; it just does
not get a Slack announcement.

Where to find it: Same flow as `SLACK_COMMUNITY_CHANNEL_IDS` — right
click the channel in Slack > "View channel details" > copy the ID at
the bottom.

Prereqs: The bot must be a member of the announcements channel. Public
channels: invite via `/invite @<bot-name>`. Private channels: same
invite, plus `groups:write` if you want the bot to be added without an
admin.

Rotation: Permanent for the channel. Replace the value when you cut
over to a new announcements channel.

Test vs live: This key is the live (production) channel. Use
`SLACK_DEV_ANNOUNCEMENTS_CHANNEL_ID` for development and
`SLACK_TEST_ANNOUNCEMENTS_CHANNEL_ID` for test.

## SLACK_DEV_COMMUNITY_CHANNEL_IDS

Purpose: Development-only community channel IDs. Same shape as
`SLACK_COMMUNITY_CHANNEL_IDS`. Used only when `SLACK_ENVIRONMENT=development`.

Without it (in development mode): Community-signal capture is silent
on the development workspace. Production traffic is unaffected because
production uses a different key.

Where to find it: Same as `SLACK_COMMUNITY_CHANNEL_IDS`, but on the
development workspace.

Prereqs: Same as `SLACK_COMMUNITY_CHANNEL_IDS`.

Rotation: Same as `SLACK_COMMUNITY_CHANNEL_IDS`.

Test vs live: This key is the development counterpart to
`SLACK_COMMUNITY_CHANNEL_IDS`.

## SLACK_DEV_ANNOUNCEMENTS_CHANNEL_ID

Purpose: Development-only announcements channel. Same shape as
`SLACK_ANNOUNCEMENTS_CHANNEL_ID`. Used only when `SLACK_ENVIRONMENT=development`.

Without it (in development mode): Announcements are silent on the
development workspace. Production is unaffected.

Where to find it: Same as `SLACK_ANNOUNCEMENTS_CHANNEL_ID`, but on the
development workspace.

Prereqs: Same as `SLACK_ANNOUNCEMENTS_CHANNEL_ID`.

Rotation: Same as `SLACK_ANNOUNCEMENTS_CHANNEL_ID`.

Test vs live: This key is the development counterpart to
`SLACK_ANNOUNCEMENTS_CHANNEL_ID`.

## SLACK_TEST_COMMUNITY_CHANNEL_IDS

Purpose: Test-only community channel IDs. Used only when
`SLACK_ENVIRONMENT=test`. Typical use: integration tests routed through
a sandboxed channel (e.g. `#integration-tests`) that has no real users.

Without it (in test mode): Tests that exercise community-signal capture
have no channels to observe — the test will see empty results.

Where to find it: Same as `SLACK_COMMUNITY_CHANNEL_IDS`, but on the
test workspace (often the same workspace as development, with a
distinct set of channels).

Prereqs: Same as `SLACK_COMMUNITY_CHANNEL_IDS`.

Rotation: Same as `SLACK_COMMUNITY_CHANNEL_IDS`.

Test vs live: This key is the test counterpart to
`SLACK_COMMUNITY_CHANNEL_IDS`.

## SLACK_TEST_ANNOUNCEMENTS_CHANNEL_ID

Purpose: Test-only announcements channel ID, e.g. `#integration-tests`.
Used only when `SLACK_ENVIRONMENT=test`.

Without it (in test mode): Announcement integration tests have no
channel to assert against.

Where to find it: Same as `SLACK_ANNOUNCEMENTS_CHANNEL_ID`, but on the
test workspace / channel.

Prereqs: Same as `SLACK_ANNOUNCEMENTS_CHANNEL_ID`.

Rotation: Same as `SLACK_ANNOUNCEMENTS_CHANNEL_ID`.

Test vs live: This key is the test counterpart to
`SLACK_ANNOUNCEMENTS_CHANNEL_ID`.

## SLACK_PLAN_SPRINTS_CHANNEL_ID

Purpose: Production channel ID of `#plan-sprints` — the channel where
members post their sprint progress updates. Read by
`community/slack_config.py:get_slack_plan_sprints_channel_id` and consumed
by the daily ingest `crm/tasks/ingest_plan_sprints.py` (issues
#889/#890/#891). The ingest calls `conversations.history` to enumerate
threads and `conversations.replies` to pull each thread's replies, then
matches authors to members and auto-applies parsed progress to their
active-sprint plan. Used only when `SLACK_ENVIRONMENT=production`.

Without it (blank): The ingest logs "no #plan-sprints channel configured"
and returns without creating any rows. No sprint updates are captured;
everything else on the platform is unaffected.

Where to find it: Right click `#plan-sprints` in Slack > "View channel
details" > copy the ID at the bottom (looks like `C01ABC234`).

Prereqs:
- `SLACK_ENABLED` must be true and `SLACK_BOT_TOKEN` set.
- `SLACK_PLAN_SPRINTS_USER_TOKEN` must be set so public/private channel
  threads can be read with `conversations.replies`.
- The bot token must hold `channels:history` (public channel) or
  `groups:history` + `groups:read` (private channel). Without it the
  history calls fail with `missing_scope`.
- The bot must be a member of `#plan-sprints`. Invite it from inside the
  channel with `/invite @<bot-name>` — otherwise the history reads return
  `not_in_channel`.

Rotation: Permanent for the channel. Replace the value when you cut over
to a new `#plan-sprints` channel.

Test vs live: This key is the live (production) channel. Use
`SLACK_DEV_PLAN_SPRINTS_CHANNEL_ID` for development and
`SLACK_TEST_PLAN_SPRINTS_CHANNEL_ID` for test.

## SLACK_PLAN_SPRINTS_USER_TOKEN

Purpose: Secret User OAuth token (`xoxp-...`) used only for
`conversations.replies` on public/private `#plan-sprints` threads. Slack's
channel-thread API does not accept the bot token for this operation. History
discovery, profile lookups, and all outbound calls continue to use
`SLACK_BOT_TOKEN`.

Without it: The API trigger returns `409 ingest_unavailable`; a scheduled run
records a terminal error instead of silently persisting only roots.

Where to find it: Slack app > OAuth & Permissions > OAuth Tokens for Your
Workspace > User OAuth Token. Store it through Studio integration settings;
never put it in source or logs.

Prereqs: The installing user must be able to read `#plan-sprints`; the token
needs `channels:history` for a public channel or `groups:history` for a private
channel.

Rotation: Rotate/reinstall in Slack, then replace this secret in Studio before
the next daily run.

Test vs live: Use a credential from the workspace selected by
`SLACK_ENVIRONMENT`. Never reuse a production token in local tests.

## PLAN_SPRINTS_THREAD_REFRESH_DAYS

Purpose: Bounds how far back the daily ingest revisits unmatched/completed
thread roots for late replies. Threads linked to an active sprint are always
revisited. Defaults to 45 days for the bounded remainder.

Without it: The 45-day default applies.

Where to find it: Studio integration settings. Set a positive integer.

Prereqs: `SLACK_PLAN_SPRINTS_USER_TOKEN` must be configured.

Rotation: Safe to change; the next run uses the new window.

Test vs live: Configure independently in each deployment.

## PLAN_SPRINTS_INGEST_LEASE_MINUTES

Purpose: Maximum duration of the per-channel ingest lease. Defaults to 60
minutes; expired running rows are terminalized before a replacement starts.

Without it: The 60-minute default applies.

Where to find it: Studio integration settings. Set a positive integer.

Prereqs: None.

Rotation: Safe to change between runs.

Test vs live: Configure independently in each deployment.

## PLAN_SPRINTS_RAW_TEXT_RETENTION_DAYS

Purpose: Maximum local retention for raw Slack message text. The daily 03:40
UTC task blanks older text and rebuilds canonical CRM notes without it.
Defaults to 365 days.

Without it: The 365-day default applies.

Where to find it: Studio integration settings. Set a positive integer.

Prereqs: None.

Rotation: Lowering the value takes effect on the next purge and is not
reversible from local data.

Test vs live: Configure independently in each deployment.

## SLACK_DEV_PLAN_SPRINTS_CHANNEL_ID

Purpose: Development-only `#plan-sprints` channel ID. Same shape and
consumers as `SLACK_PLAN_SPRINTS_CHANNEL_ID`. Used only when
`SLACK_ENVIRONMENT=development`.

Without it (in development mode): The ingest no-ops on the development
workspace. Production is unaffected because it uses a different key.

Where to find it: Same as `SLACK_PLAN_SPRINTS_CHANNEL_ID`, but on the
development workspace.

Prereqs: Same as `SLACK_PLAN_SPRINTS_CHANNEL_ID`.

Rotation: Same as `SLACK_PLAN_SPRINTS_CHANNEL_ID`.

Test vs live: This key is the development counterpart to
`SLACK_PLAN_SPRINTS_CHANNEL_ID`.

## SLACK_TEST_PLAN_SPRINTS_CHANNEL_ID

Purpose: Test-only `#plan-sprints` channel ID. Same shape and consumers
as `SLACK_PLAN_SPRINTS_CHANNEL_ID`. Used only when `SLACK_ENVIRONMENT=test`.

Without it (in test mode): Tests that exercise the `#plan-sprints` ingest
have no channel to read — the run sees empty results.

Where to find it: Same as `SLACK_PLAN_SPRINTS_CHANNEL_ID`, but on the test
workspace / channel.

Prereqs: Same as `SLACK_PLAN_SPRINTS_CHANNEL_ID`.

Rotation: Same as `SLACK_PLAN_SPRINTS_CHANNEL_ID`.

Test vs live: This key is the test counterpart to
`SLACK_PLAN_SPRINTS_CHANNEL_ID`.

## SLACK_TEAM_REQUESTS_CHANNEL_ID

Purpose: Production channel ID of the team-requests channel — where staff
notifications land for two member-initiated flows. Read by
`community/slack_config.py:get_slack_team_requests_channel_id` and consumed by:

- the plan-request ping ("Ask the team to plan with me", issue #585) in
  `plans/views/sprints.py`, and
- the onboarding-submitted staff heads-up (issue #882) in
  `crm/services/onboarding_notify.py`.

Used only when `SLACK_ENVIRONMENT=production`.

Without it (blank): The Slack post is skipped — only that side of the
notification is suppressed. The email and in-app notifications still run, so
staff are still notified through those channels. No error is raised.

Where to find it: Right click the team-requests channel in Slack > "View
channel details" > copy the ID at the bottom (looks like `C01ABC234`).

Prereqs:
- `SLACK_ENABLED` must be true and `SLACK_BOT_TOKEN` set.
- The bot must be a member of the team-requests channel — invite it from
  inside the channel with `/invite @<bot-name>`, otherwise the post fails
  with `not_in_channel`.

Rotation: Permanent for the channel. Replace the value when you cut over to a
new team-requests channel.

Test vs live: This key is the live (production) channel. Use
`SLACK_DEV_TEAM_REQUESTS_CHANNEL_ID` for development and
`SLACK_TEST_TEAM_REQUESTS_CHANNEL_ID` for test.

## SLACK_DEV_TEAM_REQUESTS_CHANNEL_ID

Purpose: Development-only team-requests channel ID. Same shape and consumers
as `SLACK_TEAM_REQUESTS_CHANNEL_ID` (plan-request ping #585 and onboarding
heads-up #882). Used only when `SLACK_ENVIRONMENT=development`.

Without it (in development mode): The Slack post no-ops on the development
workspace; email + in-app notifications still run. Production is unaffected
because it uses a different key.

Where to find it: Same as `SLACK_TEAM_REQUESTS_CHANNEL_ID`, but on the
development workspace.

Prereqs: Same as `SLACK_TEAM_REQUESTS_CHANNEL_ID`.

Rotation: Same as `SLACK_TEAM_REQUESTS_CHANNEL_ID`.

Test vs live: This key is the development counterpart to
`SLACK_TEAM_REQUESTS_CHANNEL_ID`.

## SLACK_TEST_TEAM_REQUESTS_CHANNEL_ID

Purpose: Test-only team-requests channel ID. Same shape and consumers as
`SLACK_TEAM_REQUESTS_CHANNEL_ID` (plan-request ping #585 and onboarding
heads-up #882). Used only when `SLACK_ENVIRONMENT=test`.

Without it (in test mode): The Slack post no-ops; email + in-app
notifications still run.

Where to find it: Same as `SLACK_TEAM_REQUESTS_CHANNEL_ID`, but on the test
workspace / channel.

Prereqs: Same as `SLACK_TEAM_REQUESTS_CHANNEL_ID`.

Rotation: Same as `SLACK_TEAM_REQUESTS_CHANNEL_ID`.

Test vs live: This key is the test counterpart to
`SLACK_TEAM_REQUESTS_CHANNEL_ID`.

## SLACK_INVITE_URL

Purpose: Public Slack workspace invite URL (the `https://join.slack.com/t/...`
link). Shown to Main+ members on the dashboard so they can self-serve
into the community workspace. Used by
`community/services/slack.py:join_slack_via_email` and
`community/tasks/hooks.py` as a fallback when the API-driven invite is
not available.

Without it: The "Join Slack" CTA is hidden on the dashboard. The
API-driven flow (where it exists) still works; only the public
fallback link is missing. Some emails downgrade to "log in to join"
instructions.

Where to find it:
- Slack admin > "Manage members" > "Invitations" > "Get invite link"
  (the entry sits under the workspace admin menu).
- Direct link, replacing `<workspace>` with your subdomain:

  ```
  https://<workspace>.slack.com/admin/invites
  ```

- Click "Create new link" to generate a fresh shareable URL. Slack
  allows expiry and max-use limits; set them to "never" / "unlimited"
  for an evergreen invite, or shorter for time-boxed cohorts.

Prereqs: You must be a Slack workspace admin. Restricted workspaces
may have invitations disabled — re-enable in workspace settings.

Rotation: Safe and easy.

1. Slack > workspace admin > "Invitations" > revoke the old link.
2. Create a new link with the desired expiry / max uses.
3. Update this setting via Studio (Integration settings > Slack >
   `SLACK_INVITE_URL`) or via `POST /api/integrations/settings`.
4. Old links stop working at the moment you revoke — users who click a
   revoked link see Slack's "invite expired" page.

Test vs live: n/a. The invite URL is workspace-scoped. Use a separate
workspace for development/test if you want to keep them isolated.

## SLACK_TEAM_ID

Purpose: Workspace team ID (e.g. `T01ABC123`). Used to build deep links
from Studio into a member's Slack profile (the
`https://app.slack.com/team/<TEAM_ID>/user/<USER_ID>` shape). Read by
the Studio templates when rendering the Slack icon next to a user.

Without it: The Slack icon next to a user in Studio still renders, but
is not clickable. All other Slack functionality is unaffected — this
value is purely for outbound link construction.

Where to find it:
- Slack workspace menu > "Settings & administration" > "Workspace
  settings". The team ID appears in the URL bar as `T01ABC123`.
- Or open any Slack URL and look for `/team/<TEAM_ID>/` in the path.

Prereqs: None beyond having a Slack workspace.

Rotation: The team ID is permanent for the lifetime of the workspace.
There is no rotation. Replace once if your organisation migrates to a
new workspace.

Test vs live: n/a. Each workspace has its own team ID; pin it to the
environment that uses that workspace.

## STAFF_SIGNUP_NOTIFY_CHANNEL_ID

Purpose: Single Slack channel ID where the bot posts an internal
heads-up every time a paid signup completes (Basic and above). Read
by `community/services/staff_notifications.py::notify_paid_signup`.
Separate from `SLACK_ANNOUNCEMENTS_CHANNEL_ID` — that channel is
public/member-visible content announcements; this one is the
founder-only signup feed.

Without it (blank): The Slack post is skipped silently. The email
sides of the heads-up (welcome to user, internal email to staff)
still fire if their setting is configured.

Where to find it: Right click the channel in Slack > "View channel
details" > copy the ID at the bottom. Pick a private channel only
the founders are in — the post contains the new user's email and
Stripe customer link.

Prereqs:
- `SLACK_ENABLED` must be true.
- `SLACK_BOT_TOKEN` must be set.
- The bot must be a member of the channel.

Rotation: Permanent for the channel. Replace the value when you cut
over to a new founder-feed channel.

Test vs live: Single key in v1 — no dev/test channel splits. If you
need a non-production channel, point this key at the staging channel
in the staging environment's settings.

## STAFF_SLACK_JOIN_NOTIFY_ENABLED

Purpose: Boolean toggle that enables the staff heads-up (email +
optional Slack post) fired when the periodic membership refresh
observes a known user genuinely transition from "not a Slack member"
to "Slack member". Read by
`community/services/staff_notifications.py::notify_slack_join`, invoked
from `community/tasks/slack_membership.py::refresh_slack_membership`.
Recommended ON. Acts as a no-redeploy kill switch.

Delivery reuses the existing paid-signup keys — no new recipient key:
- Email recipient: `STAFF_SIGNUP_NOTIFY_EMAIL` (skipped when blank).
- Slack post channel: `STAFF_SIGNUP_NOTIFY_CHANNEL_ID` (skipped when
  blank or when Slack is disabled).

Without it (off): No join email and no join Slack post are produced;
the membership refresh still updates `slack_member` as normal.

Backfill safety: The notification fires only on a forward transition
observed on a PRIOR cycle (`slack_checked_at` already set,
`slack_member` was False) — the first-ever observation of a user
already in Slack seeds `slack_member` silently and never notifies, so
the first sync after deploy cannot email-blast existing members.

Prereqs for the Slack post side (the email side has none beyond the
recipient key):
- `SLACK_ENABLED` must be true.
- `SLACK_BOT_TOKEN` must be set.
- The bot must be a member of `STAFF_SIGNUP_NOTIFY_CHANNEL_ID`.
