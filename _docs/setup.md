# Setup Guide

How to get the AI Shipping Labs platform running locally.

## 1. Clone and Install

```bash
git clone git@github.com:AI-Shipping-Labs/website.git
cd website
uv sync
uv run playwright install chromium
```

## 2. Database

```bash
uv run python manage.py migrate
uv run python manage.py seed_data
```

## 3. Django Site Domain

allauth builds OAuth callback URLs from the site domain. Set it to match how you access the app in the browser:

```bash
uv run python manage.py shell -c "
from django.contrib.sites.models import Site
Site.objects.update_or_create(id=1, defaults={'domain': 'localhost:8000', 'name': 'AI Shipping Labs (local)'})
"
```

## 4. Environment Variables

Create `.env` in the project root. The app loads it automatically via python-dotenv.

Only fill in what you need — everything works without these, just with the corresponding features disabled.

### Google OAuth

Sign in with Google on the login page.

1. [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → OAuth consent screen → External
2. Credentials → Create Credentials → OAuth client ID → Web application
3. Add authorized redirect URIs:
   - `http://localhost:8000/accounts/google/login/callback/`
   - `http://127.0.0.1:8000/accounts/google/login/callback/`
4. Add authorized JavaScript origin: `http://localhost:8000`

```bash
GOOGLE_OAUTH_CLIENT_ID=
GOOGLE_OAUTH_CLIENT_SECRET=
```

### GitHub OAuth

Sign in with GitHub on the login page.

1. [GitHub Developer Settings](https://github.com/settings/developers) → OAuth Apps → New OAuth App
2. Homepage URL: `http://localhost:8000`
3. Authorization callback URL: `http://localhost:8000/accounts/github/login/callback/`

GitHub only allows one callback URL per app — create separate apps for local and production.

```bash
GITHUB_OAUTH_CLIENT_ID=
GITHUB_OAUTH_CLIENT_SECRET=
```

### Zoom

Auto-create Zoom meetings for live events via Studio.

1. [Zoom App Marketplace](https://marketplace.zoom.us/develop/create) → Create Server-to-Server OAuth app
2. Add scopes: `meeting:write:meeting:admin`, `cloud_recording:read:list_recording_files:admin`
3. Activate the app
4. For recording webhooks: Feature → Event Subscriptions → endpoint URL `https://yourdomain.com/api/webhooks/zoom`, subscribe to `recording.completed`, copy the secret token

```bash
ZOOM_ACCOUNT_ID=
ZOOM_CLIENT_ID=
ZOOM_CLIENT_SECRET=
ZOOM_WEBHOOK_SECRET_TOKEN=
```

### Stripe

Membership payments and subscriptions.

1. [Stripe Dashboard](https://dashboard.stripe.com/test/apikeys) → Developers → API keys
2. For webhooks: Developers → Webhooks → Add endpoint → `https://yourdomain.com/api/webhooks/stripe`
   - Events: `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`

```bash
STRIPE_PUBLISHABLE_KEY=
STRIPE_SECRET_KEY=
STRIPE_WEBHOOK_SECRET=
```

### Amazon SES

Transactional email (verification, password reset, newsletters).

1. AWS Console → SES → Verified identities → verify your sending domain or email
2. Create IAM user with `AmazonSESFullAccess` policy

New SES accounts start in sandbox mode — can only send to verified addresses until you request production access.

```bash
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_SES_REGION=us-east-1
```

### Slack

Community integration — invite members, post announcements.

1. [api.slack.com/apps](https://api.slack.com/apps) → Create app
2. Bot Token Scopes: `chat:write`, `users:read`, `users:read.email`, `channels:read`
3. Install to workspace

```bash
SLACK_BOT_TOKEN=
SLACK_COMMUNITY_CHANNEL_IDS=
SLACK_INVITE_URL=
SLACK_ANNOUNCEMENTS_CHANNEL_ID=
```

## 5. Admin Access

Sign in with OAuth, then promote yourself:

```bash
uv run python manage.py shell -c "
from accounts.models import User
u = User.objects.get(email='your@email.com')
u.is_staff = True
u.is_superuser = True
u.save()
"
```

Or create directly: `uv run python manage.py createsuperuser`

## 6. Run

```bash
uv run python manage.py runserver
```

- Site: http://localhost:8000
- Studio: http://localhost:8000/studio/ (staff only, link appears in header)
- Django Admin: http://localhost:8000/admin/

## Running Tests

```bash
uv run python manage.py test --parallel          # unit/integration
uv run python -m pytest playwright_tests/ -v      # E2E
```

## Production

1. `DEBUG = False`, strong `SECRET_KEY`, `ALLOWED_HOSTS` set to your domain
2. Update Django Site domain to `aishippinglabs.com`
3. Production OAuth redirect URIs (https)
4. PostgreSQL instead of SQLite
5. Static files via whitenoise or CDN
6. `uv run gunicorn website.wsgi`
