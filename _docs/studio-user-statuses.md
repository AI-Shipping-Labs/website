# Studio user statuses and membership fields

This page explains every field on the Studio user detail page
(`/studio/users/<id>/`) "Membership & community" card, plus the bounce
status card. It is the reference linked from the card's
"What do these mean?" link.

Each section lists what the field actually means and every value it can
take. Fields are read-only on the detail page unless noted otherwise.

## Tier

The user's effective membership tier — the level that actually gates
access right now. If an active temporary upgrade (override) is in effect,
the effective tier is the override tier, not the user's stored
subscription tier.

Possible values:

- `Free` — no paid subscription. The default for newsletter-only and
  manually created rows.
- `Basic` — the entry paid tier.
- `Main` — the standard paid tier.
- `Premium` — the highest paid tier.

The base (stored) tier and override expiry are shown beneath the pill
when an override is active.

## Tier source (the badge next to the tier pill)

Where the displayed tier comes from. The badge disambiguates a paid
subscriber from a comped account from a manual row.

- `Override` — the tier comes from an active temporary upgrade granted in
  Studio. The badge links to the override history for that user. See
  tier overrides (#562/#587).
- `From Stripe` — the tier comes from a paid Stripe subscription
  (`stripe_customer_id` is set and there is no override on top).
- `Default` — no Stripe subscription and no override. This is the user's
  stored tier, which is what you see for manual, seed, or
  newsletter-only rows.

## Status

The Django login account state, NOT the subscription. A user can be a
paying member and still be `Inactive` here if their login was disabled.

- `Active` — `is_active=True` and not staff; the user can log in.
- `Staff` — `is_staff=True`; a staff/admin account.
- `Inactive` — `is_active=False`; login is disabled.

## Email verified

Whether the user confirmed their email via the verification link.

- `Verified` — `email_verified=True`. Set by the verification-link flow,
  OAuth signups (auto-verified), and account merges.
- `Not verified` — `email_verified=False`; the user never confirmed
  their email.

The exact date of verification is not tracked — there is no
`email_verified_at` column on the model, so only the boolean is shown.
See account activation (#452/#768).

## Source (signup attribution)

How this user row was first created. Captured at signup so operators can
distinguish a real signup from a bulk-imported or pre-existing row. See
signup attribution (#768/#770).

- `Unknown (pre-existing row)` — the row predates signup tracking; the
  source was never recorded.
- `Newsletter subscribe` — created by subscribing to the newsletter.
- `Email + password signup` — created via the email/password signup flow.
- `OAuth signup` — created via an OAuth provider (these are
  auto-verified).
- `Bulk import (Stripe / CSV / course DB)` — created by a bulk import,
  not an interactive signup.
- `Staff-created (Studio)` — created by a staff member in Studio.

## Activated

Whether the user has ever taken a platform action — i.e. whether they are
an engaged member or just a newsletter-only row. See account activation
(#452/#768).

- `Yes` — the user has verified their email, paid, registered for an
  event, completed a course unit, or linked Slack.
- `No` — newsletter-only / never engaged.

## Newsletter

The user's marketing-email subscription state. Unsubscribing does not
delete the account.

- `Subscribed` — `unsubscribed=False`; receives marketing emails.
- `Unsubscribed` — `unsubscribed=True`; opted out of marketing emails but
  keeps their account.

## Slack

Result of the last Slack-workspace membership check
(`refresh_slack_membership`). See Slack membership check (#918/#561).

- `Member` — verified present in the Slack workspace.
- `Not in Slack` — verified absent from the workspace.
- `Never checked` — `slack_checked_at` is null; the user has never been
  probed.

## Slack ID

The user's linked Slack workspace user ID. Set during Slack OAuth.

- A `U…` (or `W…`) ID — the linked Slack workspace user ID. When
  `SLACK_TEAM_ID` is configured the ID deep-links into Slack.
- `Not linked` — no Slack account is connected to this user.

## Bounce state (bounce status card)

The bounce status card only renders when the user has a non-`none`
bounce. `State` reflects the SES delivery status. See bounce handling
(#766).

- `Permanent` — a hard bounce; the address is dead. Permanently-bounced
  users are auto-unsubscribed.
- `Soft` — a transient bounce; delivery may recover.

The card also shows when the bounce was recorded and the raw SES
diagnostic string when available.
