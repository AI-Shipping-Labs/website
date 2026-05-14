# Operator Configuration Guide

Audience: operators bringing up a fresh AI Shipping Labs environment. This guide takes you from "code is deployed, DB is migrated, DNS is pointed at the load balancer" to "every feature works (login, payments, email, Slack, content sync, recordings, events)". It assumes cloud provisioning (ECS / RDS / S3 / CloudFront / IAM) is already done; if you need that, see `_docs/setup.md`. Voice is imperative — do this, then this. Replace `{SITE_BASE_URL}` with your actual base URL (for example `https://aishippinglabs.com`).

Resolver order for integration settings: Studio (DB) overrides Django settings, and Django settings are usually a process-start snapshot of env vars from `website/settings.py`. If a key is listed in `Studio > Settings`, use Studio for normal operator changes unless a section below explicitly calls out an environment-variable gate.

## 1. Platform environment variables

These variables are read at process start, before the DB is reachable, used directly by Django/ECS, or deliberately excluded from `integrations/settings_registry.py`. They CANNOT be set through Studio. Configure them in the platform that runs the container (ECS task definition, AWS Secrets Manager, GitHub Actions, Docker Compose, or `.env` for local).

| Variable | Required when | Notes |
|----------|---------------|-------|
| `SECRET_KEY` | always in prod | Required when `DEBUG=false`. The app raises `ImproperlyConfigured` and exits at import time if it is unset, empty, or equal to the in-tree dev fallback. Generate with `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`. |
| `DEBUG` | always | Must be `false` in prod. Truthy values: `1`, `true`, `yes`. Anything else (including empty) is falsy. |
| `DATABASE_URL` | always in prod | PostgreSQL connection string parsed by `dj_database_url`. Default is a local SQLite file. Inject via ECS Secrets Manager in deployed environments. |
| `ALLOWED_HOSTS` | always | Comma-separated. Default `localhost,127.0.0.1`. Production must list every host that serves the site (e.g. `aishippinglabs.com,www.aishippinglabs.com,prod.aishippinglabs.com`). |
| `CSRF_TRUSTED_ORIGINS` | always over HTTPS | Comma-separated full origins with scheme (e.g. `https://aishippinglabs.com,https://www.aishippinglabs.com`). Required for any POST over HTTPS — login, forms, and Studio save buttons all break without it. |
| `SES_ENABLED` | prod email | Must be `true` to send transactional or campaign email. Defaults `false`; forced false under `manage.py test`. `manage.py check --fail-level ERROR` fails in `DEBUG=False` when this is missing. |
| `S3_ENABLED` | prod content images | Must be `true` to upload synced content images to S3. Defaults `false`; forced false under `manage.py test`. Existing CDN URLs continue to resolve, but new image uploads are skipped. |
| `SLACK_ENABLED` | prod Slack bot/imports | Must be `true` at process start to let Slack bot/import credentials survive settings import. Studio also has a `SLACK_ENABLED` key, but it cannot undo the startup blanking when this env var is false. |
| `VERSION` | optional | Build tag shown in the page footer. Set automatically by deploy scripts. |
| `RUN_MIGRATIONS` | ECS only | `true` on the web container, `false` on the worker container. The single entrypoint runs migrations only when this is `true`; `deploy/update_task_def.py` maintains it. |
| `Q_WORKERS` | optional | Worker count for django-q. Defaults to 1 on SQLite, 2 on Postgres. |
| `EXPECT_WORKER` | optional | Set `false` only for one-off environments that intentionally have no django-q worker; suppresses worker liveness warnings/banners. Defaults `true`. |
| `IP_HASH_SALT` | optional | Salt for SHA-256 hashing client IPs in `CampaignVisit.ip_hash`. Empty leaves `ip_hash` blank. |
| `ANALYTICS_COOKIE_DOMAIN` | optional | Scope analytics cookies to a domain. Defaults to `SESSION_COOKIE_DOMAIN`. |
| `EMAIL_BATCH_SIZE` | optional | Recipients per chunked `send_campaign_batch` task. Default 200. |
| `IMPORT_WELCOME_EMAILS_PER_HOUR` | optional | Rate limit for imported-user welcome emails. Default 50. |
| `SES_FROM_EMAIL` | optional legacy email fallback | Legacy fallback sender used only when the explicit transactional/promotional sender keys are not configured. Not rendered in Studio; prefer `SES_TRANSACTIONAL_FROM_EMAIL` and `SES_PROMOTIONAL_FROM_EMAIL` in Studio. |
| `SES_UNSUBSCRIBE_EMAIL` | optional email header | Optional mailto address for the `List-Unsubscribe` email header. Not rendered in Studio. |
| `SYNC_QUEUED_THRESHOLD_MINUTES` | optional | Watchdog: a sync stuck in `queued` longer than this is flipped to `failed`. Default 10. |
| `SYNC_RUNNING_THRESHOLD_MINUTES` | optional | Watchdog: a sync stuck in `running` longer than this is flipped to `failed`. Default 30. |
| `LOGIN_API_SLOW_MS` | optional | Slow-login instrumentation threshold in milliseconds. Default 750. |
| `GITHUB_APP_PRIVATE_KEY_FILE` | optional | Path to a PEM file. Takes precedence over the `GITHUB_APP_PRIVATE_KEY` env var. The app falls back to the Studio-configured AWS Secrets Manager secret path if neither is set. |
| `DJANGO_TEST_DB_NAME` | CI/test only | Makes SQLite tests file-backed so `--keepdb` can cache test databases. Used by GitHub Actions. |
| `Q_SYNC` | test/debug only | Runs django-q tasks synchronously when set to `true`. Do not set in ECS services. |

Test: `curl -I {SITE_BASE_URL}/ping` returns `200 OK`. Then visit `{SITE_BASE_URL}/` and confirm the home page renders without 500 errors.

`SITE_BASE_URL` is also written into ECS task definitions by `deploy/update_task_def.py` as the process-start baseline. Unlike the variables above, it is also a Studio setting; use Studio > Settings > Site for normal runtime URL changes after boot.

## 2. Sign in to Studio

1. Visit `{SITE_BASE_URL}/studio/`.
2. First-time on a fresh DB: open an SSH tunnel to the bastion and run `uv run python manage.py createsuperuser` against the remote database. The bastion-tunnel and `DATABASE_URL` recipe is in `_docs/setup.md` — see "Database access" and "Creating admin users".
3. Sign in at `{SITE_BASE_URL}/accounts/login/` with the superuser email + password.
4. Open `{SITE_BASE_URL}/studio/settings/`. Every integration group from `INTEGRATION_GROUPS` is rendered there with a status badge (`configured`, `partial`, `not_configured`).

Test: visit `/studio/settings/` and confirm 10 integration groups are listed (Stripe, Zoom, Email (SES), S3 Recordings, S3 Content Images, YouTube, GitHub App, Slack, Site, Auth).

## 3. OAuth login providers

Configure in Studio > Settings > Auth & Login. Each provider has its own card with the callback URL ready to copy + the developer-console link.

Per provider, fill in: Provider, Name, Client id, Secret key, and assign your site (`aishippinglabs.com` or equivalent) to the "Chosen sites" box.

| Provider | Console URL | Callback URL | Scopes |
|----------|-------------|--------------|--------|
| Google | `https://console.cloud.google.com/apis/credentials` | `{SITE_BASE_URL}/accounts/google/login/callback/` | `profile`, `email` |
| GitHub | `https://github.com/settings/developers` (OAuth Apps tab — NOT GitHub Apps) | `{SITE_BASE_URL}/accounts/github/login/callback/` | `user:email` |
| Slack | `https://api.slack.com/apps` (Create New App > From scratch) | `{SITE_BASE_URL}/accounts/slack/login/callback/` | `openid`, `profile`, `email` |

### 3.1 Google

1. Open `https://console.cloud.google.com/apis/credentials`.
2. Create an OAuth 2.0 Client ID, type "Web application".
3. Add `{SITE_BASE_URL}/accounts/google/login/callback/` to "Authorized redirect URIs".
4. Save in Studio > Settings > Auth & Login > Google.

Foot-gun: the trailing slash on the redirect URI is REQUIRED. `redirect_uri_mismatch` errors at login time mean the slash is missing.

Test: visit `{SITE_BASE_URL}/accounts/login/`, click "Sign in with Google", complete the OAuth dance, confirm a `User` row is created (visible in Studio > Users).

### 3.2 GitHub

1. Open `https://github.com/settings/developers`, "OAuth Apps" tab.
2. Click "New OAuth App". Set "Authorization callback URL" to `{SITE_BASE_URL}/accounts/github/login/callback/`.
3. Generate a client secret. Save in Studio > Settings > Auth & Login > GitHub.

Foot-gun: GitHub OAuth apps allow only ONE callback URL per app. Create a separate OAuth app per environment (local, dev, prod). This is a different app from the GitHub APP used for content sync (section 7).

Test: visit `{SITE_BASE_URL}/accounts/login/`, click "Sign in with GitHub", complete the OAuth dance, confirm a `User` row is created.

### 3.3 Slack

1. Open `https://api.slack.com/apps`, click "Create New App > From scratch".
2. Under "OAuth & Permissions", add `{SITE_BASE_URL}/accounts/slack/login/callback/` to "Redirect URLs".
3. Under "User Token Scopes", add `openid`, `profile`, `email`.
4. Copy the Client ID and Client Secret from "Basic Information" and save in Studio > Settings > Auth & Login > Slack.

Foot-gun: this is a different Slack app from the BOT used for community posting (section 6). The login app needs `openid`, `profile`, `email`. The bot needs `chat:write`, `channels:read`, etc. Two Slack apps, two sets of credentials.

Test: visit `{SITE_BASE_URL}/accounts/login/`, click "Sign in with Slack", complete the OAuth dance, confirm a `User` row is created.

### 3.4 Email-signup account lifecycle

Studio path: `Studio > Settings > Auth`.

| Key | Source | Notes |
|-----|--------|-------|
| `UNVERIFIED_USER_TTL_DAYS` | non-secret | Days an email-signup account survives without verifying. Default 7. The daily `purge-unverified-users` job hard-deletes expired rows that have no related activity (no `last_login`, no Stripe customer, no `EmailLog` / project / submission rows). The companion `remind-unverified-users` job sends a one-shot reminder ~24 hours before the window closes. Social-login signups are auto-verified by the OAuth provider and never enter this lifecycle. Issue #452. |

Foot-gun: lowering this value retroactively shortens the window for users already in the queue. Existing rows from before the issue #452 migration have `verification_expires_at` set to NULL and are NOT subject to purge — only signups created after the migration are.

Test: register a new email-only account, confirm `verification_expires_at` is populated on the User row in Studio. After `UNVERIFIED_USER_TTL_DAYS` days without verification, the daily purge cleans the row.

## 4. Stripe (payments)

Studio path: `Studio > Settings > Stripe`.

Provider consoles:

- API keys: `https://dashboard.stripe.com/apikeys`
- Webhooks: `https://dashboard.stripe.com/webhooks`
- Customer portal: `https://dashboard.stripe.com/settings/billing/portal`

Keys to set in Studio:

| Key | Source | Notes |
|-----|--------|-------|
| `STRIPE_SECRET_KEY` | secret | Stripe Dashboard > Developers > API keys. |
| `STRIPE_WEBHOOK_SECRET` | secret | Stripe Dashboard > Webhooks > select endpoint > Signing secret. |
| `STRIPE_CUSTOMER_PORTAL_URL` | non-secret | Customer portal URL from the Billing settings page. |
| `STRIPE_DASHBOARD_ACCOUNT_ID` | non-secret | Optional. Stripe account ID (e.g. `acct_1T1mfGB7mZrgL7H5`) used to deep-link the per-user Stripe icon on `/studio/users/` to `https://dashboard.stripe.com/<acct>/customers/<cus_id>`. Find it in the Stripe URL when signed in. When blank the icon renders without a link. |

Webhook endpoint to register in Stripe: `{SITE_BASE_URL}/api/webhooks/payments` (no trailing slash).

Webhook events: see #113 / Stripe documentation for the exact event list. Minimum events: `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.payment_failed`.

Operator payment notification (issue #645): set `PAYMENT_NOTIFICATION_EMAIL` under `Studio > Settings > Site` to receive an internal email whenever a Stripe checkout completes (new paid signup, tier upgrade, or course purchase). The setting is non-secret, plain string, and has NO hard-coded default — leave it blank to disable notifications entirely; populated with an address it sends one plain-text mail per non-duplicate webhook from `DEFAULT_FROM_EMAIL`. Failures to send are logged at WARNING and never break the webhook handler. Idempotency rides on the existing `WebhookEvent` row guard, so Stripe retries of the same event never produce duplicate emails.

Test: visit `{SITE_BASE_URL}/pricing`, click a paid tier, complete checkout in Stripe test mode, confirm the user's tier updates on `{SITE_BASE_URL}/account/`.

## 5. Email (Amazon SES)

Studio path: `Studio > Settings > Email (SES)`.

Provider consoles:

- IAM (for credentials): `https://console.aws.amazon.com/iam/`
- SES (for verified senders + sandbox status): `https://console.aws.amazon.com/ses/`

Production deploys MUST set the env var `SES_ENABLED=true` to actually send mail. The flag defaults to `false` everywhere — local dev, CI, Playwright, `manage.py test` — so transactional and campaign mail short-circuits with a synthetic `ses-disabled-noop` message id and no boto3 call is made. Without `SES_ENABLED=true` in prod, no user-facing email leaves the host (issue #509).

Deploy-time guard: a Django system check (`email_app.E001`) makes `manage.py check` exit non-zero whenever `DEBUG=False` and `SES_ENABLED` is not `true`, so a production deploy that runs `manage.py check` as a pre-flight step blocks before the new container is promoted. The check is silent in local dev (`DEBUG=True`) and under `manage.py test` (the test runner sets the `TESTING` flag). To intentionally run a production-like environment without SES (for example a staging box that should not send real mail), silence the check with `SILENCED_SYSTEM_CHECKS = ['email_app.E001']` in settings. Issue #521.

Keys to set in Studio:

| Key | Source | Notes |
|-----|--------|-------|
| `AWS_ACCESS_KEY_ID` | secret | IAM user with `ses:SendEmail` and `ses:SendRawEmail`. Force-blanked when `SES_ENABLED=false` so any code path that slips past the gate cannot authenticate. |
| `AWS_SECRET_ACCESS_KEY` | secret | Paired with the access key ID. Same force-blank as above. |
| `AWS_SES_REGION` | non-secret | e.g. `us-east-1`, `eu-west-1`. |
| `SES_TRANSACTIONAL_FROM_EMAIL` | non-secret | Sender for required account/service email. Defaults to `noreply@aishippinglabs.com`; must be a verified sender (or be on a verified domain) in SES. |
| `SES_PROMOTIONAL_FROM_EMAIL` | non-secret | Sender for campaigns/newsletters/marketing email. Defaults to `content@aishippinglabs.com`; must be a verified sender (or be on a verified domain) in SES. |
| `SES_CONFIGURATION_SET_NAME` | non-secret | Optional SES configuration set name for delivery, open, and click event publishing. |
| `SES_WEBHOOK_VALIDATION_ENABLED` | non-secret | `true` in prod to validate incoming SNS webhook signatures. |

Webhook endpoint (for SES bounce/complaint/open/click notifications via SNS): `{SITE_BASE_URL}/api/ses-events` (no trailing slash; the slashless form avoids the trailing-slash redirect that strips POST bodies). Configure in your SNS topic subscription.

SES bounce / complaint webhook setup (issue #453):

1. Create an SNS topic, e.g. `ses-bounces-prod` (region must match `AWS_SES_REGION`).
2. SES console -> Configuration -> Configuration sets (or Email destinations) -> enable Bounce and Complaint notifications and point them at the SNS topic. Delivery notifications are optional; the webhook accepts and logs them but takes no action.
3. SNS console -> Subscriptions -> Create subscription -> Protocol `HTTPS`, Endpoint `https://aishippinglabs.com/api/ses-events`.
4. AWS posts a `SubscriptionConfirmation` to that endpoint; our webhook auto-confirms by fetching the `SubscribeURL`. The subscription state in SNS will flip from `PendingConfirmation` to `Confirmed` once that succeeds.
5. Verify by sending an email to the SES `mailbox-simulator` address `bounce@simulator.amazonses.com`; the recipient User row should flip to `unsubscribed=True` and pick up the `bounced` tag, and a row should appear in the `email_app.SesEvent` audit table.

The webhook validates the SNS signature using `integrations.services.ses.validate_sns_notification`. In production keep `SES_WEBHOOK_VALIDATION_ENABLED=true`; in development the default (`DEBUG=True`) skips signature checks so local SNS replay is possible.

SES engagement tracking setup (issue #454):

1. SES console -> Configuration Sets -> create a configuration set, e.g. `ais-engagement-prod`.
2. Add an event destination that publishes to the SNS topic subscribed to `{SITE_BASE_URL}/api/ses-events`.
3. Tick `Open`, `Click`, `Bounce`, `Complaint`, and `Delivery`.
4. Save the configuration set name in Studio -> Settings -> Email (SES) -> `SES_CONFIGURATION_SET_NAME`.
5. Send a campaign test email and confirm the matching `EmailLog` row records `opened_at` / `clicked_at` after SES publishes engagement events.

Soft-bounce policy: a `Transient` (soft) bounce increments `User.soft_bounce_count`. The third soft bounce in a row flips `unsubscribed=True`, appends the `bounced` tag, and resets the counter. A single `Complaint` (Gmail "Report spam" etc.) unsubscribes immediately and tags `complained`.

Bounce / complaint correlation (issue #495): the webhook looks up the originating `EmailLog` by inner SES `mail.messageId` and stamps `bounced_at`, `bounce_type`, `bounce_subtype`, and `bounce_diagnostic` on that row (and `complained_at` for complaints). The `SesEvent` audit row also gets a direct `email_log` FK plus normalized bounce-classification columns. This lets staff answer "did this bounce come from a campaign, signup verification, verification reminder, or lead-magnet email?" by reading the `EmailLog` or filtering `SesEvent` in admin. Hard bounces with sub-type `NoEmail` (the SES "mailbox does not exist" classification) carry the receiver's diagnostic in `bounce_diagnostic` for triage. Events whose `mail.messageId` does not match any `EmailLog` are still logged for audit and return 200, so SNS does not retry forever and we do not create a feedback loop on spam-trap traffic.

Payload-shape support: the webhook accepts both the SES identity-notification shape (`notificationType`) and the configuration-set event-publishing shape (`eventType`) for `Bounce`, `Complaint`, `Delivery`, `Open`, and `Click`. Do not enable BOTH identity notifications AND a configuration-set destination for the same event type unless you understand the deduplication implications — SES will emit one of each, both will hit `/api/ses-events`, and although our SNS-`MessageId` dedup catches identical replays, two distinct AWS-side notifications carry distinct `MessageId`s and will both be processed. Pick one publishing path per event type.

Foot-gun: SES is in sandbox mode by default. Sandbox accounts can only send to verified addresses. Request production access via the AWS console BEFORE the launch — approval can take 24+ hours.

Test: in `Studio > Campaigns`, create a test campaign and send to a verified address; confirm delivery.

## 6. Slack (community bot)

Studio path: `Studio > Settings > Slack`. Also requires `SLACK_ENABLED=true` at the platform level (section 1) — without it the bot tokens are blanked at startup.

Provider console: `https://api.slack.com/apps` (your bot app, NOT the login app from section 3.3).

Keys to set in Studio:

| Key | Source | Notes |
|-----|--------|-------|
| `SLACK_ENABLED` | non-secret | `true` to enable. Off by default. |
| `SLACK_ENVIRONMENT` | non-secret | `production`, `development`, or `test`. Production channel IDs are used only when this is `production`; dev/test require their own channel IDs. |
| `SLACK_BOT_TOKEN` | secret | "Bot User OAuth Token" — starts with `xoxb-`. |
| `SLACK_COMMUNITY_CHANNEL_IDS` | non-secret | Production comma-separated community channel IDs (the `C0…` IDs, NOT names). |
| `SLACK_ANNOUNCEMENTS_CHANNEL_ID` | non-secret | Production channel ID for #announcements. |
| `SLACK_DEV_COMMUNITY_CHANNEL_IDS` | non-secret | Development-only community channel IDs. If empty in development, community channel mutations are skipped. |
| `SLACK_DEV_ANNOUNCEMENTS_CHANNEL_ID` | non-secret | Development-only announcement channel ID. If empty in development, announcement posting is skipped. |
| `SLACK_TEST_COMMUNITY_CHANNEL_IDS` | non-secret | Test-only community channel IDs, only for explicitly opted-in integration tests. |
| `SLACK_TEST_ANNOUNCEMENTS_CHANNEL_ID` | non-secret | Test-only announcement channel ID, expected to be `C0AHN84QNP3` for #integration-tests. |
| `SLACK_ANNOUNCEMENTS_CHANNEL_NAME` | non-secret | Display name (e.g. `#announcements`). |
| `SLACK_INVITE_URL` | non-secret | Public Slack invite link shown to new members. |

Bot scopes (Slack app > OAuth & Permissions > Bot Token Scopes): minimum `chat:write`, `channels:read`. Add more as features need them.

Foot-gun: this is the Slack BOT app, separate from the Slack OAuth LOGIN app in section 3.3. Two Slack apps, two sets of credentials. Also keep production, development, and test bot channels separate; `SLACK_ENVIRONMENT=development` and `SLACK_ENVIRONMENT=test` intentionally ignore the production channel IDs.

Test: in Studio, trigger an announcement (e.g. publish an article and use the "Announce on Slack" action) and confirm the bot posted to the configured channel.

## 7. GitHub App (content sync)

Studio path: `Studio > Settings > GitHub App`.

Provider console: `https://github.com/organizations/AI-Shipping-Labs/settings/apps` (or your org's apps page).

Keys to set in Studio:

| Key | Source | Notes |
|-----|--------|-------|
| `GITHUB_APP_ID` | non-secret | Numeric App ID from the App's settings page. |
| `GITHUB_APP_INSTALLATION_ID` | non-secret | Installation ID — visible in the URL of the org's installation page. |
| `GITHUB_APP_PRIVATE_KEY_SECRET_ID` | non-secret | AWS Secrets Manager secret name, path, or ARN containing the PEM. Default fallback is `ai-shipping-labs/github-app-private-key`. |
| `GITHUB_APP_PRIVATE_KEY_SECRET_REGION` | non-secret | AWS region for the secret. Defaults to `eu-west-1` when empty. |
| `GITHUB_APP_PRIVATE_KEY` | secret, multiline | Optional direct PEM paste. Prefer the Secrets Manager path above for production. |

Webhook endpoint: `{SITE_BASE_URL}/api/webhooks/github` (no trailing slash). Configure in the App's "Webhook" tab.

Recommended app installation setting: "All repositories". Any new content repo in the org becomes syncable without revisiting installation settings.

Foot-gun: this is the GitHub APP for content sync, not the GitHub OAuth APP for user login (section 3.2). Two GitHub apps, two sets of credentials.

Test: in `Studio > Sync`, click "Sync now" on a content source; confirm the sync run completes and articles appear at `{SITE_BASE_URL}/blog/`.

## 8. S3 — content images

Studio path: `Studio > Settings > S3 Content Images`.

Provider console: AWS S3 + CloudFront. Bucket policy, CORS, and Origin Access Control details are in `_docs/content-images-s3.md` — follow that document for the bucket and CloudFront setup before filling in the keys here.

Production deploys MUST set the env var `S3_ENABLED=true` to actually upload images to S3 during content sync. The flag defaults to `false` everywhere — local dev, CI, Playwright, `manage.py test` — so `upload_images_to_s3` short-circuits before constructing any boto3 client and returns a no-op stats dict. Without `S3_ENABLED=true` in prod, content sync still runs but image uploads are skipped (the markdown still resolves to the configured `CONTENT_CDN_BASE`, so existing CDN images keep working). Issue #532.

`S3_ENABLED` is a platform environment variable, not a Studio key.

Keys to set in Studio:

| Key | Source | Notes |
|-----|--------|-------|
| `AWS_S3_CONTENT_BUCKET` | non-secret | Bucket name (e.g. `aishippinglabs-content`). |
| `AWS_S3_CONTENT_REGION` | non-secret | Region of the bucket (e.g. `eu-west-1`). |
| `CONTENT_CDN_BASE` | non-secret | Public base URL — typically the CloudFront distribution (e.g. `https://cdn.aishippinglabs.com`). Default `/static/content-images` (local dev only). |

Note: S3 credentials are shared with SES — the same `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` from section 5 must have `s3:PutObject` on this bucket. Both `SES_ENABLED=false` and `S3_ENABLED=false` blank these credentials at startup as a belt-and-suspenders guard against any code path that slips past the gate.

Test: in `Studio > Sync`, sync a content source that has images; confirm an image URL on `{SITE_BASE_URL}/blog/<article>` resolves to a `cdn.aishippinglabs.com` (or your CloudFront) URL and returns 200.

## 9. S3 — event recordings

Studio path: `Studio > Settings > S3 Recordings`.

Keys to set in Studio:

| Key | Source | Notes |
|-----|--------|-------|
| `AWS_S3_RECORDINGS_BUCKET` | non-secret | Bucket name. |
| `AWS_S3_RECORDINGS_REGION` | non-secret | Region of the bucket (e.g. `eu-central-1`). |

Note: the recordings bucket is separate from the content bucket. Recordings tend to be larger and may need different lifecycle / region rules.

Test: upload a recording to a workshop in `Studio > Recordings` and confirm the file lands in the S3 bucket.

## 10. Zoom (events)

Studio path: `Studio > Settings > Zoom`.

Provider console: `https://marketplace.zoom.us/develop/create` (Server-to-Server OAuth app).

Keys to set in Studio:

| Key | Source | Notes |
|-----|--------|-------|
| `ZOOM_CLIENT_ID` | secret | Server-to-Server OAuth app client ID. |
| `ZOOM_CLIENT_SECRET` | secret | Same app's client secret. |
| `ZOOM_ACCOUNT_ID` | secret | Zoom account ID (visible on the app's app credentials page). |
| `ZOOM_WEBHOOK_SECRET_TOKEN` | secret | Token for verifying inbound webhook signatures. |

Webhook endpoint: `{SITE_BASE_URL}/api/webhooks/zoom` (no trailing slash). Configure in the Zoom app's "Feature > Event Subscriptions" tab.

Test: in `Studio > Events`, create an event with platform=zoom; confirm a join URL is generated and the meeting exists in your Zoom account.

## 11. YouTube (recording uploads)

Studio path: `Studio > Settings > YouTube`.

Provider console: Google Cloud Console (the same project can host both the OAuth Login client from section 3.1 and the YouTube Data API client, as separate OAuth clients).

Keys to set in Studio:

| Key | Source | Notes |
|-----|--------|-------|
| `YOUTUBE_CLIENT_ID` | secret | OAuth 2.0 client ID. |
| `YOUTUBE_CLIENT_SECRET` | secret | OAuth 2.0 client secret. |
| `YOUTUBE_REFRESH_TOKEN` | secret | One-time long-lived refresh token. Generate via OAuth Playground (`https://developers.google.com/oauthplayground/`) using your client ID/secret and the `https://www.googleapis.com/auth/youtube.upload` scope. In OAuth Playground, click the gear icon (top right), check `Use your own OAuth credentials`, and paste your client ID + secret there before authorizing. |

Test: in `Studio > Recordings`, click "Publish to YouTube" on a recording; confirm an upload starts and the resulting YouTube URL is stored on the recording.

## 12. End-to-end smoke test

Run this checklist after configuring everything. Each item is one click, end to end.

- [ ] Sign in with Google, GitHub, and Slack — all three create a `User` row visible in `/studio/users/`.
- [ ] Sign out, then visit a gated article at `{SITE_BASE_URL}/blog/<gated-article-slug>`; confirm a paywall renders with a working `View Pricing` link that lands on `/pricing`.
- [ ] Subscribe to the newsletter on the home page; receive the welcome email at the subscribed address.
- [ ] Upgrade to a paid tier in Stripe test mode (`/pricing`); the user's tier reflects on `/account/`.
- [ ] As a paid member, cancel the subscription via `{SITE_BASE_URL}/account/` (or the Stripe customer portal); confirm the user's tier on `/account/` drops to Free within a few seconds (Stripe webhook `customer.subscription.deleted` reaches `/api/webhooks/payments`).
- [ ] Trigger a content sync at `/studio/sync/`; new articles appear at `/blog/` and their images load from the CDN domain.
- [ ] Create an event with platform=zoom in `/studio/events/`; the join URL points at zoom.us and the meeting exists in the Zoom account.
- [ ] Upload a workshop recording to S3 via `/studio/recordings/<id>/edit`; click "Publish to YouTube"; the YouTube URL is stored.
- [ ] Trigger a Slack announcement from `/studio/articles/<id>/announce-slack/`; the bot posts to the configured channel.
- [ ] Send a test email campaign in `/studio/campaigns/`; confirm delivery to a verified SES recipient.

## 13. Where to look when something is wrong

- `Studio > Worker` (`/studio/worker/`) — django-q heartbeats and queue depth. If the worker is "NOT running", background sync / email / Slack jobs will queue forever.
- `Studio > Sync > history` (`/studio/sync/history/`) — last sync attempts with success / failure status.
- `Studio > Notifications` (`/studio/notifications/`) — recent notification log (Slack, email, push).
- `Studio > Settings` (`/studio/settings/`) — every group's status badge. A "partial" badge means some keys are set and others aren't.
- Server logs — depends on hosting. ECS: CloudWatch log group for the service. Local: stdout from `runserver`.
- DB inspection — `IntegrationSetting` table holds Studio-saved values (encrypted secrets are still readable by Django; treat the table as sensitive).

## Future updates

This doc is locked against TODAY's codebase. As the linked issues ship, update the corresponding lines.

| Trigger | What changes in the doc |
|---------|-------------------------|
| #321 ships | Done — section 1 mentions Studio's host-mismatch banner; the legacy `SITE_URL` setting was deleted. |
| #322 ships | Done — section 3 points to Studio > Settings > Auth & Login; the legacy "configure via Django admin" path was removed. |
| #323 ships | Add a one-liner in section 13: "You can export and import settings via Studio (with a secret-handling policy)." |
| #324 ships | Add one sentence in section 1: "Studio shows a source badge per field — `db` or `env` — so you can see which value is winning." |
