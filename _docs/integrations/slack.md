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
  Token" (the one prefixed `xoxb-`). The "User OAuth Token"
  (`xoxp-...`) is a different credential and will not work — the
  platform talks as the bot user, not as the installer.

Prereqs:
- A Slack app installed to your workspace.
- Bot token scopes that match what the platform calls. At minimum:
  `chat:write`, `chat:write.public`, `channels:read`, `channels:history`,
  `users:read`, `users:read.email`, `reactions:read`, `groups:read`.
  Missing scopes manifest as `missing_scope` errors at the Slack API.
- The bot user must be invited to every channel listed in
  `SLACK_*_COMMUNITY_CHANNEL_IDS` and `SLACK_*_ANNOUNCEMENTS_CHANNEL_ID`,
  or `chat.postMessage` returns `not_in_channel`.

Rotation: Safe to rotate, but requires a re-install in some cases.

1. In the Slack app config, click "OAuth & Permissions" > "Reinstall to
   Workspace" if you've changed scopes; otherwise click "Rotate Token"
   under the bot token row. Slack shows the new `xoxb-...` once.
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

## SLACK_ANNOUNCEMENTS_CHANNEL_NAME

Purpose: Human-readable display name of the announcements channel
(e.g. `#announcements`). Used in UI copy only — for example, "New
events go to #announcements" on internal Studio screens. Has no effect
on routing; routing is driven entirely by the `*_CHANNEL_ID` keys.

Without it: UI copy that interpolates the channel name renders an
empty string. Posting still works because routing uses the channel ID,
not the name.

Where to find it: It is whatever you call the channel in Slack —
include the leading `#`. There is no Slack API or dashboard step.

Prereqs: None. Cosmetic only.

Rotation: Update whenever you rename the announcements channel in
Slack. There is no automation that picks the new name up.

Test vs live: n/a. One name, used in copy across environments. If the
test workspace uses a different channel name, you can override per
environment via the usual `IntegrationSetting` per-env precedence.

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
