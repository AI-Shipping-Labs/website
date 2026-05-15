# YouTube integration setup

This page documents every YouTube-related setting registered in
`integrations/settings_registry.py` (the `youtube` group). Each
section follows the same template — Purpose, Without it, Where to find
it, Prereqs, Rotation, Test vs live.

The platform uploads event recordings to YouTube using a one-time
OAuth consent flow. After consent, the long-lived refresh token mints
short-lived access tokens at request time
(`integrations/services/youtube.py:get_access_token`). There is no
periodic user-facing OAuth dance — the refresh token is the stable
credential.

Direct deep-link URLs are intentionally written in code blocks so they
do not render as clickable links. Copy them into the browser.

## YOUTUBE_CLIENT_ID

Purpose: OAuth client ID for the Google Cloud project that owns the
YouTube Data API quota. Read by
`integrations/services/youtube.py:56` and sent as `client_id` on
every token-refresh POST to `https://oauth2.googleapis.com/token`.

Without it: `get_access_token` raises `YouTubeAPIError` with
`YouTube OAuth credentials not configured. Set YOUTUBE_CLIENT_ID,
YOUTUBE_CLIENT_SECRET, and YOUTUBE_REFRESH_TOKEN.`. Recording uploads
to YouTube fail; recordings still land in S3 but are not published.

Where to find it:

- Google Cloud Console > "APIs & Services" > "Credentials" > pick the
  OAuth 2.0 Client ID row for the platform.
- Direct link, replacing `<project-id>`:

  ```
  https://console.cloud.google.com/apis/credentials?project=<project-id>
  ```

- The client ID looks like `<digits>-<random>.apps.googleusercontent.com`.
- Make sure the "Application type" is "Web application" so the
  refresh token flow returns a long-lived token.

Prereqs:
- A Google Cloud project with the "YouTube Data API v3" enabled:

  ```
  https://console.cloud.google.com/apis/library/youtube.googleapis.com
  ```

- The project's OAuth consent screen must be configured (App name,
  support email, scopes including `https://www.googleapis.com/auth/youtube.upload`).
- If the consent screen is in "Testing" mode, only listed test users
  can authorise — publish it to "In production" for general use.

Rotation: The client ID is stable for the lifetime of the OAuth
client. Rotation is rare; if compromised, delete the OAuth client and
create a new one — that also invalidates `YOUTUBE_REFRESH_TOKEN`, so
all three values must be re-issued together.

Test vs live: n/a. Google does not have a sandbox; use a different
Google Cloud project (or YouTube channel) for non-prod environments
and store its credentials in the environment-specific overrides.

## YOUTUBE_CLIENT_SECRET

Purpose: OAuth client secret paired with `YOUTUBE_CLIENT_ID`. Sent as
`client_secret` on every refresh-token POST. Read by
`integrations/services/youtube.py:57`.

Without it: Same failure mode as missing client id —
`YouTubeAPIError` immediately, no uploads.

Where to find it:

- Google Cloud Console > "APIs & Services" > "Credentials" > click
  the OAuth 2.0 Client ID > the page shows the client secret. There
  is a "Reset secret" button to rotate.
- Direct link:

  ```
  https://console.cloud.google.com/apis/credentials
  ```

Prereqs: Same as `YOUTUBE_CLIENT_ID`.

Rotation: Safe to rotate.

1. Google Cloud Console > "Credentials" > the OAuth client > "Reset
   secret". Google shows the new secret once.
2. Update this setting via Studio (Integration settings > YouTube >
   `YOUTUBE_CLIENT_SECRET`).
3. The refresh token remains valid — Google ties refresh tokens to
   the client id, not the secret, so existing refresh tokens
   continue to mint access tokens with the new secret.
4. Window of impact: between rotation and save, refresh requests
   return `invalid_client`. Cached access tokens (up to 1 hour old)
   continue to work in the meantime.

Test vs live: n/a — same as `YOUTUBE_CLIENT_ID`.

## YOUTUBE_REFRESH_TOKEN

Purpose: Long-lived OAuth refresh token authorising uploads to the
target YouTube channel. Read by
`integrations/services/youtube.py:58` and exchanged for a short-lived
access token on each upload. The refresh token never expires unless
the user revokes it or Google force-rotates it for abuse.

Without it: Same hard failure as missing client id/secret.

Where to find it: Generated once via the YouTube OAuth flow. The
recommended path:

1. From a fresh browser session, visit:

   ```
   https://accounts.google.com/o/oauth2/v2/auth?response_type=code&client_id=<YOUTUBE_CLIENT_ID>&redirect_uri=urn:ietf:wg:oauth:2.0:oob&scope=https://www.googleapis.com/auth/youtube.upload&access_type=offline&prompt=consent
   ```

   Replace `<YOUTUBE_CLIENT_ID>` with the value of that setting. The
   redirect URI `urn:ietf:wg:oauth:2.0:oob` is Google's "copy code
   manually" mode and must be added as an authorised redirect URI on
   the OAuth client.

2. Authenticate as the YouTube channel owner. Grant the requested
   scope.

3. Google shows a one-time authorisation code. Exchange it for a
   refresh token via:

   ```
   curl -s -X POST https://oauth2.googleapis.com/token \
     -d code=<CODE> \
     -d client_id=<YOUTUBE_CLIENT_ID> \
     -d client_secret=<YOUTUBE_CLIENT_SECRET> \
     -d redirect_uri=urn:ietf:wg:oauth:2.0:oob \
     -d grant_type=authorization_code
   ```

   The response includes `refresh_token` — copy that value into Studio
   as `YOUTUBE_REFRESH_TOKEN`.

   The `prompt=consent` query parameter is critical: without it,
   Google omits `refresh_token` on subsequent authorisations for the
   same user, and you only get an access token.

Prereqs:
- The authenticating Google account must own (or have manager-role
  access to) the target YouTube channel.
- The OAuth client must have `https://www.googleapis.com/auth/youtube.upload`
  added on the consent screen.
- The channel must be a YouTube channel (not just a Google account
  without a channel). Visit `https://youtube.com/account` to confirm
  there is a channel attached.

Rotation: Refresh tokens are long-lived. Google may invalidate them in
these cases:
- The user revokes app access at `https://myaccount.google.com/permissions`.
- Google detects suspicious activity.
- The token is unused for 6+ months (only applies to consent screens
  in "Testing" mode — published apps are not subject to this).

To rotate: re-run the consent flow above with `prompt=consent` and
save the new value.

Test vs live: Use a separate YouTube channel (and a separate OAuth
client in a separate Google Cloud project) for non-prod environments.
Refresh tokens are scoped to a single OAuth client, so mixing
environments is not possible with one set of credentials.
