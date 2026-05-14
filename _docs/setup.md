# Infrastructure and Deployment

If you are an operator bringing up a fresh environment (not a developer running locally), see [`_docs/configuration.md`](configuration.md) first — it covers the OAuth login + Studio integration setup. This file documents infrastructure, CI/CD, ECS env vars, and bastion / remote-DB access.

## Infrastructure

The app runs on AWS ECS Fargate behind an ALB, with Docker images stored in ECR.

| Component | Resource |
|-----------|----------|
| ECS Cluster | `ai-shipping-labs` |
| ECR Repo | `387546586013.dkr.ecr.eu-west-1.amazonaws.com/ai-shipping-labs` |
| RDS | `ai-shipping-labs` (PostgreSQL, private VPC) |
| ALB | `aisl-alb` |
| Dev URL | https://dev.aishippinglabs.com |
| Prod URL | https://aishippinglabs.com (canonical) — `prod.aishippinglabs.com` is a legacy alias that still resolves to the same ECS service |
| Region | `eu-west-1` |

The ECS task runs two containers from the same Docker image:

- `ai-shipping-labs` — gunicorn web server (essential)
- `ai-shipping-labs-worker` — Django-Q2 `qcluster` background worker (non-essential)

On startup, the entrypoint (`scripts/entrypoint_init.py`) runs `manage.py migrate` before starting the main process. The `email_app` migration `0013_create_django_q_cache_table` creates the `django_q_cache` `DatabaseCache` table as part of `migrate`, so it is applied automatically on every deployment without a separate `createcachetable` step on every container boot.

The deployed version tag is set via the `VERSION` environment variable and displayed in the page footer.

### Prepare app and infrastructure env

Before the app can boot in ECS, provision the ALB/ECS/ECR/RDS resources above, create the Secrets Manager records below, and make sure the task definition carries the platform environment variables in this section. Most rows here are read at process start, used by Django/ECS directly, or intentionally excluded from Studio; rows that also have Studio overrides call that out explicitly.

Set in the ECS task definition (plain environment variables):

| Variable | Example | Purpose |
|----------|---------|---------|
| `VERSION` | `20260327-124723-02ce799` | Displayed in the page footer, set automatically by deploy scripts |
| `DEBUG` | `false` | Django debug mode. Truthy values: `1`, `true`, `yes`. Anything else (including `0`, `false`, `no`, empty) is falsy. Production must set this to `false` to enforce the `SECRET_KEY` guard. |
| `ALLOWED_HOSTS` | `dev.aishippinglabs.com` | Comma-separated list of allowed hosts |
| `CSRF_TRUSTED_ORIGINS` | `https://dev.aishippinglabs.com,https://aishippinglabs.com` | Required for POST requests (login, forms) to work over HTTPS |
| `SITE_BASE_URL` | `https://dev.aishippinglabs.com` | Process-start baseline for absolute URLs. Runtime URL generation can be overridden in Studio > Settings > Site. |
| `RUN_MIGRATIONS` | `true` on web, `false` on worker | Entrypoint dispatch flag. Only the web container runs `migrate`; the worker starts `qcluster`. Set automatically by `deploy/update_task_def.py`. |
| `SES_ENABLED` | `true` in prod | Required to send transactional/campaign email. Defaults false and fails `manage.py check` when `DEBUG=False`. |
| `S3_ENABLED` | `true` in prod | Required to upload content-sync images to S3. Defaults false; content sync skips image upload when missing. |
| `SLACK_ENABLED` | `true` where the Slack bot/imports should run | Startup gate. Studio also has Slack settings, but if this env var is false, Slack token/channel settings are blanked at import time. |
| `SLACK_ENVIRONMENT` | `development` on dev, `production` on prod | Slack routing mode. Non-production modes ignore production Slack channel IDs and require dev/test channel overrides before posting. Can be managed in Studio for normal routing changes after the startup gate is enabled. |
| `Q_WORKERS` | `2` | Optional django-q worker count. Defaults to 1 on SQLite, 2 on Postgres. |
| `EXPECT_WORKER` | `true` | Optional worker-health expectation. Set `false` only for one-off environments that intentionally have no worker. |
| `IP_HASH_SALT` | random string | Optional salt for analytics IP hashes. Empty means IP hashes are not stored. |
| `ANALYTICS_COOKIE_DOMAIN` | `.aishippinglabs.com` | Optional analytics cookie domain override. |
| `EMAIL_BATCH_SIZE` | `200` | Optional campaign-send chunk size. |
| `IMPORT_WELCOME_EMAILS_PER_HOUR` | `50` | Optional throttle for imported-user welcome emails. |
| `SES_FROM_EMAIL` | `noreply@aishippinglabs.com` | Optional legacy email sender fallback. Prefer the explicit Studio keys `SES_TRANSACTIONAL_FROM_EMAIL` and `SES_PROMOTIONAL_FROM_EMAIL`. |
| `SES_UNSUBSCRIBE_EMAIL` | `unsubscribe@aishippinglabs.com` | Optional mailto address for the `List-Unsubscribe` email header. Not rendered in Studio. |
| `SYNC_QUEUED_THRESHOLD_MINUTES` | `10` | Optional sync watchdog queued threshold. |
| `SYNC_RUNNING_THRESHOLD_MINUTES` | `30` | Optional sync watchdog running threshold. |
| `LOGIN_API_SLOW_MS` | `750` | Optional slow-login instrumentation threshold in milliseconds. |

Set via AWS Secrets Manager (injected as ECS secrets):

| Variable | Secret ID | Purpose |
|----------|-----------|---------|
| `DATABASE_URL` | `ai-shipping-labs/database-url` (dev) / `ai-shipping-labs/database-url-prod` (prod) | PostgreSQL connection string |
| `SECRET_KEY` | `ai-shipping-labs/django-secret-key` | Django secret key. Required at startup when `DEBUG=false`: the app raises `ImproperlyConfigured` and exits if it is unset, empty, or equal to the in-tree dev fallback. The fallback `django-insecure-dev-only-do-not-use-in-production` is rejected on purpose so a copy-paste from local dev cannot satisfy the production guard. |

Fetched at runtime by the Django app (not injected via ECS):

| Secret ID | Purpose |
|-----------|---------|
| `ai-shipping-labs/github-app-private-key` | GitHub App PEM key for private content repo sync |

The app fetches this from Secrets Manager automatically if no direct PEM is set. The secret path and region can be configured in Studio with `GITHUB_APP_PRIVATE_KEY_SECRET_ID` and `GITHUB_APP_PRIVATE_KEY_SECRET_REGION`; otherwise the app uses `ai-shipping-labs/github-app-private-key` in `eu-west-1`. Fallback order: Studio PEM → local PEM file → env var → Studio secret path → default Secrets Manager path.

When adding a new environment, make sure `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` include all domains that will submit forms to it. `deploy/update_task_def.py` currently writes `dev.aishippinglabs.com` for dev and `aishippinglabs.com,www.aishippinglabs.com` plus matching HTTPS origins for prod. If `prod.aishippinglabs.com` must keep accepting traffic, add it to the deploy helper and redeploy so the task definition matches reality.

The app also has test-only/internal env controls:

| Variable | Scope | Purpose |
|----------|-------|---------|
| `DJANGO_TEST_DB_NAME` | CI/test | File-backed SQLite test DB for `--keepdb` caching. |
| `Q_SYNC` | test/debug | Runs django-q tasks synchronously when `true`; do not set in ECS services. |
| `DJANGO_QCLUSTER_PROCESS` | internal | Set by `scripts/entrypoint_init.py` before starting `qcluster`; operators should not set it manually. |

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

django-q writes cluster heartbeats to Django's cache backend. The `/studio/worker/` dashboard reads them back via `Stat.get_all()` to decide whether the cluster is alive. If the cache backend is per-process — which is the default `LocMemCache` — the gunicorn / runserver process never sees heartbeats written by the qcluster process, and the dashboard reports "Worker NOT running" forever, even when the cluster is healthy. The same problem exists per-container: a `FileBasedCache` works on a single host but is invisible across ECS task containers because each container has its own ephemeral disk.

The project ships a dedicated `django_q` cache for this. It is `LocMemCache` during tests (single-process, fast, isolated) and `DatabaseCache` everywhere else.

| Setting | Test mode | Local dev / Production |
|---------|-----------|------------------------|
| `CACHES['django_q']['BACKEND']` | `locmem.LocMemCache` | `db.DatabaseCache` |
| `CACHES['django_q']['LOCATION']` | `django-q-test` | `django_q_cache` (a DB table) |
| `Q_CLUSTER['cache']` | `django_q` | `django_q` |

`DatabaseCache` requires a one-time table creation. The `email_app` migration `0013_create_django_q_cache_table` runs `createcachetable django_q_cache` as a `RunPython` step so the table is created on a fresh `manage.py migrate` everywhere — local dev, CI test DB, dev/prod ECS — without a separate one-shot command. To recreate it manually if the table is dropped:

```bash
uv run python manage.py createcachetable django_q_cache
```

The command is idempotent.

### Recurring schedules

Register or refresh recurring django-q schedules after setup and deploys:

```bash
uv run python manage.py setup_schedules
```

This command is idempotent. It updates existing `Schedule` rows by name instead of creating duplicates.

The external user import schedules are:

| Schedule | Source | Cron UTC | Notes |
|----------|--------|----------|-------|
| `import-slack-daily` | Slack workspace | `0 3 * * *` | Runs a live system import and sends imported-user welcome emails through the existing throttle. |
| `import-stripe-daily` | Stripe customers | `30 3 * * *` | Runs a live system import and sends imported-user welcome emails through the existing throttle. |

Course-db remains a manual CSV import from Studio and is not scheduled automatically.

Staff can review scheduled import history in Studio at `/studio/imports/`. Superusers can disable or re-enable the Slack and Stripe daily schedules from that page; disabling pauses the django-q schedule without deleting historical `ImportBatch` rows. After three consecutive failed scheduled batches for the same source, the app sends one admin alert with the source, latest batch id, summary, and Studio review path. A later successful scheduled batch resets that failure streak.

Why DatabaseCache: the application database is the only thing every web and worker process is guaranteed to share, in every deployment topology — local SQLite, multi-container ECS, future hosts. No extra infrastructure (Redis, EFS) needed. The latency cost (one query per heartbeat read) is negligible at the dashboard's request rate.

We deliberately do not use Redis: avoiding the operational dependency is a product decision.

If you change `CACHES`, also keep `Q_CLUSTER['cache']` pointing at the same named cache. The wiring is asserted in `studio/tests/test_worker_health_cache.py::DjangoQCacheWiringTest`.

## Run the app

For local development:

```bash
make setup
make dev
```

`make dev` starts the web server and the django-q worker using `Procfile.dev`; both use `SITE_BASE_URL=http://localhost:8000`. To run one process at a time:

```bash
make run
make worker
```

For ECS, the same Docker image runs both containers. `scripts/entrypoint_init.py` imports Django once, applies migrations only when `RUN_MIGRATIONS=true`, runs `manage.py check --fail-level ERROR`, then starts gunicorn for the web container or `qcluster` for the worker container. The web container is essential; the worker container is non-essential but should be alive for background jobs and Studio worker health.

After a fresh setup or deploy, register recurring jobs:

```bash
uv run python manage.py setup_schedules
```

Verify a running environment:

```bash
curl -fsSL https://dev.aishippinglabs.com/ping
curl -fsSL https://aishippinglabs.com/ping
```

The response body is the deployed `VERSION` tag.

## CI/CD

Two GitHub Actions workflows handle deployment:

- `deploy-dev.yml` — runs tests, builds Docker image, pushes to ECR, and deploys to the dev ECS service. Triggers automatically on push to `main`.
- `deploy-prod.yml` — manual `workflow_dispatch` with a confirmation checkbox. Promotes the current dev image tag to the prod ECS service. Optionally accepts a specific tag.

Both workflows use `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` GitHub secrets for ECR/ECS access.

## Deploying and redeploying manually

One command to build, push, and deploy:

```bash
# Deploy to dev (default)
bash deploy/deploy.sh

# Deploy to prod
bash deploy/deploy.sh prod
```

This runs `deploy/deploy.sh` which generates a tag, logs into ECR, builds the Docker image, pushes it, and updates the ECS service.

Redeploy an existing image tag without rebuilding:

```bash
# Redeploy an existing tag to dev
bash deploy/deploy_dev.sh 20260327-124723-02ce799 dev

# Promote or redeploy an existing tag to prod
CONFIRM_DEPLOY=true bash deploy/deploy_prod.sh 20260327-124723-02ce799
```

Force ECS to restart tasks on the current task definition, for example after an AWS-side secret value changes but the image/tag does not:

```bash
aws ecs update-service \
  --cluster ai-shipping-labs \
  --service ai-shipping-labs-dev \
  --force-new-deployment
```

Use `ai-shipping-labs-prod` for production.

### ECS rollout recovery

`deploy/deploy_dev.sh` waits for `aws ecs wait services-stable`. If the wait times out, the script prints recent service events, running/stopped task reasons, and ALB target health. The common recovery path is:

1. Read the stopped task reason and CloudWatch logs for the new task. Startup failures usually come from missing env-only values: `SECRET_KEY`, `DATABASE_URL`, `SES_ENABLED=true` with `DEBUG=False`, invalid `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS`, or missing `S3_ENABLED=true` for production content-image uploads.
2. Fix the ECS task definition, Secrets Manager value, or deploy helper source of truth.
3. Register a corrected task definition by rerunning `bash deploy/deploy_dev.sh <tag> <env>`, or use `aws ecs update-service --force-new-deployment` if only the referenced secret value changed.
4. Confirm `/ping` returns the expected `VERSION` tag and check `/studio/worker/` for a live django-q heartbeat.

To roll back, redeploy a previously known-good tag with `deploy_dev.sh` for dev or `CONFIRM_DEPLOY=true deploy_prod.sh` for prod. Prod tags are appended to `.prod-versions`; the current dev tag can be read from `https://dev.aishippinglabs.com/ping`.

## Deploy scripts

- `deploy/deploy_dev.sh <tag> [env]` — fetches the current ECS task definition, swaps the image tag, registers a new revision, and updates the service. `env` defaults to `dev`.
- `deploy/deploy_prod.sh [tag]` — promotes a tag to prod. If no tag is given, reads the current dev tag. Requires confirmation.
- `deploy/update_task_def.py` — helper that ensures the worker container exists and updates the image tag plus `VERSION`, `DEBUG`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `SITE_BASE_URL`, and per-container `RUN_MIGRATIONS` in a task definition JSON file.

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
| Prod | `https://aishippinglabs.com/accounts/github/login/callback/` |

`prod.aishippinglabs.com` is a legacy alias of the same ECS service. If you want logins to keep working from that hostname, also register a callback for it; otherwise users are expected to land on the canonical `aishippinglabs.com`.

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

For `https://aishippinglabs.com/` (production):

1. Log in at https://aishippinglabs.com/admin/
2. Go to https://aishippinglabs.com/admin/socialaccount/socialapp/add/
3. Add Google with your client ID and secret, assign to the site, save
4. Repeat for GitHub (use the prod GitHub OAuth app) and Slack

`prod.aishippinglabs.com` is a legacy alias that points to the same ECS service and database, so the social apps configured for `aishippinglabs.com` already cover it — no separate setup needed.

The same Google/Slack OAuth client can be reused across environments as long as all redirect URIs are registered. GitHub requires a separate OAuth app per environment since it only allows one callback URL per app.

### OAuth troubleshooting

- `redirect_uri_mismatch` (Google) — Add both `localhost` and `127.0.0.1` callback URIs in Google Console. allauth uses the host from the browser request.
- `The redirect_uri MUST match the registered callback URL` (GitHub) — GitHub only allows one callback URL per app. Create separate apps for each environment.
- `User has no field named 'username'` — Ensure `ACCOUNT_USER_MODEL_USERNAME_FIELD = None` is in `settings.py`.
- `SocialApp matching query does not exist` — No Social Application has been added for this provider. Add one via Django admin or `seed_data`.
- `Site matching query does not exist` — Run: `uv run python manage.py shell -c "from django.contrib.sites.models import Site; Site.objects.update_or_create(id=1, defaults={'domain': 'localhost:8000', 'name': 'AI Shipping Labs'})"`
- Google shows "App not verified" warning — Expected during development. Click Continue. For production, submit for Google verification.
- Login works but accounts not consolidated — Ensure `SOCIALACCOUNT_EMAIL_AUTHENTICATION = True` and `SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True` in settings.
