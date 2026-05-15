# Zoom integration setup

This page documents every Zoom-related setting registered in
`integrations/settings_registry.py` (the `zoom` group). Each section
follows the same template — Purpose, Without it, Where to find it,
Prereqs, Rotation, Test vs live — so an operator can answer "do I need
to set this right now, or can I defer it?" without leaving the page.

The platform talks to Zoom through a Server-to-Server OAuth app. There
is no per-user OAuth dance: one app authenticates as the workspace
account and creates meetings on behalf of the host. Direct deep-link
URLs are intentionally written in code blocks so they do not render as
clickable links. Copy them into the browser.

## ZOOM_CLIENT_ID

Purpose: OAuth client ID for the Zoom Server-to-Server (S2S) app. Paired
with `ZOOM_CLIENT_SECRET` and `ZOOM_ACCOUNT_ID` to mint a workspace-wide
access token in `integrations/services/zoom.py:get_access_token`. Every
Zoom call the platform makes — creating a meeting from Studio
(`event_create_zoom`), fetching cloud-recording assets in
`jobs/tasks/recording_upload.py`, looking up the meeting host — depends
on that token.

Without it: `get_access_token` raises `ZoomAPIError` immediately, so the
"Create Zoom meeting" action on a Studio event fails before any HTTP
call leaves the box. Existing events that already have a `join_url`
keep working (Zoom hosts them, the platform just stores the URL), but
no new meetings can be provisioned and post-event recording pulls stop.

Where to find it:

- Direct link to the Marketplace app list:

  ```
  https://marketplace.zoom.us/user/build
  ```

- Open your Server-to-Server OAuth app, then the "App credentials" tab.
- Copy the "Client ID" string. It is a short opaque alphanumeric, not a
  UUID.

Prereqs: You must own (or have admin access to) a Zoom Pro account or
better — Basic accounts cannot host the Server-to-Server OAuth app type.
The app must be activated (green "App is activated on your account"
banner) before the credentials accept token requests.

Rotation: The client ID is permanent for the lifetime of the app. Zoom
does not expose a rotation control for it. Treat a leaked client ID
together with its secret as cause to rotate the secret (see
`ZOOM_CLIENT_SECRET` below); rotating the ID itself requires creating a
new S2S app and switching all three Zoom keys at once.

Test vs live: n/a. Zoom does not have a sandbox mode for S2S apps —
there is one app, one set of credentials, one billing relationship.
For a non-prod environment, create a separate S2S app under a different
Zoom workspace (e.g. the development workspace) and point its
credentials at `ZOOM_CLIENT_ID` / `ZOOM_CLIENT_SECRET` / `ZOOM_ACCOUNT_ID`
in that environment.

## ZOOM_CLIENT_SECRET

Purpose: OAuth client secret paired with `ZOOM_CLIENT_ID`. Sent as HTTP
basic auth to `https://zoom.us/oauth/token` in
`integrations/services/zoom.py:get_access_token` to exchange the
account credentials grant for a short-lived (1 hour) access token. The
platform caches the resulting token in-process for ~55 minutes and
refreshes on demand.

Without it: Same failure mode as a missing `ZOOM_CLIENT_ID` — token
minting fails with `ZoomAPIError`, and every downstream Zoom action
(meeting creation, recording fetch) reports the same configuration
error before contacting Zoom.

Where to find it:

- Direct link to the Marketplace app list:

  ```
  https://marketplace.zoom.us/user/build
  ```

- Open your Server-to-Server OAuth app, then the "App credentials" tab.
- Click "View Client Secret" and copy the revealed string.

Prereqs: Same as `ZOOM_CLIENT_ID` — the parent S2S OAuth app must be
created and activated on a Pro-or-higher Zoom workspace.

Rotation: Safe to rotate.

1. In the Marketplace app, click "Regenerate" next to the client secret.
   Zoom shows the new value once. Copy it.
2. Update this setting via Studio (Integration settings > Zoom >
   `ZOOM_CLIENT_SECRET`) or via `POST /api/integrations/settings`.
3. Call `clear_token_cache()` (happens automatically on the next failed
   token fetch) or wait up to 60 minutes for the cached token to expire.
4. Window of impact: between the moment Zoom regenerates the secret and
   you save the new value here, in-flight access-token refreshes return
   `invalid_client`. Cached tokens (up to ~55 minutes old) continue to
   work in the meantime.

Test vs live: n/a. Use a different Zoom workspace for non-prod and keep
each workspace's secret pinned to its own environment.

## ZOOM_ACCOUNT_ID

Purpose: UUID of the Zoom account the S2S OAuth app belongs to. Sent as
the `account_id` query parameter on the token-grant request (the
`account_credentials` grant type Zoom defines for S2S apps). Without it
Zoom cannot resolve which workspace the credentials authenticate
against, so the entire token exchange fails.

Without it: Same as the other two credentials — `get_access_token`
raises `ZoomAPIError` and every Zoom-touching code path fails fast.

Where to find it:

- Direct link to the Marketplace app list:

  ```
  https://marketplace.zoom.us/user/build
  ```

- Open your Server-to-Server OAuth app, then the "App credentials" tab.
- Copy the "Account ID" string (looks like a long alphanumeric, ~20-22
  chars). It is also visible at:

  ```
  https://zoom.us/account
  ```

  under "Account Profile" > "Account ID".

Prereqs: A Zoom Pro-or-higher account with the S2S OAuth app installed
on it.

Rotation: The account ID is permanent for the lifetime of the Zoom
workspace. If your organisation migrates to a new Zoom account, you
will also be re-creating the S2S OAuth app and rotating all three
`ZOOM_*` values together.

Test vs live: n/a. Each Zoom workspace has its own account ID; pair it
with the matching client ID/secret.

## ZOOM_WEBHOOK_SECRET_TOKEN

Purpose: Zoom-issued secret used to verify webhook delivery signatures.
`integrations/services/zoom.py:validate_webhook_signature` computes
`HMAC-SHA256(secret, "v0:{timestamp}:{request_body}")` and compares the
result to the `x-zm-signature` header on every inbound webhook. The
view at `integrations/views/zoom_webhook.py` rejects any request that
does not verify, so without a correct value the platform cannot react
to `meeting.started`, `recording.completed`, or other Zoom events.

Without it: `validate_webhook_signature` logs
`ZOOM_WEBHOOK_SECRET_TOKEN not configured` and returns False, so the
webhook endpoint returns 401 for every Zoom delivery. Side effects:
- Recording-ready signals never reach the platform, so the
  `jobs/tasks/recording_upload.py` chain (pull from Zoom cloud → push
  to S3 → upload to YouTube) does not run automatically. You can still
  run the pipeline manually.
- "Meeting started" UI cues (e.g. "Join now" highlights) lose their
  real-time signal and fall back to scheduled-time heuristics.

Where to find it:

- Direct link:

  ```
  https://marketplace.zoom.us/user/build
  ```

- Open your Server-to-Server OAuth app, then the "Feature" tab.
- Toggle "Event Subscriptions" on if not already enabled.
- Each event subscription shows a "Secret Token" field with a "Copy"
  button. Copy that value.

Prereqs: You must add the platform's webhook endpoint to the
subscription:

- Endpoint URL: `https://<host>/api/webhooks/zoom`
  (e.g. `https://aishippinglabs.com/api/webhooks/zoom` in production).
- Subscribed event types: at minimum `meeting.started` and
  `recording.completed`. Other events are accepted but ignored by the
  current handlers — don't subscribe speculatively.
- Validation step: Zoom requires you to respond to a one-time URL
  validation challenge before the subscription activates. The webhook
  view handles this automatically — submit the endpoint, click
  "Validate", and Zoom should show a green check.

Rotation: Safe to rotate.

1. In the Marketplace app, click "Regenerate" next to the secret token.
   Zoom shows the new value once. Copy it.
2. Update this setting via Studio (Integration settings > Zoom >
   `ZOOM_WEBHOOK_SECRET_TOKEN`) or via `POST /api/integrations/settings`.
3. Window of impact: between the moment Zoom regenerates the secret
   and you save it here, signature validation fails and webhooks return
   401. Zoom retries failed deliveries automatically, so transient
   misses self-heal once the new value is in place.

Test vs live: n/a. Zoom does not separate test and live deliveries —
each event subscription has one secret. Use a separate event
subscription (or a separate S2S app on a development workspace) for
non-prod traffic.
