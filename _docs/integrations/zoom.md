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

## Server-to-Server OAuth app setup

### Account and role prerequisites

- Create the app in the Zoom account that owns the event meetings. The app
  owner must be the Zoom account owner/admin, or have a custom role with
  **User Management → Roles → Role Settings → Advanced features →
  Server-to-Server OAuth app** View/Edit permission. The custom role must also
  include the account permission corresponding to every requested admin scope
  below. Zoom removes admin scopes if the app owner's role later loses a
  matching permission; transfer ownership to an authorized admin before
  changing that role.
- The meeting host must have Zoom Meetings scheduling/hosting access. Cloud
  recording additionally requires a paid account, a licensed host, available
  cloud-recording storage, and Cloud Recording enabled at the account/group or
  user level.
- For recording metadata, passcodes, download links, and file content, the app
  owner's role must include the **View recording content** privilege. Zoom's
  API privilege identifier is `RecordingContent:Read`; it permits viewing,
  downloading, playing, and sharing account recording content. Account owners
  have this authority; custom admin roles must be checked explicitly under
  **User Management → Roles → Role Settings**.

### Exact least-privilege granular scopes

Add these six granular **admin** scopes—no broad classic scopes and no scopes
for unused Zoom APIs or ignored webhook event types:

| Granular scope | Current path it enables |
| --- | --- |
| `meeting:write:meeting:admin` | `integrations.services.zoom.create_meeting` calls `POST /v2/users/me/meetings` for a disposable or real event that explicitly requests meeting creation. |
| `meeting:read:meeting:admin` | `GET /v2/meetings/{meeting_id}` provider read-back in the operator smoke/verification step. The application runtime does not otherwise GET meetings. |
| `meeting:update:meeting:admin` | `update_meeting` and `update_meeting_settings` call `PATCH /v2/meetings/{meeting_id}` for event reschedules, settings backfill, and `asl events sync-zoom` retry. |
| `meeting:delete:meeting:admin` | `delete_meeting` calls `DELETE /v2/meetings/{meeting_id}` when a future Zoom-backed event is cancelled. |
| `cloud_recording:read:recording:admin` | Authorizes the account-level `recording.completed` event subscription and delivery to the Zoom webhook. |
| `cloud_recording:read:list_recording_files:admin` | Authorizes recording-file metadata/download access. The webhook supplies `recording_files[].download_url`; `jobs.tasks.recording_upload` obtains an S2S token, downloads the selected MP4, and uploads it to S3. |

These mappings follow the current code. In particular, there is no runtime
meeting-list, participant, registrant, report, or `meeting.started` handler, so
those APIs/events do not justify extra scopes.

### Add, save, and activate the scopes

1. Sign in to the Zoom App Marketplace with the authorized app-owner account.
2. Open **Manage → Created Apps**, then select the Server-to-Server OAuth app
   used by this environment.
3. Open **Scopes → Add Scopes**. Search for and add the six exact granular
   admin scopes in the table above, then select **Done** and save the app.
4. Open **Activation** and activate/reactivate the app. Confirm Zoom reports
   that the app is activated for the account. An inactive app cannot mint a
   usable access token, and deactivation invalidates existing tokens.

New Server-to-Server OAuth apps use granular scopes. Do not substitute the
older umbrella scopes merely because an older app still displays them.

### Configure the recording webhook subscription

OAuth scopes authorize what the app may access; the event subscription tells
Zoom what to deliver. Configure both:

1. In the same app, open **Features/Access → Event Subscriptions**, enable event
   subscriptions, and add a webhook subscription for all users in this account.
2. Set the endpoint to `{SITE_BASE_URL}/api/webhooks/zoom` with no trailing
   slash. It must be a reachable HTTPS URL.
3. Add only **Cloud Recording → All Recordings have completed**
   (`recording.completed`), then save. The endpoint automatically answers
   Zoom's one-time `endpoint.url_validation` challenge.
4. Copy the app's **Secret Token** into `ZOOM_WEBHOOK_SECRET_TOKEN` in
   **Studio → Settings → Zoom**, then validate the endpoint.

`ZOOM_WEBHOOK_SECRET_TOKEN` is an HMAC verification secret, not an OAuth
scope or OAuth access token. The current webhook view ignores every event type
except `recording.completed`; do not subscribe to additional events
speculatively.

### Obtain a fresh token after changing scopes

Saving a scope does **not** add it to an access token that was already issued.
Zoom S2S access tokens live for up to one hour. Each web and worker process has
its own module-local token cache; the platform reuses a token with more than 60
seconds remaining, so operators should treat the cache window as roughly 55
minutes.

After saving scopes and activating/reactivating the app, choose one recovery:

1. Restart **every** process that may call Zoom (all gunicorn/web processes and
   every Django-Q worker) so each process mints a fresh token on its next call.
   In ECS, deploy or force a new deployment of both web and worker services.
2. Or wait up to one hour for all previously issued/cached tokens to expire
   before retrying.

Restarting only the web process is insufficient if a worker may download a
recording. `clear_token_cache()` affects only the Python process in which it is
called; it is not a cross-process operator invalidation mechanism. Never print,
paste, or store an OAuth access token, client secret, webhook secret, or Zoom
join URL in documentation, logs, screenshots, or issue evidence.

## ZOOM_CLIENT_ID

Purpose: OAuth client ID for the Zoom Server-to-Server (S2S) app. Paired
with `ZOOM_CLIENT_SECRET` and `ZOOM_ACCOUNT_ID` to mint a workspace-wide
access token in `integrations/services/zoom.py:get_access_token`. Every
Zoom API call the platform makes—creating, updating, or deleting a meeting and
fetching cloud-recording assets in `jobs/tasks/recording_upload.py`—depends on
that token. Provider read-back in the smoke checklist uses the same token but
prints only an allowlisted schedule summary.

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
3. Restart every web and worker process, or wait up to 60 minutes for every
   process-local cached token to expire.
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
does not verify, so without a correct value the platform cannot process
`recording.completed`.

Without it: `validate_webhook_signature` logs
`ZOOM_WEBHOOK_SECRET_TOKEN not configured` and returns False, so the
webhook endpoint returns 400 for every Zoom delivery. Side effects:
- Recording-ready signals never reach the platform, so the
  `jobs/tasks/recording_upload.py` chain (pull from Zoom cloud → push
  to S3) does not run automatically. You can still run the pipeline
  manually.

Where to find it:

- Direct link:

  ```
  https://marketplace.zoom.us/user/build
  ```

- Open your Server-to-Server OAuth app, then **Features/Access → Event
  Subscriptions**.
- Toggle "Event Subscriptions" on if not already enabled.
- Each event subscription shows a "Secret Token" field with a "Copy"
  button. Copy that value.

Prereqs: You must add the platform's webhook endpoint to the
subscription:

- Endpoint URL: `https://<host>/api/webhooks/zoom`
  (e.g. `https://aishippinglabs.com/api/webhooks/zoom` in production).
- Subscribed event type: only `recording.completed`. Other event types are
  ignored by the current handler—do not subscribe speculatively or add scopes
  for them.
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
   400. Zoom retries failed deliveries automatically, so transient
   misses self-heal once the new value is in place.

Test vs live: n/a. Zoom does not separate test and live deliveries —
each event subscription has one secret. Use a separate event
subscription (or a separate S2S app on a development workspace) for
non-prod traffic.

## ZOOM_WEBHOOK_TOLERANCE_SECONDS

Purpose: Maximum accepted age or future clock skew for a signed Zoom webhook,
in Unix seconds. The default is `300` (five minutes). Signature verification
accepts timestamps exactly at either boundary and rejects timestamps one
second beyond it, even when the HMAC is otherwise correct. This limits replay
of a captured delivery while allowing ordinary provider and server clock skew.

Type: positive integer seconds. Default: `300`. Invalid, zero, or negative
overrides fall back to `300`; they never disable freshness validation. Webhook
timestamps must be canonical ASCII decimal Unix seconds in the non-negative
signed 64-bit range; malformed, padded, or oversized headers are rejected
before integer conversion. The setting is non-secret and can be changed in
**Studio → Settings → Zoom** without a deploy.

Where it applies: every request to `/api/webhooks/zoom`, including
`endpoint.url_validation` and `recording.completed`. A request outside the
window is rejected before JSON parsing, `WebhookLog` creation, event mutation,
or recording-upload task enqueueing. The HMAC still covers the exact timestamp
header and raw request body and is compared with a timing-safe comparison.

Operational guidance: keep web-server clocks synchronized. Increase the
window only to accommodate a measured clock-skew problem; a larger value also
increases how long a captured signed delivery remains replayable.

## ZOOM_WAITING_ROOM

Purpose: Boolean. Off by default. When true, every meeting created or
patched by `integrations/services/zoom.py` places attendees in a Zoom
waiting room until the host admits them. A waiting room requires the host
to manually admit each attendee, which is why it is off by default —
keeping `ZOOM_JOIN_BEFORE_HOST` off (see below) already makes cloud
recording wait for the host without any manual admitting (issue #1004).
The key stays configurable for operators who explicitly want a waiting
room.

Type: boolean (`true` / `false`). Default: `false`.

Where it applies:

- New meetings: `create_meeting` reads this when building the meeting
  `settings`.
- Existing upcoming meetings: run
  `uv run python manage.py apply_zoom_meeting_settings` to PATCH the
  settings onto meetings created before this flag existed. The patch never
  changes the join URL.

## ZOOM_JOIN_BEFORE_HOST

Purpose: Boolean. When false (the default), attendees cannot start a Zoom
meeting before the host arrives. Early joiners instead see Zoom's "waiting
for the host to start this meeting" hold, and the meeting (with its cloud
recording) only begins once the host joins — with no manual admitting. So
leaving this off is what ensures the recording never captures pre-host
waiting time.

Set it true only if you deliberately want attendees to start a meeting
without the host present (not recommended while cloud auto-recording is
on, since the recording would then capture pre-host time).

Type: boolean (`true` / `false`). Default: `false`.

Where it applies: same as `ZOOM_WAITING_ROOM` — `create_meeting` for new
meetings and `apply_zoom_meeting_settings` for existing upcoming meetings.

## ZOOM_AUTO_RECORDING

Purpose: Controls how event-created Zoom meetings auto-record. Set on every
meeting's `settings.auto_recording` in `_meeting_settings()` so each event
meeting starts recording automatically once the host joins, without anyone
clicking Record. The recording-ready webhook then drives the
`jobs/tasks/recording_upload.py` chain (pull from Zoom cloud → publish), so
`cloud` is required for that pipeline to find an asset.

Allowed values:

| Value   | Effect                                                        |
| ------- | ------------------------------------------------------------ |
| `cloud` | Record to the Zoom cloud (default; needed for the recording webhook). |
| `local` | Record to the host's local machine (no cloud asset to fetch). |
| `none`  | Do not auto-record.                                          |

Type: string (`cloud` / `local` / `none`). Default: `cloud`.

Account-level prerequisite: This per-meeting setting only takes effect if cloud
recording is enabled and not locked at the Zoom ACCOUNT level for the host
account used by the server-to-server OAuth app. If an account admin has cloud
recording disabled or locked off, Zoom ignores the per-meeting
`auto_recording: cloud` request and the meeting will not record — this is the
most common reason an event meeting fails to auto-record even though the payload
is correct (issue #1081). Verify it in the Zoom admin console under
Account Management → Account Settings → Recording. The licensing/account config
itself is not managed in this repo.

Where it applies: same as `ZOOM_WAITING_ROOM` — `create_meeting` for new
meetings and `apply_zoom_meeting_settings` (`update_meeting_settings`) for
existing upcoming meetings, so the backfill turns auto-record on for older
meetings too.

## Meeting payload and transcription

Meeting create, schedule-sync, and settings-backfill requests share these
supported canonical settings: `auto_recording`, `join_before_host`,
`mute_upon_entry`, and `waiting_room`.

Automatic audio transcription is configured at the Zoom account or user level.
It is deliberately not sent as `settings.auto_transcribing` in meeting POST or
PATCH payloads because that field is not supported by Zoom's meeting request
contract. Enable and lock transcription as needed in the authorized Zoom admin
console under Account Management → Account Settings → Recording.

## Reschedule failures and explicit retry

Ordinary Studio/API event edits are fail-soft: a valid local reschedule stays
saved and the existing attendee schedule-notification behavior still runs if
Zoom rejects the in-place meeting PATCH. The caller receives a bounded warning
with the operation and, when Zoom provides them, its HTTP status, provider code,
and sanitized provider message. Logs carry the same fields plus the platform
event ID/slug and Zoom meeting ID. Tokens, authorization headers, join URLs, raw
response bodies, and unrelated provider fields are neither surfaced nor logged.

To retry from the already-stored event state without making another edit or
sending attendees another schedule notification, use:

```bash
uv run asl events sync-zoom <event-slug>
```

This calls staff-token `POST /api/events/{slug}/sync-zoom`. The action PATCHes
the same stored meeting ID using the current event title, start/end datetimes,
timezone, and canonical settings. It never creates a replacement meeting,
saves the event, changes its meeting ID/join URL, regenerates a banner, or
enqueues attendee notifications. Repeating the action is safe and convergent.
Only future, non-cancelled, Studio/API-origin Zoom events with an existing
meeting ID are eligible.

A success returns `zoom_sync_status: "synced"` and `zoom_meeting_id` without a
join URL. Provider/auth/network failure returns HTTP 502 with code
`zoom_sync_failed` and sanitized structured diagnostics while leaving the local
event unchanged.

## Disposable lifecycle and recording smoke checklist

Run this only in an isolated development/non-production Zoom workspace and the
matching application environment. Never use a real event, an event with
registrations, or a meeting that people may join. Use a unique title and slug,
keep the event draft and unpublished, omit hosts, and use a start time far
enough in the future to complete and clean up the test.

The `asl` CLI defaults to `https://aishippinglabs.com` when `ASL_BASE_URL` is
unset. Prose context is not a safety control: every command below must use the
explicit non-production target guard. Never run an unguarded copied command.

Evidence must contain only the event slug, redacted meeting ID, status, and
allowlisted schedule fields. Do not paste raw CLI responses, webhook payloads,
OAuth tokens, secrets, recording download URLs, or join/start URLs into a
terminal transcript, screenshot, issue, or chat. The examples below pipe CLI
JSON directly through `jq` so those sensitive fields never reach stdout.

### Fail-closed non-production target preflight

Set the intended non-production application URL explicitly. The example uses
the development environment, not production. Set `ASL_API_TOKEN` to a staff
token minted for that same non-production environment; never reuse the
production token.

```bash
export SMOKE_EXPECTED_BASE_URL="https://dev.aishippinglabs.com"
export ASL_BASE_URL="$SMOKE_EXPECTED_BASE_URL"
read -r -s -p "Matching non-production staff API token: " ASL_API_TOKEN
printf '\n'
export ASL_API_TOKEN

require_nonproduction_zoom_smoke_target() {
  local resolved_base="${ASL_BASE_URL:-}"
  local expected_base="${SMOKE_EXPECTED_BASE_URL:-}"
  resolved_base="${resolved_base%/}"
  expected_base="${expected_base%/}"

  case "$resolved_base" in
    "https://aishippinglabs.com"|"https://www.aishippinglabs.com")
      echo "Refusing to run Zoom smoke against production" >&2
      return 1
      ;;
  esac
  if [ -z "$expected_base" ] || [ "$resolved_base" != "$expected_base" ]; then
    echo "ASL_BASE_URL does not match the intended non-production URL" >&2
    return 1
  fi
  if [ -z "${ASL_API_TOKEN:-}" ]; then
    echo "Set the matching non-production ASL_API_TOKEN first" >&2
    return 1
  fi
  printf 'Verified non-production ASL_BASE_URL=%s\n' "$resolved_base"
}

require_nonproduction_zoom_smoke_target &&
  uv run asl events list --status draft --format json >/dev/null
```

Do not continue unless the complete preflight returns zero. It rejects both
production hostnames before any API request, verifies the resolved URL equals
the intended host, refuses an unset/placeholder token, then uses a read-only
request to confirm that the token authenticates there. On success it prints
only the resolved non-production base URL. Every later mutating CLI example
calls the guard again; do not remove it or change either URL variable between
preflight and cleanup.

### Meeting create, read, update, repeat, and delete

1. Export a unique slug and ISO-8601 schedule in the isolated operator shell:

   ```bash
   export SMOKE_EVENT_SLUG="zoom-scope-smoke-$(date -u +%Y%m%d%H%M%S)"
   export SMOKE_START_AT="$(date -u -d '+7 days 14:00' +%Y-%m-%dT%H:%M:%SZ)"
   export SMOKE_END_AT="$(date -u -d '+7 days 14:30' +%Y-%m-%dT%H:%M:%SZ)"
   ```

   Choose future values appropriate to the day of the test. Do not echo any
   credential or URL variables.

2. Create exactly one disposable Zoom-backed event. Keep it draft,
   unpublished, unregistered, hostless, and without a generated banner:

   ```bash
   require_nonproduction_zoom_smoke_target &&
     uv run asl events create \
     --title "Disposable Zoom scope smoke" \
     --slug "$SMOKE_EVENT_SLUG" \
     --platform zoom \
     --start-datetime "$SMOKE_START_AT" \
     --end-datetime "$SMOKE_END_AT" \
     --timezone UTC \
     --status draft \
     --no-publish \
     --create-zoom \
     --no-generate-banner \
     --format json \
     | jq '{slug, status, published, zoom_meeting_id}'
   ```

   Confirm one meeting ID exists. Record only a redacted form such as its last
   four digits in test evidence. Keep the full ID private for the next step.

3. Read the provider meeting back with the S2S token, printing only schedule
   fields. This operator-only GET is why the scope matrix includes the granular
   meeting read scope. Run the Django shell **inside the same non-production
   deployed application environment behind `ASL_BASE_URL`**, with its matching
   database and Zoom credentials—not from a local checkout and never from
   production. Export `SMOKE_EXPECTED_BASE_URL` and
   `SMOKE_ZOOM_MEETING_ID` inside that execution environment. The command
   independently rejects production or a mismatched application URL before it
   obtains a Zoom token:

   ```bash
   export SMOKE_ZOOM_MEETING_ID="<private meeting ID from step 2>"
   uv run python manage.py shell -c '
   import os, requests
   from integrations.config import site_base_url
   from integrations.services.zoom import get_access_token
   expected_base = os.environ["SMOKE_EXPECTED_BASE_URL"].rstrip("/")
   actual_base = site_base_url().rstrip("/")
   production_urls = {
       "https://aishippinglabs.com",
       "https://www.aishippinglabs.com",
   }
   if actual_base in production_urls or actual_base != expected_base:
       raise SystemExit("Refusing mismatched or production application environment")
   print("application_base_url", actual_base)
   meeting_id = os.environ["SMOKE_ZOOM_MEETING_ID"]
   response = requests.get(
       f"https://api.zoom.us/v2/meetings/{meeting_id}",
       headers={"Authorization": f"Bearer {get_access_token()}"},
       timeout=30,
   )
   print("status", response.status_code)
   if response.ok:
       body = response.json()
       print({key: body.get(key) for key in
              ("topic", "start_time", "duration", "timezone")})
   response.raise_for_status()
   '
   ```

   The same rule applies to any `manage.py shell` or `clear_token_cache()` step:
   run it only inside that matching non-production deployment. Never use a
   local process or production process as a shortcut. A cache clear affects
   only its current process; use the documented web/worker restart procedure
   when all non-production processes need fresh tokens.

4. Exercise update and explicit retry without changing local event state or
   creating another meeting:

   ```bash
   require_nonproduction_zoom_smoke_target &&
     uv run asl events update "$SMOKE_EVENT_SLUG" \
     --title "Disposable Zoom scope smoke — updated" \
     --format json \
     | jq '{slug, title, status, published, zoom_meeting_id}'

   require_nonproduction_zoom_smoke_target &&
     uv run asl events sync-zoom "$SMOKE_EVENT_SLUG" --format json \
     | jq '{zoom_sync_status, zoom_meeting_id}'

   require_nonproduction_zoom_smoke_target &&
     uv run asl events sync-zoom "$SMOKE_EVENT_SLUG" --format json \
     | jq '{zoom_sync_status, zoom_meeting_id}'
   ```

   Confirm both retries report `synced`, the same redacted meeting identity is
   retained throughout, repeating the provider read-back in step 3 shows the
   updated topic, and no
   replacement meeting, notification, registration, publication, or local
   event save is produced by either retry.

5. Exercise deletion by cancelling the still-future disposable event:

   ```bash
   require_nonproduction_zoom_smoke_target &&
     uv run asl events update "$SMOKE_EVENT_SLUG" \
     --status cancelled \
     --format json \
     | jq '{slug, status, published, zoom_meeting_id}'
   ```

   Confirm the local meeting identity is cleared and a provider GET using the
   private ID from step 3 now returns HTTP 404. Do not print the response body.
   The event remains as an auditable cancelled, unpublished disposable record;
   delete it only under the environment's ordinary test-data retention policy.

### Recording webhook and authenticated download

This requires a second disposable event and meeting because the lifecycle
meeting above was deliberately deleted. Before creating it:

- Set `RECORDING_AUTO_PUBLISH_ON_S3_UPLOAD=false` in the isolated environment
  and restore the previous value after the smoke test.
- Leave `STAFF_SIGNUP_NOTIFY_EMAIL` blank, omit `host_email` and host profiles,
  and add no registrations. This makes the recording-ready notification skip
  with `no_recipient` instead of emailing a person.
- Confirm the isolated environment points at a disposable/test recordings
  bucket or prefix. Do not run the recording smoke against production storage.

Then:

1. Set `SMOKE_EVENT_SLUG` to a new unique recording-smoke slug, then create a
   second draft, unpublished, hostless event with `--create-zoom` as above:

   ```bash
   export SMOKE_EVENT_SLUG="zoom-recording-smoke-$(date -u +%Y%m%d%H%M%S)"
   ```

   Keep all CLI output behind the same `jq` allowlist.
2. Have a licensed host start the disposable meeting and record 10–30 seconds
   of a non-sensitive color slate with no people, screens, names, or private
   audio. Stop recording and end the meeting so Zoom emits
   `recording.completed`.
3. In **Studio → Webhooks**, confirm the matching Zoom
   `recording.completed` row is processed. Do not open, copy, or capture its raw
   payload because it contains recording URLs.
4. In **Studio → Worker**, confirm the named **Upload Zoom recording** task
   succeeds. Inspect only status and the disposable event identity—not task
   arguments, which contain the download URL.
5. Re-fetch the event through the CLI and allowlist only safe state:

   ```bash
   require_nonproduction_zoom_smoke_target &&
     uv run asl events get "$SMOKE_EVENT_SLUG" --format json \
     | jq '{slug, status, published,
            has_recording: ((.recording_s3_url // "") != "")}'
   ```

   Confirm `has_recording` is true while `published` remains false. Confirm no
   `event_recording_ready` email was sent and the task reports notification
   status `skipped` with reason `no_recipient`.
6. Delete the disposable cloud recording and meeting in the isolated Zoom UI,
   remove the test object from disposable S3 storage, cancel the local event
   using the same guarded update command, and restore the two configuration
   values. Keep only redacted status evidence.

## Official Zoom references

- [Create and manage a Server-to-Server OAuth app](https://developers.zoom.us/docs/internal-apps/create/)
- [Server-to-Server OAuth token flow](https://developers.zoom.us/docs/internal-apps/s2s-oauth/)
- [Meeting API scopes and endpoints](https://developers.zoom.us/docs/api/meetings/)
- [Webhook event scopes](https://developers.zoom.us/docs/api/webhooks/)
- [Zoom API role privileges](https://developers.zoom.us/docs/api/references/privileges/)
- [Cloud recording account, license, and host prerequisites](https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0063923)
