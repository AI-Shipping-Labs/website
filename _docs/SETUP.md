# Setup Guide

How to configure all third-party integrations for the AI Shipping Labs platform.

All secrets are set as environment variables (e.g. in `.env` file). Never commit secrets to the repo.

## Environment Variables

```bash
# Django
SECRET_KEY=your-django-secret-key
DEBUG=true

# Database (default: SQLite, no config needed for local dev)
# DATABASE_URL=postgres://user:pass@localhost:5432/aishippinglabs

# --- Google OAuth (Login with Google) ---
GOOGLE_OAUTH_CLIENT_ID=
GOOGLE_OAUTH_CLIENT_SECRET=

# --- GitHub OAuth (Login with GitHub) ---
GITHUB_OAUTH_CLIENT_ID=
GITHUB_OAUTH_CLIENT_SECRET=

# --- GitHub App (Private repo content sync) ---
GITHUB_APP_ID=
GITHUB_APP_PRIVATE_KEY=
GITHUB_APP_INSTALLATION_ID=

# --- Slack ---
SLACK_OAUTH_CLIENT_ID=
SLACK_OAUTH_CLIENT_SECRET=
SLACK_BOT_TOKEN=
SLACK_COMMUNITY_CHANNEL_IDS=C12345,C67890
SLACK_INVITE_URL=https://join.slack.com/t/your-workspace/shared_invite/...
SLACK_ANNOUNCEMENTS_CHANNEL_ID=

# --- Stripe ---
STRIPE_SECRET_KEY=sk_test_...
STRIPE_PUBLISHABLE_KEY=pk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...

# --- Zoom ---
ZOOM_CLIENT_ID=
ZOOM_CLIENT_SECRET=
ZOOM_ACCOUNT_ID=
ZOOM_WEBHOOK_SECRET_TOKEN=

# --- AWS (SES email + S3 recordings) ---
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_SES_REGION=us-east-1
AWS_S3_RECORDINGS_BUCKET=
AWS_S3_RECORDINGS_REGION=eu-central-1
SES_FROM_EMAIL=community@aishippinglabs.com

# --- YouTube ---
YOUTUBE_CLIENT_ID=
YOUTUBE_CLIENT_SECRET=
YOUTUBE_REFRESH_TOKEN=

# --- Content CDN ---
CONTENT_CDN_BASE=/static/content-images
```

---

## Google OAuth (Login with Google)

Used for: user login/registration via Google account.

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project (or use existing)
3. Go to APIs & Services > Credentials
4. Click "Create Credentials" > "OAuth 2.0 Client ID"
5. Application type: Web application
6. Authorized redirect URIs: `http://localhost:8000/accounts/google/login/callback/` (local), `https://aishippinglabs.com/accounts/google/login/callback/` (production)
7. Copy Client ID and Client Secret

```bash
GOOGLE_OAUTH_CLIENT_ID=123456789.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=GOCSPX-...
```

---

## GitHub OAuth (Login with GitHub)

Used for: user login/registration via GitHub account.

1. Go to [GitHub Developer Settings](https://github.com/settings/developers)
2. Click "New OAuth App"
3. Application name: AI Shipping Labs
4. Homepage URL: `http://localhost:8000` (local) or `https://aishippinglabs.com` (production)
5. Authorization callback URL: `http://localhost:8000/accounts/github/login/callback/` (local), `https://aishippinglabs.com/accounts/github/login/callback/` (production)
6. Copy Client ID, then generate a Client Secret

```bash
GITHUB_OAUTH_CLIENT_ID=Ov23li...
GITHUB_OAUTH_CLIENT_SECRET=abc123...
```

---

## GitHub App (Content Sync from Private Repos)

Used for: syncing content (articles, courses, resources, projects) from the private `AI-Shipping-Labs/content` repo.

### Create the GitHub App

1. Go to [AI-Shipping-Labs org settings](https://github.com/organizations/AI-Shipping-Labs/settings/apps)
2. Click "New GitHub App"
3. App name: `AI Shipping Labs Content Sync` (must be globally unique)
4. Homepage URL: `https://aishippinglabs.com`
5. Uncheck "Webhook" > "Active" (we use our own webhook endpoint)
6. Permissions > Repository permissions:
   - Contents: Read-only
   - Metadata: Read-only
7. Where can this app be installed: "Only on this account"
8. Click "Create GitHub App"
9. Note the App ID (shown at the top of the app settings page)

### Generate a Private Key

1. On the app settings page, scroll to "Private keys"
2. Click "Generate a private key"
3. A `.pem` file will download
4. Set the contents as `GITHUB_APP_PRIVATE_KEY` (see below)

### Install the App

1. On the app settings page, click "Install App" in the left sidebar
2. Select the `AI-Shipping-Labs` organization
3. Choose "Only select repositories" and select `content`
4. Click "Install"
5. After install, the URL will be `https://github.com/organizations/AI-Shipping-Labs/settings/installations/XXXXXXXX` — the number at the end is the Installation ID

### Set Environment Variables

```bash
GITHUB_APP_ID=123456
GITHUB_APP_INSTALLATION_ID=78901234
# For the private key, either paste the PEM content directly (with literal \n for newlines)
# or use a file path approach depending on your deployment
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----"
```

### Set Up the Webhook (optional, for auto-sync on push)

1. Go to [repo settings](https://github.com/AI-Shipping-Labs/content/settings/hooks)
2. Click "Add webhook"
3. Payload URL: `https://aishippinglabs.com/api/webhooks/github`
4. Content type: `application/json`
5. Secret: generate a random string, then set it as `webhook_secret` on the ContentSource in the database
6. Events: select "Just the push event"
7. Click "Add webhook"

Without the webhook, you can still trigger syncs manually from the admin dashboard at `/admin/sync/`.

---

## Stripe (Payments)

Used for: tier subscriptions (basic, main, premium), billing portal.

### Create a Stripe Account

1. Go to [Stripe Dashboard](https://dashboard.stripe.com/)
2. Create an account or use existing

### Get API Keys

1. Go to Developers > API keys
2. Copy the Publishable key and Secret key (use test keys for local dev)

```bash
STRIPE_SECRET_KEY=sk_test_...
STRIPE_PUBLISHABLE_KEY=pk_test_...
```

### Set Up Webhook

1. Go to Developers > Webhooks
2. Click "Add endpoint"
3. Endpoint URL: `https://aishippinglabs.com/api/webhooks/stripe`
4. Events to send:
   - `checkout.session.completed`
   - `customer.subscription.created`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
   - `invoice.payment_succeeded`
   - `invoice.payment_failed`
5. Copy the Signing secret

```bash
STRIPE_WEBHOOK_SECRET=whsec_...
```

### For Local Testing

Use the Stripe CLI to forward webhooks locally:

```bash
stripe listen --forward-to localhost:8000/api/webhooks/stripe
```

The CLI will print a webhook signing secret — use that as `STRIPE_WEBHOOK_SECRET` locally.

### Payment Links

Payment links are configured in `website/settings.py` under `STRIPE_PAYMENT_LINKS`. To update them, create new Payment Links in the Stripe dashboard and update the settings.

---

## Zoom (Live Events)

Used for: creating and managing Zoom meetings for live events.

### Create a Server-to-Server OAuth App

1. Go to [Zoom App Marketplace](https://marketplace.zoom.us/)
2. Click "Develop" > "Build App"
3. Choose "Server-to-Server OAuth"
4. App name: AI Shipping Labs
5. Note the Account ID, Client ID, and Client Secret

```bash
ZOOM_CLIENT_ID=abc123...
ZOOM_CLIENT_SECRET=xyz789...
ZOOM_ACCOUNT_ID=ABCDEF...
```

### Scopes

Add these scopes to the app:
- `meeting:write:admin` — create meetings
- `meeting:read:admin` — read meeting details

### Webhook (optional, for meeting status updates)

1. In the Zoom App settings, go to "Feature" > "Event Subscriptions"
2. Add event subscription
3. Endpoint URL: `https://aishippinglabs.com/api/webhooks/zoom`
4. Events: Meeting Started, Meeting Ended, Participant Joined
5. Copy the Secret Token

```bash
ZOOM_WEBHOOK_SECRET_TOKEN=abc123...
```

---

## Slack (Community)

Used for: social login, community auto-invite/remove on tier change, announcements.

### OAuth App (Login with Slack)

1. Go to [Slack API](https://api.slack.com/apps)
2. Create New App > From scratch
3. App name: AI Shipping Labs, pick your workspace
4. Go to OAuth & Permissions
5. Add redirect URL: `http://localhost:8000/accounts/slack/login/callback/` (local), `https://aishippinglabs.com/accounts/slack/login/callback/` (production)
6. Scopes (User Token Scopes): `openid`, `profile`, `email`
7. Go to Basic Information, copy Client ID and Client Secret

```bash
SLACK_OAUTH_CLIENT_ID=123456.789012
SLACK_OAUTH_CLIENT_SECRET=abc123...
```

### Bot Token (Community Management)

The bot needs to invite/remove users and post announcements.

1. In the same Slack App, go to OAuth & Permissions
2. Add Bot Token Scopes:
   - `admin.invites:write` — invite users
   - `users:read` — look up users by email
   - `users:read.email` — look up users by email
   - `chat:write` — post announcements
   - `channels:read` — list channels
3. Install the app to your workspace
4. Copy the Bot User OAuth Token

```bash
SLACK_BOT_TOKEN=xoxb-...
SLACK_INVITE_URL=https://join.slack.com/t/your-workspace/shared_invite/...
SLACK_COMMUNITY_CHANNEL_IDS=C12345ABC,C67890DEF
SLACK_ANNOUNCEMENTS_CHANNEL_ID=C11111AAA
```

To find channel IDs: right-click a channel in Slack > "View channel details" > the ID is at the bottom.

---

## AWS (SES Email + S3 Recordings)

Used for: sending emails (newsletters, transactional), storing recording files.

### IAM User

1. Go to [AWS IAM Console](https://console.aws.amazon.com/iam/)
2. Create a new IAM user (e.g. `aishippinglabs-app`)
3. Attach policies:
   - `AmazonSESFullAccess` (or a scoped policy for sending only)
   - `AmazonS3FullAccess` (or scoped to the recordings bucket)
4. Create access key, copy the key ID and secret

```bash
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=abc123...
```

### SES (Email)

1. Go to [SES Console](https://console.aws.amazon.com/ses/)
2. Verify your sending domain (`aishippinglabs.com`) or email address
3. If in sandbox mode, also verify recipient addresses for testing
4. Request production access when ready

```bash
AWS_SES_REGION=us-east-1
SES_FROM_EMAIL=community@aishippinglabs.com
```

### S3 (Recording Uploads)

1. Go to [S3 Console](https://console.aws.amazon.com/s3/)
2. Create a bucket (e.g. `aishippinglabs-recordings`)
3. Block public access (files served via signed URLs)

```bash
AWS_S3_RECORDINGS_BUCKET=aishippinglabs-recordings
AWS_S3_RECORDINGS_REGION=eu-central-1
```

---

## YouTube (Video Metadata)

Used for: fetching video metadata for recordings.

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Enable the YouTube Data API v3
3. Create OAuth 2.0 credentials (same project as Google OAuth)
4. Scopes: `https://www.googleapis.com/auth/youtube.readonly`
5. Generate a refresh token using the OAuth playground or a script

```bash
YOUTUBE_CLIENT_ID=123456789.apps.googleusercontent.com
YOUTUBE_CLIENT_SECRET=GOCSPX-...
YOUTUBE_REFRESH_TOKEN=1//0abc...
```

---

## Quick Start (Local Development)

```bash
# 1. Clone and install
git clone git@github.com:AI-Shipping-Labs/website.git
cd website
uv sync

# 2. Copy env vars
cp .env.example .env   # then fill in the values

# 3. Run migrations and seed data
uv run python manage.py migrate
uv run python manage.py seed_data
uv run python manage.py seed_content_sources

# 4. Create a superuser (or use seeded admin@aishippinglabs.com / admin123)
uv run python manage.py createsuperuser

# 5. Run the dev server
uv run python manage.py runserver

# 6. (Optional) Run the task queue worker for background jobs
uv run python manage.py qcluster
```

The site works locally without any integrations configured — OAuth login, Stripe, Zoom, etc. are all optional for development. Seed data provides test users, articles, courses, and events.
