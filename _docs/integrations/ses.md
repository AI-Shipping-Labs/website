# Email (SES) integration setup

This page documents every SES-related setting registered in
`integrations/settings_registry.py` (the `ses` group). Each section
follows the same template — Purpose, Without it, Where to find it,
Prereqs, Rotation, Test vs live — so an operator can answer "do I need
to set this right now, or can I defer it?" without leaving the page.

All transactional email (verification, password reset, paid-tier
notifications) and promotional email (newsletters, course campaigns)
flow through Amazon SES. Direct deep-link URLs are intentionally
written in code blocks so they do not render as clickable links. Copy
them into the browser.

## AWS_ACCESS_KEY_ID

Purpose: AWS access key for the IAM user the platform uses to talk to
SES. Read by `email_app/services/email_service.py` and
`events/services/registration_email.py` when constructing the boto3
SES client. The same key is reused by recordings uploads
(`jobs/tasks/recordings_s3.py`) and content-image uploads
(`integrations/services/github_sync/media.py`), so a single IAM user
holds permissions for SES + both S3 buckets.

Without it: Every outbound email returns
`NoCredentialsError` from boto3 — verification email never sends, new
users cannot confirm their account, and the paid-checkout
notification email is silently dropped (the webhook handler logs but
does not retry). Existing users are unaffected; only new outbound
mail breaks.

Where to find it:

- AWS console > IAM > Users > pick the platform IAM user > "Security
  credentials" > "Access keys" > "Create access key". AWS shows the
  pair (key id + secret) once. Save both immediately.
- Direct link:

  ```
  https://console.aws.amazon.com/iam/home#/users
  ```

Prereqs:
- An IAM user attached to a policy that grants:
  - `ses:SendEmail`, `ses:SendRawEmail`
  - `ses:GetSuppressedDestination`, `ses:ListSuppressedDestinations`,
    `ses:PutSuppressedDestination`, `ses:DeleteSuppressedDestination`
  - `s3:PutObject`, `s3:GetObject` on the recordings and content
    buckets (these are scoped per-bucket; see `s3_recordings` /
    `s3_content` groups).
- The sending region must already be out of SES sandbox (or the
  recipients must be verified). See
  `https://console.aws.amazon.com/ses/home#/account` to request
  production access.

Rotation: Safe to rotate.

1. AWS console > IAM > Users > the IAM user > "Security credentials" >
   "Create access key". AWS allows two active keys per user during the
   transition window.
2. Update both `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` via
   Studio (Integration settings > Email (SES)) or via
   `POST /api/integrations/settings`.
3. Once the platform is using the new pair, delete the old key from
   the same IAM console screen.
4. Window of impact: between the moment you start the rotation and the
   moment you save both new values together, in-flight outbound email
   may fail with `SignatureDoesNotMatch` if the old key was already
   deleted. Keep the old key active until the new pair is saved.

Test vs live: AWS does not have a sandbox vs live distinction for IAM
keys — but SES itself has a sandbox in each region. If your region is
in sandbox mode, you can only send to verified addresses regardless
of the key. Request production access per-region from:

```
https://console.aws.amazon.com/ses/home#/account
```

## AWS_SECRET_ACCESS_KEY

Purpose: AWS secret key paired with `AWS_ACCESS_KEY_ID`. Used by
boto3 to sign every SES (and S3) request. The platform stores it as
a secret-marked `IntegrationSetting`, so it is masked in Studio after
saving.

Without it: Same failure mode as a missing `AWS_ACCESS_KEY_ID` — boto3
raises `NoCredentialsError`, no email or S3 traffic leaves the box.

Where to find it: AWS shows the secret key exactly once, at the moment
you create the access key. There is no way to retrieve it later — if
lost, you must rotate. See the same console path as
`AWS_ACCESS_KEY_ID`.

Prereqs: Same IAM user and policy as `AWS_ACCESS_KEY_ID`.

Rotation: Always rotate the secret alongside the access key id as a
pair (they are minted together).

1. Create a new access-key pair in the IAM console.
2. Save both `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` in Studio
   in the same submit.
3. Disable, then delete the old key from IAM. AWS allows two active
   keys per user precisely to enable zero-downtime rotation.

Test vs live: n/a. Same as `AWS_ACCESS_KEY_ID`.

## AWS_SES_REGION

Purpose: AWS region the SES client targets (e.g. `eu-west-1`,
`us-east-1`). Read by `email_app/services/email_service.py:77`,
`events/services/registration_email.py:139`, and the registration
email helpers. Must match the region where the sending identities
(domain or email) are verified — SES is region-scoped.

Without it: Defaults to `us-east-1`. If your verified identities live
in a different region (e.g. you onboarded SES in `eu-west-1` for
GDPR locality), boto3 will succeed in connecting but every
`SendEmail` call returns `MessageRejected: Email address is not
verified`.

Where to find it:

- AWS console > SES > pick the region from the region dropdown (top
  right). Verified identities and configuration sets are listed for
  the currently selected region only.
- Direct link:

  ```
  https://console.aws.amazon.com/ses/home
  ```

  Then read the region segment of the URL (e.g.
  `?region=eu-west-1`).

Prereqs: SES must be configured in that region — at minimum, a
verified domain and a request for production access. Sending from a
sandbox region only works to verified recipient addresses.

Rotation: n/a routine. Change the value if you migrate SES to a new
region; you must also re-verify the sending domain and configuration
sets in the new region first.

Test vs live: n/a. The region is the same per-environment — pin it to
where your verified identities live.

## SES_TRANSACTIONAL_FROM_EMAIL

Purpose: Sender address for required, account-tied email — verification
links, password resets, paid-tier confirmations, payment receipts.
Selected by `email_app/services/email_classification.py` when the
classification result is `EMAIL_KIND_TRANSACTIONAL` (see
`get_sender_for_kind`). Default Django setting: `noreply@aishippinglabs.com`.

Without it: Falls back to the Django settings default
(`noreply@aishippinglabs.com`). If that address is not verified in
the active SES region, every transactional email errors with
`Email address is not verified` and account flows break.

Where to find it:

- AWS console > SES > "Verified identities" in the active region.
- Direct link:

  ```
  https://console.aws.amazon.com/ses/home#/verified-identities
  ```

- The value must exactly match a verified email (or fall under a
  verified domain). For domain-level verification, any address at
  `@<verified-domain>` works.

Prereqs: The sender must be a verified SES identity in `AWS_SES_REGION`.
Domain verification (DKIM + SPF records published) is strongly
preferred over per-address verification — it covers any address at
the domain and improves deliverability.

Rotation: Safe to rotate.

1. Verify the new identity in SES (domain or address).
2. Update this setting via Studio (Integration settings > Email (SES)
   > `SES_TRANSACTIONAL_FROM_EMAIL`).
3. Window of impact: zero if the old and new identities are both
   verified at the moment of save. If you delete the old identity
   before saving, in-flight transactional sends bounce.

Test vs live: n/a in SES terms — there is one set of verified
identities per region. Use a different verified address (e.g.
`noreply@dev.example.com`) for non-prod environments, and pin it via
the per-environment override.

## SES_PROMOTIONAL_FROM_EMAIL

Purpose: Sender address for marketing email — newsletters, course
campaigns, event announcements. Selected by
`email_app/services/email_classification.py` when the classification
result is `EMAIL_KIND_PROMOTIONAL`. Default: `content@aishippinglabs.com`.

The platform splits transactional and promotional senders so that
suppression-list bounces and complaints on promotional traffic do not
poison the deliverability of account-critical mail.

Without it: Falls back to the Django settings default. If that address
is unverified, promotional sends fail in the same way as transactional
ones — but account flows continue to work because they use the
transactional sender.

Where to find it: Same as `SES_TRANSACTIONAL_FROM_EMAIL` — the SES
"Verified identities" page in the active region.

Prereqs:
- Verified SES identity for this address.
- A separate sub-domain (e.g. `content.<domain>`) is best practice so
  promotional reputation issues stay off the apex domain. The DNS
  records for the sub-domain are separate from the apex.

Rotation: Same flow as `SES_TRANSACTIONAL_FROM_EMAIL`.

Test vs live: n/a beyond per-environment override.

## SES_CONFIGURATION_SET_NAME

Purpose: SES configuration-set name applied to every outbound email
via the `X-SES-CONFIGURATION-SET` header. Read by
`email_app/services/email_service.py:452`. A configuration set is
the SES feature that publishes per-message delivery, open, bounce, and
click events to SNS (and from there to the platform's webhook at
`/api/webhooks/ses-events`).

Without it: Outbound mail still sends — SES does not require a
configuration set. But the platform loses delivery / bounce / open
telemetry. Suppression-list updates from hard bounces stop, so
repeated sends to bad addresses degrade the sending domain's
reputation. Optional, but strongly recommended in production.

Where to find it:

- AWS console > SES > "Configuration sets" > "Create set" in the
  active region.
- Direct link:

  ```
  https://console.aws.amazon.com/ses/home#/configuration-sets
  ```

- After creating the set, add an "Event destination" pointing at an
  SNS topic that fans out to the platform's
  `/api/webhooks/ses-events` endpoint.
- Copy the configuration-set name (a free-form string, e.g.
  `platform-prod-deliveries`) and paste it into Studio.

Prereqs:
- An SNS topic subscribed to the relevant event types
  (`Bounce`, `Complaint`, `Delivery`, `Open`, `Click`).
- An HTTPS subscription on that SNS topic pointing at the platform's
  SES-events webhook (subscription must be confirmed — SNS sends a
  one-time confirmation URL that the webhook view auto-confirms).
- Optionally enable `SES_WEBHOOK_VALIDATION_ENABLED` so the platform
  verifies SNS signatures before processing.

Rotation: n/a. Configuration sets are stable — replace the value only
if you cut over to a new set (e.g. promoting from a staging set to
a production set).

Test vs live: n/a in SES terms. Use a different configuration set per
environment if you want clean telemetry separation, and pin each
environment to its own set via the per-environment override.

## SES_WEBHOOK_VALIDATION_ENABLED

Purpose: When true, the SES events webhook (`api/views/ses_events.py`)
verifies SNS message signatures against AWS's public certificate
before processing. Implemented by
`integrations/services/ses.py:_settings_override_value`. Recommended
in production; off by default to ease local development where the SNS
signing chain is awkward to mock.

Without it (false): The webhook still accepts SNS deliveries but does
not verify their authenticity. In environments where the webhook URL
is internet-reachable, this is a minor attack surface — anyone who
discovers the URL can spoof bounce/complaint events and poison the
suppression list. Set to `true` in production.

Where to find it: Studio-only setting. There is no AWS dashboard to
consult — this is operator intent (production vs lax dev).

Prereqs: Outbound internet access to fetch SNS signing certs from
`sns.<region>.amazonaws.com/SimpleNotificationService-*.pem`.

Rotation: n/a. Toggle on or off; the next webhook delivery uses the
new value.

Test vs live: Set to `true` in production. Leaving it `false` in
production is safe behaviour-wise (the platform still processes
events) but loses the spoofing defence.
