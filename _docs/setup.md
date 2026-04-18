# Infrastructure and Deployment

## Infrastructure

The app runs on AWS ECS Fargate behind an ALB, with Docker images stored in ECR.

| Component | Resource |
|-----------|----------|
| ECS Cluster | `ai-shipping-labs` |
| ECR Repo | `387546586013.dkr.ecr.eu-west-1.amazonaws.com/ai-shipping-labs` |
| RDS | `ai-shipping-labs` (PostgreSQL, private VPC) |
| ALB | `aisl-alb` |
| Dev URL | https://dev.aishippinglabs.com |
| Prod URL | https://prod.aishippinglabs.com / https://aishippinglabs.com |
| Region | `eu-west-1` |

The ECS task runs two containers from the same Docker image:

- `ai-shipping-labs` — gunicorn web server (essential)
- `ai-shipping-labs-worker` — Django-Q2 `qcluster` background worker (non-essential)

On startup, the `entrypoint.sh` script runs `manage.py migrate` before starting the main process. This means database migrations are applied automatically on every deployment.

The deployed version tag is set via the `VERSION` environment variable and displayed in the page footer.

### ECS environment variables

Set in the ECS task definition (plain environment variables):

| Variable | Example | Purpose |
|----------|---------|---------|
| `VERSION` | `20260327-124723-02ce799` | Displayed in the page footer, set automatically by deploy scripts |
| `DEBUG` | `1` | Django debug mode |
| `ALLOWED_HOSTS` | `dev.aishippinglabs.com,aisl-alb-...` | Comma-separated list of allowed hosts |
| `CSRF_TRUSTED_ORIGINS` | `https://dev.aishippinglabs.com,https://aishippinglabs.com` | Required for POST requests (login, forms) to work over HTTPS |
| `GITHUB_APP_ID` | `3143490` | GitHub App ID for content sync |
| `GITHUB_APP_INSTALLATION_ID` | `117839867` | GitHub App installation ID |

Set via AWS Secrets Manager (injected as ECS secrets):

| Variable | Secret ID | Purpose |
|----------|-----------|---------|
| `DATABASE_URL` | `ai-shipping-labs/database-url` (dev) / `ai-shipping-labs/database-url-prod` (prod) | PostgreSQL connection string |
| `SECRET_KEY` | `ai-shipping-labs/django-secret-key` | Django secret key |

Fetched at runtime by the Django app (not injected via ECS):

| Secret ID | Purpose |
|-----------|---------|
| `ai-shipping-labs/github-app-private-key` | GitHub App PEM key for private content repo sync |

The app fetches this from Secrets Manager automatically if the `GITHUB_APP_PRIVATE_KEY` env var and `GITHUB_APP_PRIVATE_KEY_FILE` are not set. Fallback order: local PEM file → env var → Secrets Manager.

When adding a new environment (e.g. prod), make sure `CSRF_TRUSTED_ORIGINS` includes all domains that will submit forms to it.

### GitHub App (content sync)

Content syncing uses one GitHub App. The App's installation in the org grants the platform access to specific repos.

Recommended setup: at https://github.com/organizations/AI-Shipping-Labs/settings/installations, open the installation (ID `117839867`) and set Repository access to "All repositories". With this setting, any new content repo in the `AI-Shipping-Labs` org becomes syncable without revisiting installation settings.

To onboard a new content repo:

1. Confirm the App installation has access to it (covered automatically by "All repositories"). Otherwise add it under "Only select repositories".
2. Add a `ContentSource` row pointing at the repo (`is_private=True` for private repos). Either edit `seed_content_sources.py` and re-run `manage.py seed_content_sources`, or add via Django admin.
3. Run `uv run python manage.py sync_content` (or push to the repo if the webhook is wired).

To verify the App can reach a specific repo:

```bash
uv run python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()
from integrations.services.github import generate_github_app_token
import requests
t = generate_github_app_token()
r = requests.get('https://api.github.com/repos/AI-Shipping-Labs/<repo-name>',
    headers={'Authorization': f'token {t}'}, timeout=10)
print(r.status_code, r.json().get('full_name') or r.json().get('message'))
"
```

A `200` with the full repo name means it works. A `404` means the installation is not granted access to that repo.

## Cache backend (required for the worker dashboard)

django-q writes cluster heartbeats to Django's cache backend. The `/studio/worker/` dashboard reads them back via `Stat.get_all()` to decide whether the cluster is alive. If the cache backend is per-process — which is the default `LocMemCache` — the gunicorn / runserver process never sees heartbeats written by the qcluster process, and the dashboard reports "Worker NOT running" forever, even when the cluster is healthy.

The project ships a dedicated `django_q` cache for this. It is `LocMemCache` during tests (single-process, fast, isolated) and `FileBasedCache` everywhere else.

| Setting | Test mode | Local dev / Production |
|---------|-----------|------------------------|
| `CACHES['django_q']['BACKEND']` | `locmem.LocMemCache` | `filebased.FileBasedCache` |
| `CACHES['django_q']['LOCATION']` | `django-q-test` | `$CACHE_DIR` (default: `<project>/.django_cache`) |
| `Q_CLUSTER['cache']` | `django_q` | `django_q` |

Override the directory with `CACHE_DIR=/path/to/cache` if you need to. The directory is created lazily on first write.

Approved alternatives for production:

- `FileBasedCache` (default) — works on a single host. The directory must be writable by both the web container and the worker container, and they must share the same volume. On ECS this means mounting an EFS volume into both containers.
- `DatabaseCache` — uses a row in Postgres (`CREATE TABLE` via `manage.py createcachetable`). Survives container restarts and works across hosts. Slightly higher latency than file-based but acceptable at heartbeat frequency (every 5 s).

We deliberately do not use Redis: avoiding the operational dependency is a product decision.

If you change `CACHES`, also keep `Q_CLUSTER['cache']` pointing at the same named cache. The wiring is asserted in `studio/tests/test_worker_health_cache.py::DjangoQCacheWiringTest`.

## CI/CD

Two GitHub Actions workflows handle deployment:

- `deploy-dev.yml` — runs tests, builds Docker image, pushes to ECR, and deploys to the dev ECS service. Triggers automatically on push to `main`.
- `deploy-prod.yml` — manual `workflow_dispatch` with a confirmation checkbox. Promotes the current dev image tag to the prod ECS service. Optionally accepts a specific tag.

Both workflows use `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` GitHub secrets for ECR/ECS access.

## Deploying manually

One command to build, push, and deploy:

```bash
# Deploy to dev (default)
bash deploy/deploy.sh

# Deploy to prod
bash deploy/deploy.sh prod
```

This runs `deploy/deploy.sh` which generates a tag, logs into ECR, builds the Docker image, pushes it, and updates the ECS service.

## Deploy scripts

- `deploy/deploy_dev.sh <tag> [env]` — fetches the current ECS task definition, swaps the image tag, registers a new revision, and updates the service. `env` defaults to `dev`.
- `deploy/deploy_prod.sh [tag]` — promotes a tag to prod. If no tag is given, reads the current dev tag. Requires confirmation.
- `deploy/update_task_def.py` — helper that updates the image tag and `VERSION` env var in a task definition JSON file.

## Database access

The RDS instance is in a private VPC and only accessible via the bastion host.

### SSH tunnel setup

Add this to your `~/.ssh/config`:

```
Host bastion-tunnel-aisl
	HostName <BASTION_PUBLIC_IP>
	User ubuntu
	IdentityFile ~/.ssh/razer.pem
	StrictHostKeyChecking no
	LocalForward 5434 <RDS_ENDPOINT>:5432
```

Find the values from Terraform outputs or the AWS console:
- `BASTION_PUBLIC_IP` — EC2 console, instance named "bastion"
- `RDS_ENDPOINT` — RDS console, instance `ai-shipping-labs` (or `terraform output db_endpoint` in `ai-shipping-labs-infra`)

1. Open an SSH tunnel:

```bash
ssh -N bastion-tunnel-aisl
```

2. Connect with pgcli (in another terminal):

```bash
uvx pgcli -h localhost -p 5434 -U aisl_admin -d aisl_dev
```

Enter the DB password when prompted. Retrieve it from Secrets Manager:

```bash
aws secretsmanager get-secret-value \
  --secret-id ai-shipping-labs/db-password \
  --region eu-west-1 \
  --query SecretString --output text
```

### Databases

The RDS instance hosts two databases:

| Database | Environment |
|----------|-------------|
| `aisl_dev` | Dev (`dev.aishippinglabs.com`) |
| `aisl_prod` | Production |

To connect to a specific database, pass it as the `-d` flag:

```bash
uvx pgcli -h localhost -p 5434 -U aisl_admin -d aisl_dev
```

### One-time data seeding

After creating a new database, run migrations first (if the container hasn't done it yet):

```bash
DB_PASSWORD=$(aws secretsmanager get-secret-value \
  --secret-id ai-shipping-labs/db-password \
  --region eu-west-1 \
  --query SecretString --output text)

DATABASE_URL="postgresql://aisl_admin:${DB_PASSWORD}@localhost:5434/aisl_prod" \
  uv run python manage.py migrate
```

Then seed the content sources (one-time, idempotent):

```bash
DB_PASSWORD=$(aws secretsmanager get-secret-value \
  --secret-id ai-shipping-labs/db-password \
  --region eu-west-1 \
  --query SecretString --output text)

DATABASE_URL="postgresql://aisl_admin:${DB_PASSWORD}@localhost:5434/aisl_dev" \
  uv run python manage.py seed_content_sources
```

This registers the GitHub content sources so the sync page (`/studio/sync/`) can pull content. Replace `aisl_dev` with `aisl_prod` for production.

### Creating admin users

With the bastion tunnel open, run Django management commands against the remote database:

```bash
DB_PASSWORD=$(aws secretsmanager get-secret-value \
  --secret-id ai-shipping-labs/db-password \
  --region eu-west-1 \
  --query SecretString --output text)

DATABASE_URL="postgresql://aisl_admin:${DB_PASSWORD}@localhost:5434/aisl_dev" \
  uv run python manage.py createsuperuser --email you@example.com
```

Or via a Python script:

```bash
DB_PASSWORD=$(aws secretsmanager get-secret-value \
  --secret-id ai-shipping-labs/db-password \
  --region eu-west-1 \
  --query SecretString --output text)

DATABASE_URL="postgresql://aisl_admin:${DB_PASSWORD}@localhost:5434/aisl_dev" \
  uv run python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()
from accounts.models import User
from payments.models import Tier
user = User.objects.create_user(email='you@example.com', password='yourpass')
user.is_staff = True
user.is_superuser = True
user.tier = Tier.objects.get(slug='premium')
user.email_verified = True
user.save()
print('Created', user.email)
"
```

## OAuth (Google, GitHub, Slack)

OAuth credentials are managed via the database (Django admin > Social applications), not environment variables.

### Getting Google OAuth credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
2. Create a project (or select an existing one)
3. Go to APIs & Services > OAuth consent screen, configure it (External, add app name and email)
4. Go to APIs & Services > Credentials > Create Credentials > OAuth client ID
5. Application type: Web application
6. Name: e.g. `AI Shipping Labs`
7. Authorized JavaScript origins: add all domains you want to support
8. Authorized redirect URIs: add a callback URL for each environment you want to support:
   - `http://localhost:8000/accounts/google/login/callback/`
   - `https://dev.aishippinglabs.com/accounts/google/login/callback/`
   - `https://prod.aishippinglabs.com/accounts/google/login/callback/`
   - `https://aishippinglabs.com/accounts/google/login/callback/`
9. Save and copy the Client ID and Client secret

You can use a single OAuth client for all environments (local, dev, prod) by adding all redirect URIs to the same client. Or create separate clients per environment if you prefer isolation.

### Getting GitHub OAuth credentials

1. Go to [GitHub Developer Settings](https://github.com/settings/developers)
2. Click New OAuth App
3. Application name: e.g. `AI Shipping Labs`
4. Homepage URL: `https://aishippinglabs.com`
5. Authorization callback URL: `http://localhost:8000/accounts/github/login/callback/`
6. Click Register application
7. Copy the Client ID, then click Generate a new client secret and copy it

GitHub only allows one callback URL per OAuth app, so create a separate app for each environment:

| Environment | Callback URL |
|-------------|-------------|
| Local | `http://localhost:8000/accounts/github/login/callback/` |
| Dev | `https://dev.aishippinglabs.com/accounts/github/login/callback/` |
| Prod (staging) | `https://prod.aishippinglabs.com/accounts/github/login/callback/` |
| Prod | `https://aishippinglabs.com/accounts/github/login/callback/` |

### Getting Slack OAuth credentials

1. Go to [Slack API Apps](https://api.slack.com/apps)
2. Click Create New App > From scratch
3. App Name: e.g. `AI Shipping Labs`
4. Pick the workspace you want to develop in
5. Go to OAuth & Permissions
6. Under Redirect URLs, add the callback URLs for each environment:
   - `http://localhost:8000/accounts/slack/login/callback/`
   - `https://dev.aishippinglabs.com/accounts/slack/login/callback/`
   - `https://prod.aishippinglabs.com/accounts/slack/login/callback/`
   - `https://aishippinglabs.com/accounts/slack/login/callback/`
7. Under Scopes > User Token Scopes, add: `openid`, `profile`, `email`
8. Go to Basic Information and copy the Client ID and Client Secret

### Option A: via `.env` + seed script (local development)

Add the credentials to your `.env` file:

```
GOOGLE_OAUTH_CLIENT_ID=your-client-id
GOOGLE_OAUTH_CLIENT_SECRET=your-client-secret
GITHUB_OAUTH_CLIENT_ID=your-client-id
GITHUB_OAUTH_CLIENT_SECRET=your-client-secret
```

Then run `uv run python manage.py seed_data` — it picks up any `*_OAUTH_CLIENT_ID` / `*_OAUTH_CLIENT_SECRET` pairs from the environment and creates the corresponding Social Application entries in the database.

### Option B: via Django admin (any environment)

1. Log in to the admin panel:
   - Local: http://localhost:8000/admin/socialaccount/socialapp/add/
   - Dev: https://dev.aishippinglabs.com/admin/socialaccount/socialapp/add/
2. Fill in:
   - Provider: `Google` (or `GitHub`, `Slack`)
   - Name: `Google`
   - Client id: your OAuth client ID
   - Secret key: your OAuth client secret
   - Sites: move `aishippinglabs.com` to the "Chosen sites" box
3. Save

### Setup per environment

Each environment has its own database, so the Social Application must be added to each one separately.

For `https://dev.aishippinglabs.com/`:

1. Log in at https://dev.aishippinglabs.com/admin/
2. Go to https://dev.aishippinglabs.com/admin/socialaccount/socialapp/add/
3. Add Google with your client ID and secret, assign to the site, save
4. Repeat for GitHub (use the dev-specific GitHub OAuth app) and Slack

For `https://prod.aishippinglabs.com/`:

1. Log in at https://prod.aishippinglabs.com/admin/
2. Go to https://prod.aishippinglabs.com/admin/socialaccount/socialapp/add/
3. Add Google with your client ID and secret, assign to the site, save
4. Repeat for GitHub (use the prod-specific GitHub OAuth app) and Slack

For `https://aishippinglabs.com/` (production, custom domain):

1. Log in at https://aishippinglabs.com/admin/
2. Go to https://aishippinglabs.com/admin/socialaccount/socialapp/add/
3. Add Google with your client ID and secret, assign to the site, save
4. Repeat for GitHub (use the prod GitHub OAuth app) and Slack

If `prod.aishippinglabs.com` and `aishippinglabs.com` point to the same ECS service and database, you only need to configure the social apps once — they share the same DB.

The same Google/Slack OAuth client can be reused across environments as long as all redirect URIs are registered. GitHub requires a separate OAuth app per environment since it only allows one callback URL per app.

### OAuth troubleshooting

- `redirect_uri_mismatch` (Google) — Add both `localhost` and `127.0.0.1` callback URIs in Google Console. allauth uses the host from the browser request.
- `The redirect_uri MUST match the registered callback URL` (GitHub) — GitHub only allows one callback URL per app. Create separate apps for each environment.
- `User has no field named 'username'` — Ensure `ACCOUNT_USER_MODEL_USERNAME_FIELD = None` is in `settings.py`.
- `SocialApp matching query does not exist` — No Social Application has been added for this provider. Add one via Django admin or `seed_data`.
- `Site matching query does not exist` — Run: `uv run python manage.py shell -c "from django.contrib.sites.models import Site; Site.objects.update_or_create(id=1, defaults={'domain': 'localhost:8000', 'name': 'AI Shipping Labs'})"`
- Google shows "App not verified" warning — Expected during development. Click Continue. For production, submit for Google verification.
- Login works but accounts not consolidated — Ensure `SOCIALACCOUNT_EMAIL_AUTHENTICATION = True` and `SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True` in settings.
