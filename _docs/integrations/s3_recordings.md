# S3 Recordings integration setup

This page documents every setting declared in
`integrations/settings_keys.py` under the `s3_recordings` group.
Each section follows the same template — Purpose, Without it, Where to
find it, Prereqs, Rotation, Test vs live.

The recordings bucket is the durable, private home for Zoom cloud
recordings. Members watch them through the access-controlled in-app
player, which redirects to a short-lived presigned S3 URL. The flow is:

1. The platform finds out that Zoom has a finished MP4 in one of two ways,
   whichever happens first:
   - the `recording-pipeline-watch` schedule (every minute) lists the
     Zoom recordings of every event that started between 10 minutes and 3
     days ago and has no stored MP4 yet
     (`events/services/recording_pipeline_watch.py`);
   - Zoom posts `recording.completed` or `recording.transcript_completed`
     to dapier (the platform has no Zoom webhook, issue #1913). The
     `aisl-recording-agent` workflow drives
     `POST /api/recording-intake`.
   Both paths call `refresh_transcript_from_zoom`, which stores the MP4
   download URL, stamps `Event.recording_zoom_available_at`, and enqueues
   the upload. Neither waits for the transcript.
2. `jobs/tasks/recording_upload.py` downloads the MP4 from Zoom.
3. `jobs/tasks/recordings_s3.py` uploads it to this bucket and stores
   the S3 URL on the event.
4. Right after the upload, the host "Recording ready for review" email
   goes out (`events/services/recording_ready_notification.py`) and the
   transcript task is chained.
5. The authenticated serving endpoint mints a presigned `GetObject`
   URL (`build_recording_presigned_url` in
   `jobs/tasks/recordings_s3.py`) and redirects the in-app player to it.

## Recording-ready bound

The MP4 upload and host email must be confirmed complete within 60 minutes
of Zoom's `recording.completed` availability timestamp. The Dapier producer
passes this timestamp to the website; retries preserve it. The one-minute
reconciler is a recovery path, not evidence of Zoom's original availability:
when a webhook is missing, first poll observation is recorded explicitly.
A later webhook replaces it only when its timestamp is earlier.

A local `EmailLog` proves campaign submission only. The watchdog requires
Relay aggregate `sent_count` to cover every deduplicated person and no pending
recipients, then stores `recording_ready_completed_at`. It reports unconfirmed
or first-confirmed-late delivery as a deadline breach rather than claiming a
healthy send from a queued campaign. Confirmation polling runs every minute. Unknown state has four minutes of
observation grace plus at most one minute of scheduler delay (five minutes total). S3 upload errors and missing host email
submission are retried through the existing upload lease/reclaim and the
per-event notice reconciliation task.

Before intake 1f596393 the dapier workflow only ran on
`recording.transcript_completed`, and the upload and host email waited on
the transcript. For event 69 (Evals in CI) the email arrived about 19 hours
after the recording ended.

If the S3 upload or the host email has not finished within the bound,
`recording-pipeline-watch` posts a stall alert to the staff Slack alert
channel (`STAFF_COMMENT_NOTIFY_CHANNEL_ID`, falling back to
`STAFF_SIGNUP_NOTIFY_CHANNEL_ID`). If Slack is unavailable, it emails the
dev mailbox instead. It alerts once per event
(`Event.recording_stall_alerted_at`). When no route accepts the alert, it
retries on every pass instead of dropping it. The alert names the stalled
step and links to Studio. It never includes the S3 or Zoom URLs.

Recipients of the host email are deduplicated by person, not by exact
address. `event.host_email` comes first, then active Host profile emails.
Addresses that resolve to the same account through a primary login or an
`EmailAlias` count once. Record known delivery forwards (for example a
Relay inbound forward from an `@aishippinglabs.com` mailbox into a work
inbox) as an `EmailAlias` on the person's account with
`POST /api/users/<email>/aliases`. Then the forwarded copy is not sent.

The bucket is private — there is no public listing or CDN fronting it,
and objects are accessed only via the platform's IAM user or short-lived
presigned URLs.

Direct deep-link URLs are intentionally written in code blocks so they
do not render as clickable links. Copy them into the browser.

## AWS_S3_RECORDINGS_BUCKET

Purpose: Bucket name where event recordings land. Read by
`jobs/tasks/recordings_s3.py` for both upload and presigned playback
URLs. The IAM user defined by
`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` must have
`s3:PutObject` and `s3:GetObject` on this bucket.

Without it: `jobs/tasks/recording_upload.py:48` logs
`AWS_S3_RECORDINGS_BUCKET not configured, skipping upload for event
%s` and the recording upload chain skips for that event. Zoom's cloud
copy stays in place (until Zoom's retention policy reclaims it), so
this is recoverable as long as you fix the setting before Zoom expires
the recording.

Where to find it:

- AWS console > S3 > "General purpose buckets". The bucket name is
  the leftmost column — copy that exact string.
- Direct link:

  ```
  https://s3.console.aws.amazon.com/s3/buckets
  ```

- The bucket name is globally unique across AWS, so the value is
  unambiguous.

Prereqs:
- Create the bucket in the same region as `AWS_S3_RECORDINGS_REGION`
  (cross-region traffic is allowed but slow and pays egress).
- Block all public access (the platform never serves directly from
  this bucket; members watch via short-lived presigned URLs behind the
  authenticated in-app player).
- Lifecycle rules are optional: the platform serves recordings straight
  from this bucket, so keep objects available for as long as members
  should be able to watch. The platform does not delete its own objects.
- The shared S3 credential identity has no mail-send role. Its IAM policy must
  include:

  ```
  s3:GetObject
  s3:PutObject
  s3:ListBucket
  ```

  scoped to `arn:aws:s3:::<bucket>/*` and `arn:aws:s3:::<bucket>`.

Rotation: n/a — bucket names are permanent. To migrate to a new
bucket:

1. Create the new bucket and update its IAM policy.
2. Copy existing objects via `aws s3 sync`.
3. Update this setting via Studio (Integration settings > S3
   Recordings > `AWS_S3_RECORDINGS_BUCKET`).
4. Window of impact: zero if you copy first and switch second. New
   recordings start landing in the new bucket on the next dapier-triggered
   intake (`sync-transcript`) run.

Test vs live: n/a. Use a different bucket per environment if you want
to isolate dev recordings — naming convention
`<env>-recordings-<random>` works well.

## AWS_S3_RECORDINGS_REGION

Purpose: AWS region of the recordings bucket. Read by
`jobs/tasks/recordings_s3.py` with a fallback default of
`eu-central-1`. Used to construct the S3 client and the
`https://<bucket>.s3.<region>.amazonaws.com/<key>` URL stored on the
event and resolved for presigned playback.

Without it: Falls back to `eu-central-1`. If the bucket lives in a
different region, boto3 returns a permanent redirect (HTTP 301) on
the first request and then retries with the correct region — slow but
functional. Set it explicitly to avoid the redirect tax on every
upload.

Where to find it:

- AWS console > S3 > select the bucket > "Properties" tab > "AWS
  Region" panel. The region code (e.g. `eu-west-1`) is shown there.
- Direct link, replacing `<bucket>`:

  ```
  https://s3.console.aws.amazon.com/s3/buckets/<bucket>?tab=properties
  ```

Prereqs: The bucket must already exist in the specified region.

Rotation: n/a. Buckets cannot move regions — to "rotate" the region,
create a bucket in the new region and follow the migration steps in
`AWS_S3_RECORDINGS_BUCKET`.

Test vs live: n/a. Pair the region with whichever bucket each
environment uses.

## RECORDING_PRESIGNED_URL_TTL_SECONDS

Purpose: Lifetime (in seconds) of the short-lived presigned S3 GetObject
URL the access-controlled recording serving endpoint redirects to (issue
#1134). Default 900 (15 minutes). The presigned URL is never rendered into
HTML — the in-page video player points at the authenticated serving
endpoint, which re-checks access and mints a fresh presigned URL on every
request.

Without it: Falls back to 900 seconds (15 minutes).

Where to find it: Studio integration settings (`S3 Recordings` group). Set
a positive integer. Keep it long enough that a member can watch / seek
without the URL expiring mid-playback, but short enough that a leaked URL is
quickly useless.

Prereqs: The recordings bucket and serving endpoint must be configured.

Rotation: Safe to change; the next serving request mints a URL with the new
TTL.

Test vs live: Configure independently in each deployment.

## RECORDING_AUTO_PUBLISH_ON_S3_UPLOAD

Purpose: When on, a successful Zoom -> S3 recording upload auto-publishes
the event so entitled members can watch the recording right away, and the
host notification says the recording is available to watch with a link to
the workshop video page (issue #1134, Phase B). On by default per the
product decision that the recording should be watchable immediately.

Without it: Defaults to `true` (auto-publish on upload).

Where to find it: Studio integration settings (`S3 Recordings` group).
Boolean toggle. Turn it off to keep the review-first flow: the event stays
unpublished after upload and the host email keeps the "ready for
review/publishing" framing with a Studio link.

Prereqs: The Zoom -> S3 recording upload pipeline must be wired.

Rotation: Safe to flip at any time; it affects the next upload.

Test vs live: Configure independently in each deployment.

## Workshop preparation

`RECORDING_PREPARATION_HUB_URL` is the existing Hub inbound webhook job
route (`https://hub.dtcdev.click/webhooks/<route UUID>`).
`RECORDING_PREPARATION_HUB_TOKEN` is its producer secret, stored as a secret
integration setting. Both are editable through Studio settings or the staff
integration settings API without redeployment. A missing configuration raises
an operational alert; it does not silently disable required preparation.

Dapier calls staff-authenticated `POST /api/recording-intake` deterministically
before any agent classification. The body includes `event`, `meeting_id`,
`recording_uuid`, `start_time`, and `available_at`. Supported events are
`recording.completed` and `recording.transcript_completed`. The website matches
one event occurrence, rejects ambiguous matches, and ignores non-AISL meetings.
The producer must supply the original provider readiness timestamp in
`available_at`; missing, null, malformed, naive and future values return `422`
without queuing work. Local queue receipt time is never substituted by this API.
Availability is stored before downstream work is queued. Replays preserve the
earliest receipt time. A delayed webhook can correct a later poll observation.

The transcript phase creates one durable `RecordingPreparation` per event.
The website sends `X-Delivery-Id: aisl-recording-preparation-<event ID>` to Hub;
retries, including ambiguous HTTP timeouts, use that same key. Hub receives only
an authenticated source reference, not a private storage URL. Its Hetzner job
reads timestamped VTT from `/api/events/<slug>/preparation-source`, generates
video chapters and a workshop summary using `workshop-followup-materials` and
the `AI-Shipping-Labs/workshops-raw` format, and returns review artifacts.
A source response of `409` means capture is still running and must be retried.

Automatic transcript processing always uses this draft job. The legacy recap
publishing path is reserved for explicit operator `redraft=True` requests.
No attendee email or content publication is part of preparation.

Hub must report running within 5 minutes of transcript readiness, and finished
with artifact proof within 60 minutes. A queued HTTP receipt is not completion.
The status endpoint `/api/events/<slug>/preparation` exposes the durable delivery
identity, ready/start/finish timestamps, artifact references and safe error code.
Missing start, missing finish and failed jobs alert the existing staff Slack
channel with the dev mailbox fallback. Start and finish breaches have independent
markers, so a start alert cannot hide a later finish failure. Failed alert delivery
is retried. A late successful job still alerts about its missed deadline.

The watchdog runs every minute. Unknown completion state has four minutes
of observation grace plus at most one minute of scheduler delay (five minutes total). It checks durable deadline
state before queuing independent per-event provider tasks. A slow Zoom or Hub
request cannot prevent the next local deadline check. Provider reconciliation is
bounded and rotates by update time so old pending jobs cannot monopolize the queue.

`Event.recording_zoom_uuid` binds the canonical Zoom occurrence on first receipt.
Same-UUID notifications and MP4 segments are replays of one recording. A different
UUID for the same event returns `409 conflicting_recording_identity`, alerts staff,
and cannot overwrite availability, transcript, delivery identity or completed proof.
A legitimate replacement occurrence requires operator mapping review; ingestion
never silently resets a completed recording's identity.

An unconfirmed Hub start/finish alert becomes eligible after four minutes
of observation grace (ready + 9 minutes / ready + 64 minutes respectively),
then runs within the next one-minute tick (ready + 10 / ready + 65 at latest),
so the first scheduled status refresh can establish an on-time job start before
an alarm. Explicit provider timestamps proving a late start or finish still raise
the relevant deadline alert. Alert copy states that completion was not confirmed;
it does not invent a provider start timestamp from a queued receipt.
