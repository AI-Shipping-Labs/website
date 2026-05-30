# Banner Generator integration setup

This page documents every setting registered in
`integrations/settings_registry.py` under the `banner_generator` group
(issue #788). Each section follows the same template — Purpose,
Without it, Where to find it, Prereqs, Rotation, Test vs live.

The banner-generator is a separate Lambda (source at
`AI-Shipping-Labs/banner-generator`) that renders OG-card JPEGs from a
small templated JSON payload. The platform calls it from the content
sync pipeline (auto-banner on create / title-change) and from the
Studio "Regenerate banner" buttons on the article / course / project /
download / workshop edit pages.

The Lambda writes the rendered JPEG straight into the existing content
CDN bucket (`AWS_S3_CONTENT_BUCKET`). Each render uses a new cache-busting
object key under the generated-banner prefix, for example
`{CONTENT_CDN_BASE}/banners/<content_type>/<content_id>-<uuid>.jpg`.
The website persists the exact key sent to the Lambda on
`auto_banner_url`, so the database URL and uploaded object stay in
sync without requiring CloudFront invalidation.

## BANNER_GENERATOR_FUNCTION_URL

Purpose: HTTPS Function URL of the deployed Lambda. Every render call
POSTs JSON to this URL with the OG-card payload + the S3 target. The
platform expects the Lambda to respond with `{"ok": true, ...}` on
success.

Without it: Auto-banner generation is silently skipped. New synced
content keeps an empty `auto_banner_url`; the Studio "Regenerate
banner" button renders disabled with a tooltip linking to this
settings section. No errors are raised — the platform is happy to
serve content without auto-banners.

Where to find it: The Lambda Function URL is shown in the AWS Lambda
console for the `banner-generator` function, on the "Function URL"
tab. The operator who deployed the Lambda hands it over out-of-band
together with the bearer token below.

Prereqs:

- The Lambda is deployed in the same AWS account that owns
  `AWS_S3_CONTENT_BUCKET`, AND the Lambda's execution role has
  `s3:PutObject` on `arn:aws:s3:::<bucket>/banners/*`. Without that,
  the Lambda returns HTTP 5xx on every call — the platform logs a
  WARNING and moves on.
- The website runtime identity should have `s3:DeleteObject` on
  `arn:aws:s3:::<bucket>/banners/*` so it can remove the previous
  generated object after a successful re-render. Missing delete
  permission does not fail regeneration; cleanup logs a WARNING and the
  new `auto_banner_url` stays persisted.
- `AWS_S3_CONTENT_BUCKET` and `CONTENT_CDN_BASE` are configured under
  Studio > Settings > Storage. The bucket is the upload target, the
  CDN base is what we persist on `auto_banner_url`.

Rotation: Function URLs rarely rotate. If the Lambda is redeployed
behind a new URL, paste the new value here and save. Existing
`auto_banner_url` rows continue to point at the CDN, so the rotation
only affects future renders.

Test vs live: Use the sandbox Lambda URL in dev / staging environments
and the production Lambda URL in prod. The S3 bucket and CDN base
should be environment-specific so test renders don't pollute the live
content CDN.

## BANNER_GENERATOR_AUTH_TOKEN

Purpose: Bearer token sent in the `Authorization: Bearer <token>`
header on every render request. The Lambda rejects calls with a wrong
or missing token.

Without it: Auto-banner generation is silently skipped, same as when
the Function URL is unset. The platform treats either missing value as
"banner-generator disabled".

Where to find it: Issued out-of-band by the operator who configured
the Lambda. The token is stored as a Lambda environment variable on
the deployed function; rotate it there and paste the new value into
Studio here.

Prereqs: None beyond a deployed Lambda that knows the same token.

Rotation: To rotate, update the token on the Lambda first, then paste
the new token into Studio. There is a brief window where the platform
sends the old token to the new Lambda (or vice-versa) — those calls
fail with a logged WARNING and the sync continues. No content is lost.

Test vs live: Use different tokens in sandbox vs production so a leaked
sandbox token cannot be used to render against the production bucket.

## Notes

- Banner generation runs as a fire-and-forget `async_task` on
  django-q2. Failures (network, HTTP 5xx, malformed JSON, `ok: false`)
  are logged at WARNING and never block the sync pipeline or the
  operator-initiated regenerate action.
- Regeneration writes a new `.jpg` object every time to avoid stale CDN
  or browser caches. After the database row points at the new URL, the
  website best-effort deletes the previous generated object only when
  the old URL is under the configured `CONTENT_CDN_BASE` and the
  matching `banners/<content_type>/` prefix. External URLs, manual cover
  images, mismatched prefixes, and suspicious encoded/query URLs are not
  deleted.
- The bearer token is `is_secret=True` so the Studio settings page
  renders it in a `<input type="password">` and the JSON export
  redacts it. The token never appears in log lines or rendered
  template output for any content edit page.
- The Lambda's IAM `s3:PutObject` grant is configured in
  `AI-Shipping-Labs/ai-shipping-labs-infra`. If you see HTTP 5xx
  responses from the Lambda after wiring everything, the IAM policy
  is the first thing to check.
