# SES event ingest and shared AWS storage credentials

The website does not send mail through SES. Relay owns outbound rendering and
delivery. The remaining SES code accepts Relay's provider events so the site
can correlate bounces and complaints with historical `EmailLog` records.

The AWS credentials below are used for S3 content, recordings, and downloads.
They are documented here because older deployments grouped them with SES.

## AWS_ACCESS_KEY_ID

Purpose: AWS access key for S3 recording uploads, content image uploads, and
the GitHub App key fallback.

Without it: new recording and content uploads fail. Existing CDN objects and
outbound mail are unaffected.

Configure the key in Studio under S3 Content Images. Rotate it together with
`AWS_SECRET_ACCESS_KEY`, keeping the old pair active until the new pair has
been verified.

## AWS_SECRET_ACCESS_KEY

Purpose: secret paired with `AWS_ACCESS_KEY_ID` for S3 requests.

Without it: S3 requests fail with `NoCredentialsError`. Outbound mail is
unaffected.

AWS displays this value only when the access key is created. Store it as a
secret and rotate the pair together.

## SES_WEBHOOK_VALIDATION_ENABLED

Purpose: verify the SNS signing certificate and signature on SES bounce and
complaint notifications received at `/api/ses-events`.

Without it: the endpoint still applies the optional shared-secret check, but
does not verify the SNS signature. Keep validation enabled in production.

The event endpoint accepts SNS subscription confirmation plus SES bounce and
complaint notifications. It stores the provider event and updates the matching
site recipient state. This inbound path is independent from outbound Relay
template and send APIs.

## SES_WEBHOOK_SHARED_SECRET

Purpose: require the exact secret in the `X-SES-Webhook-Secret` request header
before processing an event.

Without it: no shared-secret header is required. This is convenient for local
replay; production should configure a secret on both the event forwarder and
the website.

Relay's production `aisl-ses-ingress` service reads the value from Secrets
Manager and sends it with the unchanged SNS envelope. Rotate both sides in one
release window. Never print the value in logs or issue comments.

SES publishes the provider events to SNS, which fans them into the dedicated
`aisl-ses-webhooks` SQS queue. Relay drains that queue and POSTs to the website.
The website remains the sole owner of `SesEvent` and recipient mutations. Relay
deletes a queue message only after the website returns HTTP 200 with `ok` or
`duplicate`; failures retry through SQS and its DLQ.

## Historical correlation fields

`EmailLog.ses_message_id`, `SesEvent`, and SES-named event API fields remain
intentionally. They store provider identifiers already present in historical
records and correlate current Relay delivery events with those records.
Renaming them would be a data and public API migration unrelated to removing
the old site-owned send path.

For outbound senders, campaign settings, and template operations, see
[`relay.md`](relay.md).
