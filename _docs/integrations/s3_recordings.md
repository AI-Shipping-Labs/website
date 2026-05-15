# S3 Recordings integration setup

This page documents every setting registered in
`integrations/settings_registry.py` under the `s3_recordings` group.
Each section follows the same template — Purpose, Without it, Where to
find it, Prereqs, Rotation, Test vs live.

The recordings bucket is the durable staging area for Zoom cloud
recordings before they are pushed to YouTube. The flow is:

1. Zoom posts `recording.completed`.
2. `jobs/tasks/recording_upload.py` downloads the MP4 from Zoom.
3. `jobs/tasks/recordings_s3.py` uploads it to this bucket.
4. `jobs/tasks/youtube_upload.py` reads it back from this bucket and
   uploads to YouTube.

The bucket is private — there is no public listing or CDN fronting it,
and objects are accessed only via the platform's IAM user.

Direct deep-link URLs are intentionally written in code blocks so they
do not render as clickable links. Copy them into the browser.

## AWS_S3_RECORDINGS_BUCKET

Purpose: Bucket name where event recordings land. Read by
`jobs/tasks/recordings_s3.py:28` (upload) and
`jobs/tasks/youtube_upload.py:146` (download). The IAM user defined by
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
  this bucket; YouTube is the public surface).
- Lifecycle rule recommended: transition to Glacier (or expire) after
  N days once a recording has been confirmed on YouTube. The platform
  does not delete its own objects.
- IAM policy on the SES user must include:

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
   recordings start landing in the new bucket on the next webhook
   delivery.

Test vs live: n/a. Use a different bucket per environment if you want
to isolate dev recordings — naming convention
`<env>-recordings-<random>` works well.

## AWS_S3_RECORDINGS_REGION

Purpose: AWS region of the recordings bucket. Read by
`jobs/tasks/recordings_s3.py:30` with a fallback default of
`eu-central-1`. Used to construct the S3 client and the
`https://<bucket>.s3.<region>.amazonaws.com/<key>` URL referenced by
the YouTube uploader.

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
