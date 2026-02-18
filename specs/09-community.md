# 09 - Community

## Overview

Slack workspace for Main and Premium members. Fully automated lifecycle: invite on purchase, deactivate on cancellation, reactivate on re-subscribe.

## Slack Integration

Uses Slack Web API (requires a Slack app with `admin.users:write`, `users:read`, `users:read.email` scopes on an Enterprise Grid or Business+ plan for user management, or `conversations:invite` for channel-based approach).

### Approach A: Full workspace management (requires Business+ plan)

- Invite: `admin.users.invite` with email
- Deactivate: `admin.users.remove` or `users.admin.setInactive`
- Reactivate: `admin.users.invite` again (re-sends invite to same email)

### Approach B: Channel-based (works on any paid Slack plan)

- All community content happens in private channels
- Invite: bot adds user to community channels via `conversations.invite`
- Deactivate: bot removes user from community channels via `conversations.kick`
- Reactivate: bot re-adds user to community channels
- Requires: user must be in the Slack workspace first (manual one-time join, or via shared invite link)

Pick Approach B for MVP (less restrictive Slack plan requirement). Store a list of community channel IDs in config.

## Data Model

```
User (community-related fields):
  slack_user_id: string | null    # set after user joins Slack and is matched by email
  community_access: bool          # derived: true if tier.level >= 2
```

```
Config:
  SLACK_BOT_TOKEN: string
  SLACK_COMMUNITY_CHANNEL_IDS: string[]   # list of private channel IDs for community
```

## Lifecycle Events

### On Purchase (Main or Premium)

Triggered by: payment webhook (spec 02) when `user.tier.level` changes to >= 2.

1. Look up Slack user by email: `GET /api/users.lookupByEmail?email={user.email}`
2. If found: store `slack_user_id` on user record. For each channel in `SLACK_COMMUNITY_CHANNEL_IDS`, call `conversations.invite(channel, user_slack_id)`.
3. If not found: send the user an email with:
   - Subject: "Welcome to AI Shipping Labs community!"
   - Body: Slack workspace invite link + instructions to join. Once they join, a background job matches their email and adds them to channels.
4. Log the action: `community_action: "invited", user_id, timestamp`

### On Cancellation / Downgrade Below Main

Triggered by: payment webhook when `user.tier.level` drops below 2, effective at `billing_period_end`.

1. Schedule a job for `billing_period_end` datetime
2. When job runs: for each channel in `SLACK_COMMUNITY_CHANNEL_IDS`, call `conversations.kick(channel, user_slack_id)`
3. Send email: "Your community access has ended. Recordings and content remain available based on your current plan. Re-subscribe anytime to rejoin."
4. Log: `community_action: "removed", user_id, timestamp`

### On Re-subscribe (to Main or Premium)

Triggered by: payment webhook when `user.tier.level` changes back to >= 2.

1. If `slack_user_id` is set: re-add to all community channels via `conversations.invite`
2. If `slack_user_id` is null: same as new purchase flow (email with invite link)
3. Send email: "Welcome back! You've been re-added to the community."
4. Log: `community_action: "reactivated", user_id, timestamp`

## Background Job: Email Matcher

Runs every hour (or on Slack `team_join` event webhook):

1. Query all users where `tier.level >= 2` and `slack_user_id IS NULL`
2. For each, call `users.lookupByEmail`
3. If found: set `slack_user_id`, add to community channels

## Requirements

- R-COM-1: Implement a `CommunityService` with methods: `invite(user)`, `remove(user)`, `reactivate(user)`. Each method handles the Slack API calls described above.
- R-COM-2: Call `CommunityService.invite(user)` from the payment webhook handler when tier changes to level >= 2.
- R-COM-3: Schedule `CommunityService.remove(user)` to run at `billing_period_end` when tier drops below level 2.
- R-COM-4: Call `CommunityService.reactivate(user)` from the payment webhook handler when tier changes back to level >= 2.
- R-COM-5: Implement the email matcher background job. Run hourly. Match users by email to Slack user IDs and add unmatched community members to channels.
- R-COM-6: Log all community actions to a `community_audit_log` table: `user_id`, `action` (invited/removed/reactivated), `timestamp`, `details` (JSON with Slack API response).
- R-COM-7: `CommunityService` is behind an interface/abstract class so the Slack implementation can be swapped for a different platform later without changing the callers.
