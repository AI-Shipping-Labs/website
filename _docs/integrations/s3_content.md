# S3 Content Images integration setup

This page documents every setting registered in
`integrations/settings_registry.py` under the `s3_content` group. Each
section follows the same template — Purpose, Without it, Where to find
it, Prereqs, Rotation, Test vs live.

The content-images bucket holds every image referenced from a synced
markdown article, course, or project. The flow is:

1. Content sync clones the content repo.
2. `integrations/services/github_sync/media.py` walks the markdown for
   image references.
3. Each image is uploaded to this bucket (public-read).
4. The markdown is rewritten so the image URL is the CDN host
   (`CONTENT_CDN_BASE`) fronting the bucket.

This bucket is public-read by design — images load directly in
browsers via the CDN.

Direct deep-link URLs are intentionally written in code blocks so they
do not render as clickable links. Copy them into the browser.

## AWS_S3_CONTENT_BUCKET

Purpose: Bucket name for synced content images. Read by
`integrations/services/github_sync/media.py:151`. The IAM user defined
by `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` must have
`s3:PutObject` on this bucket with `public-read` ACL.

Without it: `integrations/services/github_sync/media.py:155` logs
`AWS_S3_CONTENT_BUCKET not configured, skipping image upload` and the
sync skips image upload for the run. The markdown still imports, but
image references in it remain as the original (raw GitHub) URLs.
Those URLs work for public content repos but break on private ones,
so the live site shows broken images until the bucket is configured.

Where to find it:

- AWS console > S3 > "General purpose buckets". Copy the exact bucket
  name.
- Direct link:

  ```
  https://s3.console.aws.amazon.com/s3/buckets
  ```

Prereqs:
- Create the bucket in the same region as `AWS_S3_CONTENT_REGION`.
- Disable "Block all public access" on this bucket only — content
  images need to be publicly fetchable.
- Add a bucket policy allowing `s3:GetObject` to `*` for `arn:aws:s3:::<bucket>/*`.
- Recommended: enable static-website hosting OR put a CloudFront
  distribution in front (see `CONTENT_CDN_BASE`).
- IAM policy on the platform IAM user:

  ```
  s3:PutObject
  s3:PutObjectAcl
  s3:GetObject
  s3:ListBucket
  ```

  scoped to `arn:aws:s3:::<bucket>/*` and `arn:aws:s3:::<bucket>`.

Rotation: n/a — bucket names are permanent. To migrate:

1. Create the new bucket with the same public-read policy.
2. `aws s3 sync` existing images over.
3. Update this setting, then re-run content sync so freshly synced
   markdown points at the new bucket.
4. Old image URLs continue to resolve to the old bucket until you
   tear it down. Keep it alive until every sync has refreshed.

Test vs live: n/a. Per-environment buckets are fine — just match
`CONTENT_CDN_BASE` to whichever bucket each environment uses.

## AWS_S3_CONTENT_REGION

Purpose: AWS region of the content-images bucket. Read by
`integrations/services/github_sync/media.py:152` with a fallback of
`eu-central-1`. Used to construct the boto3 S3 client.

Without it: Falls back to `eu-central-1`. If the bucket lives
elsewhere, boto3 follows the redirect on first request and retries —
functional but slow per-image.

Where to find it:

- AWS console > S3 > select the bucket > "Properties" tab > "AWS
  Region" panel.
- Or use this URL with the bucket name filled in:

  ```
  https://s3.console.aws.amazon.com/s3/buckets/<bucket>?tab=properties
  ```

Prereqs: The bucket must exist in the specified region.

Rotation: n/a. Region cannot change after bucket creation. To move
regions, follow the migration in `AWS_S3_CONTENT_BUCKET`.

Test vs live: n/a. Match the region to the bucket per environment.

## CONTENT_CDN_BASE

Purpose: Public CDN base URL fronting the content-images bucket
(e.g. `https://cdn.aishippinglabs.com`). Read by
`integrations/services/github_sync/media.py:18` to rewrite synced
markdown so image references point at the CDN, not the raw S3 URL.
Browsers hit this host directly when rendering the article body.

Without it: Falls back to `/static/content-images` (the dev-only
default in `website/settings.py:424`), which serves images through
Django's static-file handler. Acceptable on a developer's laptop;
catastrophic in production because every image request hits a Django
process. Images also break across deploys because the static path is
ephemeral.

Where to find it:

- AWS console > CloudFront > Distributions > pick the distribution
  fronting your content bucket > "Domain name" (e.g. `dxyzabc.cloudfront.net`).
- Direct link:

  ```
  https://console.aws.amazon.com/cloudfront/v4/home#/distributions
  ```

- Recommended: configure a custom domain (`cdn.<your-domain>`) via
  AWS ACM + Route 53, and use that. The platform stores whatever
  hostname you put here.

Prereqs:
- A CloudFront distribution (or another CDN) configured to serve from
  the S3 content bucket.
- HTTPS — the platform's live site is HTTPS, so mixed-content blocking
  rejects http-only image URLs.
- Optional but recommended: aggressive cache headers on the
  distribution (images are content-addressed, so they are safe to
  cache forever).

Rotation: Safe to rotate as long as the new host serves the same
keys.

1. Stand up the new CDN host pointing at the same content bucket.
2. Update this setting via Studio (Integration settings > S3 Content
   Images > `CONTENT_CDN_BASE`).
3. Re-run content sync so every article's stored HTML is rewritten to
   the new host. Until then, old articles keep referencing the old
   CDN (which still works if you leave the old distribution up).
4. Window of impact: zero if both CDNs serve the same keys. Tear down
   the old distribution only after every article has been re-synced.

Test vs live: n/a beyond per-environment override — dev typically
uses `/static/content-images` (the default), and production uses the
production CloudFront host.
