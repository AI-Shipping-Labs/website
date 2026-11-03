# Infrastructure and Deployment

If you are an operator bringing up a fresh environment (not a developer running locally), see [`_docs/configuration.md`](configuration.md) first — it covers the OAuth login + Studio integration setup. This file documents the Coolify runtime infrastructure, CI/CD, runtime env vars, and bastion / remote-DB access.

A fresh environment that uses Zoom also needs Marketplace configuration outside
the application: follow [configuration section 10](configuration.md#10-zoom-events)
and the authoritative [Server-to-Server OAuth setup, six-scope matrix, and
disposable smoke procedure](integrations/zoom.md#server-to-server-oauth-app-setup).
After changing Zoom scopes, restart both web and worker services so every
process-local token cache obtains a newly scoped token, or wait up to one hour.
Never validate a fresh environment with a real event or expose a token, secret,
recording download URL, or Zoom join/start URL as evidence.

Local development additionally requires Node.js 24 and npm. Tailwind CSS
3.4.17 is pinned in `package.json`/`package-lock.json`; `make run` and
`make run2` perform a minified one-time build, while `make dev` starts the CSS
watcher. `make css-build` and `make css-watch` are the canonical explicit
commands. The output at `static/css/tailwind.css` is generated and gitignored.

## The community-base package dependency

`community-base` (DataTalksClub/community-base) is pinned as a git dependency
at a released `vX.Y.Z` tag in `pyproject.toml`, resolved by `uv.lock`. Both
`ci.yml` and `deploy-dev.yml` run `scripts/check_community_base_source.py`
before installing dependencies; the guard fails the build if the dependency
is ever a local path, editable install, branch ref, or registry copy instead
of the pinned tag, so unreviewed package code cannot reach the site runtime.

To work on the package together with this site, use the reversible local link
(the package repo lives as a sibling checkout, `../community-base`):

- `make core-link` — requires clean `pyproject.toml`/`uv.lock`, snapshots
  their exact bytes under `.tmp/core-link/`, and points the dependency at the
  sibling checkout (editable; package edits are visible without reinstall).
  Refuses when a link is already active or the checkout is missing or is not
  a community-base repo.
- `make core-unlink` — restores the snapshotted pinned dependency and syncs
  it back. Refuses when `pyproject.toml` has changes beyond the link edit or
  when recovery state is missing; it never runs a blanket `git checkout --`.

Never commit a linked tree: `uv run python -c "import community_base,
pathlib; print(pathlib.Path(community_base.__file__).resolve())"` must print
a `site-packages` path before you commit, and `grep -n 'path = ' pyproject.toml`
must print nothing.

## Infrastructure

The app runs on Contabo Coolify (`https://coolify.169.58.119.163.sslip.io`) behind Coolify's Traefik proxy, with immutable `linux/amd64` Docker images stored in GitHub Container Registry (`ghcr.io/ai-shipping-labs/website`). The earlier AWS ECS Fargate runtime and ECR repository are retired; see [`coolify-deployment.md`](coolify-deployment.md), [`coolify-dual-deploy.md`](coolify-dual-deploy.md), and [`coolify-networking.md`](coolify-networking.md).

| Component | Resource |
|-----------|----------|
| Coolify Source Service | `aisl-staging` (`mekt9kz74fvbixla6olinuma`) — migration-capable source `web` and production `worker` |
| Coolify Rolling Web App | `aishl-production-rolling-web` (`w3mxfr6wzjnpg3hqaw3y0obq`) — public production web routes |
| Image Registry | `ghcr.io/ai-shipping-labs/website` (`deploy-<run_id>-<attempt>-<40-char-sha>`) |
| Networks | `coolify` (PostgreSQL DNS/connectivity) and `mekt9kz74fvbixla6olinuma` (Service UUID / Traefik) |
| Dev URL | https://dev.aishippinglabs.com (Traefik file-provider route to `web-mekt9kz74fvbixla6olinuma:8000`; see [`coolify-dev-route.md`](coolify-dev-route.md)) |
| Prod URL | https://aishippinglabs.com |
| AWS Region (S3 / SES / Secrets Manager) | `eu-west-1` |

Three runtime containers run from the same GHCR Docker image (`scripts/entrypoint_init.py`):

- `web-mekt9kz74fvbixla6olinuma` (`aisl-staging` Service `web`) — sole migrator (`RUN_MIGRATIONS=true`, `BOOT_MODE` unset), runs `manage.py migrate`, R1 reconciliation/schedule suppression, and `setup_schedules`, then starts Gunicorn
- `worker-mekt9kz74fvbixla6olinuma` (`aisl-staging` Service `worker`) — background worker (`BOOT_MODE=worker`, `RUN_MIGRATIONS=false`), skips `migrate`, runs R1 reconciliation/schedule suppression and `setup_schedules`, then starts Django-Q2 `qcluster`
- `aishl-production-rolling-web` (`w3mxfr6wzjnpg3hqaw3y0obq`) — public rolling web (`BOOT_MODE=web`, `RUN_MIGRATIONS=false`), skips `migrate`, runs R1 reconciliation/schedule suppression and `setup_schedules`, then starts Gunicorn behind `/opt/rolling-launch.py`

The `email_app` migration `0013_create_django_q_cache_table` creates the `django_q_cache` `DatabaseCache` table as part of `migrate`, so it is applied automatically on every deployment without a separate `createcachetable` step on every container boot.

The deployed version tag is baked into the image `VERSION` environment variable (the full 40-character commit SHA) and returned by `/ping` and displayed in the page footer.

### Prepare app and infrastructure env

The Coolify Service Compose definition and Application environment carry the platform environment variables in this section (`deploy/coolify/topology.py` owns the protected runtime contract). Most rows here are read at process start, used by Django or the entrypoint directly, or intentionally excluded from Studio; rows that also have Studio overrides call that out explicitly.

Set in the Coolify Service/Application environment or image build:

| Variable | Example | Purpose |
|----------|---------|---------|
| `VERSION` | `e79168d6974422fa345132c45b81467efd7eeaab` | Baked into the image at build time (`GITHUB_SHA`); displayed in the page footer and `/ping`. Must not be overridden in Coolify runtime env (`reject_version_override`). |
| `DEBUG` | `0` | Django debug mode. Truthy values: `1`, `true`, `yes`. Anything else (including `0`, `false`, `no`, empty) is falsy. Production must set this to `0` / `false` to enforce the `SECRET_KEY` guard. |
| `ALLOWED_HOSTS` | `dev.aishippinglabs.com,aishippinglabs.com` | Comma-separated list of allowed hosts |
| `CSRF_TRUSTED_ORIGINS` | `https://dev.aishippinglabs.com,https://aishippinglabs.com` | Required for POST requests (login, forms) to work over HTTPS |
| `SITE_BASE_URL` | `https://aishippinglabs.com` | Process-start baseline for absolute URLs. Runtime URL generation can be overridden in Studio > Settings > Site. |
| `BOOT_MODE` | unset on source `web`, `worker` on source `worker`, `web` on rolling `web` | Entrypoint role selector (`scripts/entrypoint_init.py`). `web` and `worker` skip `migrate` on boot; when unset, `RUN_MIGRATIONS` selects web-migrator vs worker. |
| `RUN_MIGRATIONS` | `true` on source `web`, `false` on `worker` and rolling `web` | Entrypoint migration flag. Only the source `web` container runs `migrate`. Set by the Coolify topology (`deploy/coolify/topology.py`). |
| `SERVING_BOOT_SMOKE_CHECK_ENABLED` | `false` on source Service | Controls the pre-Django raw-env smoke check in `scripts/entrypoint_init.py` (defaults to `true` when unset). |
| `GUNICORN_WORKERS` / `GUNICORN_THREADS` | `2` / `4` | Gunicorn worker and thread counts for `scripts/gunicorn_runtime.py`. |
| `RELAY_BASE_URL` / `RELAY_API_KEY` | configured in prod | Required so transactional/campaign email deliver through Relay. Missing values fail `manage.py check` when `DEBUG=False`. |
| `S3_ENABLED` | `true` in prod | Uploads content-sync images to S3. Defaults true — a missing var resolves to true so uploads run; content sync only skips image upload when the flag is explicitly `false` (or under `TESTING`). Keep it `true` in prod so uploads never depend on the default drifting. |
| `SLACK_ENABLED` | `true` where the Slack bot/imports should run | Startup gate. Studio also has Slack settings, but if this env var is false, Slack token/channel settings are blanked at import time. |
| `SLACK_ENVIRONMENT` | `development` on dev, `production` on prod | Slack routing mode. Non-production modes ignore production Slack channel IDs and require dev/test channel overrides before posting. Can be managed in Studio for normal routing changes after the startup gate is enabled. |
| `Q_WORKERS` | `2` | Optional django-q worker count. Defaults to 1 on SQLite, 2 on Postgres. |
| `IP_HASH_SALT` | random string | Optional salt for analytics IP hashes. Empty means IP hashes are not stored. |
| `ANALYTICS_COOKIE_DOMAIN` | `.aishippinglabs.com` | Optional analytics cookie domain override. |
| `EMAIL_BATCH_SIZE` | `200` | Optional campaign-send chunk size. |
| `IMPORT_WELCOME_EMAILS_PER_HOUR` | `50` | Optional throttle for imported-user welcome emails. |
| `RELAY_UNSUBSCRIBE_EMAIL` | `unsubscribe@aishippinglabs.com` | Optional mailto address for the `List-Unsubscribe` email header. Not rendered in Studio. |
| `LOGIN_API_SLOW_MS` | `750` | Optional slow-login instrumentation threshold in milliseconds. |

Configure the following runtime controls in Studio > Settings > Site. Their
environment values remain process-start fallbacks for a fresh database or an
environment that has not saved an override.

| Variable | Default | Purpose |
|----------|---------|---------|
| `SYNC_QUEUED_THRESHOLD_MINUTES` | `10` | Minutes a sync may remain queued before the watchdog marks it failed. |
| `SYNC_RUNNING_THRESHOLD_MINUTES` | `30` | Minutes a sync may remain running before the watchdog marks it failed. |
| `EXPECT_WORKER` | `true` | Set `false` only for a one-off environment intentionally running without django-q. |

Set as secret environment variables in Coolify (Service `aisl-staging` and Application `aishl-production-rolling-web`):

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | PostgreSQL connection string on the `coolify` network |
| `SECRET_KEY` | Django secret key. Required at startup when `DEBUG=false`: the app raises `ImproperlyConfigured` and exits if it is unset, empty, or equal to the in-tree dev fallback. The fallback `django-insecure-dev-only-do-not-use-in-production` is rejected on purpose so a copy-paste from local dev cannot satisfy the production guard. |

Fetched at runtime by the Django app when no direct PEM is configured in Studio or the Coolify environment:

| Secret ID | Purpose |
|-----------|---------|
| `ai-shipping-labs/github-app-private-key` | GitHub App PEM key for private content repo sync |

The AWS Secrets Manager fallback remains because the authenticated Coolify
source Service and production web Application environments do not define
`GITHUB_APP_PRIVATE_KEY` or `GITHUB_APP_PRIVATE_KEY_FILE`. Keep
`integrations/services/github_app.py:_resolve_github_app_private_key()` until a
direct PEM is configured and verified for both Coolify runtimes. The fallback
uses `GITHUB_APP_PRIVATE_KEY_SECRET_ID`, defaulting to
`ai-shipping-labs/github-app-private-key` in `eu-west-1`.

When adding a new environment, make sure `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` include all domains that will submit forms to it.

The app also has test-only/internal env controls:

| Variable | Scope | Purpose |
|----------|-------|---------|
| `DJANGO_TEST_DB_NAME` | CI/test | File-backed SQLite test DB for `--keepdb` caching. |
| `Q_SYNC` | test/debug | Runs django-q tasks synchronously when `true`; do not set in deployed services. |
| `DJANGO_QCLUSTER_PROCESS` | internal | Set by `scripts/entrypoint_init.py` before starting `qcluster`; operators should not set it manually. |

### GitHub App (content sync)

Content syncing uses one GitHub App. The App's installation in the org grants the platform access to specific repos.

Recommended setup: at https://github.com/organizations/AI-Shipping-Labs/settings/installations, open the installation (ID `117839867`) and set Repository access to "All repositories". With this setting, any new content repo in the `AI-Shipping-Labs` org becomes syncable without revisiting installation settings.

To onboard a new content repo:

1. Confirm the App installation has access to it (covered automatically by "All repositories"). Otherwise add it under "Only select repositories".
2. Add a `ContentSource` row pointing at the repo (`is_private=True` for private repos). Use `/studio/sync/sources/new`, import a source configuration in Studio, or edit `seed_content_sources.py` and re-run `manage.py seed_content_sources`.
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

django-q writes cluster heartbeats to Django's cache backend. The `/studio/worker/` dashboard reads them back via `Stat.get_all()` to decide whether the cluster is alive. If the cache backend is per-process — which is the default `LocMemCache` — the gunicorn / runserver process never sees heartbeats written by the qcluster process, and the dashboard reports "Worker NOT running" forever, even when the cluster is healthy. The same problem exists per-container: a `FileBasedCache` works inside a single container but is invisible across separate web and worker containers because each container has its own filesystem.

The project ships a dedicated `django_q` cache for this. It is `LocMemCache` during tests (single-process, fast, isolated) and `DatabaseCache` everywhere else.

| Setting | Test mode | Local dev / Production |
|---------|-----------|------------------------|
| `CACHES['django_q']['BACKEND']` | `locmem.LocMemCache` | `db.DatabaseCache` |
| `CACHES['django_q']['LOCATION']` | `django-q-test` | `django_q_cache` (a DB table) |
| `Q_CLUSTER['cache']` | `django_q` | `django_q` |

`DatabaseCache` requires a one-time table creation. The `email_app` migration `0013_create_django_q_cache_table` runs `createcachetable django_q_cache` as a `RunPython` step so the table is created on a fresh `manage.py migrate` everywhere — local dev, CI test DB, and the Coolify runtime — without a separate one-shot command. To recreate it manually if the table is dropped:

```bash
uv run python manage.py createcachetable django_q_cache
```

The command is idempotent.

### Recurring schedules

Register or refresh recurring django-q schedules after setup and deploys:

```bash
uv run python manage.py setup_schedules
```

The command builds and validates the complete declarative set before writing,
then updates every managed `Schedule` row in one atomic transaction. It is
idempotent, preserves operator-unknown schedules, and never leaves a partially
updated set when validation or a database write fails.

Web and worker boot run this reconciliation but remain fail-open for site
availability: a failure is logged, existing crons remain unchanged, and the
container continues starting. The durable degraded result is visible on
`/studio/worker/` and from staff-token `GET /api/diagnostics/schedules`.
Recovery runs again on the next container boot and every 15 minutes through
`reconcile-schedules`; an operator can also run `setup_schedules` manually and
receive the original validation or apply error directly.

The external user import schedules are:

| Schedule | Source | Cron UTC | Notes |
|----------|--------|----------|-------|
| `import-slack-daily` | Slack workspace | `0 3 * * *` | Runs a live system import and sends imported-user welcome emails through the existing throttle. |
| `import-stripe-daily` | Stripe customers | `30 3 * * *` | Runs a live system import and sends imported-user welcome emails through the existing throttle. |

Course-db remains a manual CSV import from Studio and is not scheduled automatically.

Staff can review scheduled import history in Studio at `/studio/imports/`. Superusers can disable or re-enable the Slack and Stripe daily schedules from that page; disabling pauses the django-q schedule without deleting historical `ImportBatch` rows. After three consecutive failed scheduled batches for the same source, the app sends one admin alert with the source, latest batch id, summary, and Studio review path. A later successful scheduled batch resets that failure streak.

Why DatabaseCache: the application database is the only thing every web and worker process is guaranteed to share, in every deployment topology — local SQLite, multi-container Coolify, future hosts. No extra infrastructure (Redis, EFS) needed. The latency cost (one query per heartbeat read) is negligible at the dashboard's request rate.

We deliberately do not use Redis: avoiding the operational dependency is a product decision. Do not add Redis, Memcached, or another cache service for request-path helpers.

Request-path helpers (`get_config` / `is_enabled` / `site_base_url`, announcement banner, active redirects, public downloads nav, marketing-page nav) must not treat a `DatabaseCache` GET as free. Every GET is SQL. Those helpers read through `integrations.shared_cache`, which memoizes the shared-cache GET per request and with a bounded in-process TTL of at most 5 seconds. `DatabaseCache` remains the cross-process source of truth for django-q heartbeats and for publishing invalidation. Studio saves become visible to other processes no later than that TTL.

If you change `CACHES`, also keep `Q_CLUSTER['cache']` pointing at the same named cache. The wiring is asserted in `studio/tests/test_worker_health_cache.py::DjangoQCacheWiringTest`. Query-budget tests that count SQL against the cache table must force `DatabaseCache`; LocMem hides the amplification.

## Run the app

For local development:

```bash
make setup
make dev
```

`make dev` starts the web server, django-q worker, and Tailwind watcher using
`Procfile.dev`; the application processes use
`SITE_BASE_URL=http://localhost:8000`. To run one process at a time:

```bash
make run
make worker
```

## Local Docker Compose

`docker compose up` starts `db`, `web`, `worker`, and `watcher`. One-shot
migrate / seed / content-source registration / `_content-repo` sync is:

```bash
docker compose --profile setup up setup
```

Each Compose app service supplies a `command:`. `entrypoint.sh` execs those
arguments when present, so setup, web, worker, and watcher run the commands
in `docker-compose.yml` instead of falling through to
`scripts.entrypoint_init`. Deployed Coolify containers start with an empty
argv (or `/opt/rolling-launch.py` on the rolling web Application), and
`BOOT_MODE` / `RUN_MIGRATIONS` dispatch stays in `scripts/entrypoint_init.py`.

For the Coolify runtime, the same GHCR Docker image runs the source `web`
migrator, the source `worker`, and the rolling public `web` container. A
disposable Node build stage installs the npm lockfile and compiles minified
CSS; only that bundle is copied into the Python image before `collectstatic`
fingerprints and creates gzip/Brotli variants. Node, npm, `node_modules`, and
Tailwind are absent from the runtime image. `scripts/entrypoint_init.py`
imports Django once, applies migrations only when `RUN_MIGRATIONS=true` on the
source `web` container, runs R1 reconciliation and schedule registration, then
starts gunicorn for web containers or `qcluster` for the worker container.

After a fresh setup or deploy, manually reconcile recurring jobs when needed:

```bash
uv run python manage.py setup_schedules
```

Normal web and worker boot already perform the same validate-then-atomic
reconciliation. Boot logs and records failures without crashing the container;
operators can inspect `/studio/worker/` or staff-token
`GET /api/diagnostics/schedules`. Recovery is the next container boot, the
15-minute `reconcile-schedules` tick, or the manual command above.

Verify a running environment:

```bash
curl -fsSL https://dev.aishippinglabs.com/ping
curl -fsSL https://aishippinglabs.com/ping
```

The response body is the deployed `VERSION` tag.

## CI/CD

Two GitHub Actions workflows handle deployment and production release attestation:

- `deploy-dev.yml` — runs all validation gates, then builds one exact-head GHCR image and deploys the split Coolify runtime (`aisl-staging` source web and worker followed by `aishl-production-rolling-web`). Triggers automatically on push to `main`; [`coolify-deployment.md`](coolify-deployment.md) owns its target and verification contract.
- `deploy-prod.yml` (`Manual Production Deployment`) — manual `workflow_dispatch` with a confirmation checkbox (`confirmProdDeploy`). Runs `deploy.coolify.attest_production` to attest that the green `Deploy Dev` release is running and healthy across both Coolify resources without restarting containers, then verifies production sitemap and robots. Uses `concurrency.group` `production-coolify-attestation` with `cancel-in-progress: false`.

Both workflows use the `coolify-staging` GitHub environment documented in [`coolify-deployment.md`](coolify-deployment.md).

### Lockfile contract

CI installs Python dependencies with `uv sync --locked`. The `--locked` flag does two things in one:

- It asserts that `uv.lock` is already in sync with `pyproject.toml` and exits 1 with "requirements are unsatisfiable" (or a similar "lockfile out of date" message) if it isn't. This is the drift detection the contract relies on.
- It refuses to mutate `uv.lock` during the sync. The committed lockfile is what gets installed, byte-for-byte.

We deliberately do not use `uv sync --frozen` (alone). Empirically on `uv` 0.10.11, `uv sync --frozen` exits 0 even when `pyproject.toml` and `uv.lock` have drifted — it skips the resolver entirely and installs whatever is in the lock, which is the silent-mismatch trap the contract exists to prevent. And the two flags cannot be combined: `uv sync --frozen --locked` is rejected by `uv` 0.10.11 with "the argument '--frozen' cannot be used with '--locked'". `--locked` alone is the correct choice — it gives us drift detection AND lock immutability in a single flag.

The contract is: update `uv.lock` locally and commit it on the same branch as the dependency change before deploying. To update dependencies:

1. Run `uv sync` (or `uv add <pkg>` / `uv lock --upgrade-package <pkg>`) locally.
2. Commit the updated `uv.lock` (and `pyproject.toml` if changed) on the same branch as the dependency change.
3. Push. CI will then install the new resolution exactly.

If a deploy fails at the "Install dependencies" step with a message about `uv.lock` being out of date or requirements being unsatisfiable, the fix is to regenerate the lock locally and commit it — not to retry the deploy.

## Deploying and redeploying manually

There is no local deploy script. The AWS ECS runtime and its ECR repository are retired, and the `deploy/deploy*.sh` / `deploy/update_task_def.py` helpers and `prod-emergency-*` ECS workflows were removed with it. Every deploy goes through `Deploy Dev` on push to `main` (or `workflow_dispatch`), which deploys the exact-head GHCR image to the split Coolify runtime; [`coolify-deployment.md`](coolify-deployment.md) owns the target, verification, and redeploy contract.

### Emergency production diagnostics and rollback

Keep the expanded schema and follow [`expand-contract-releases.md`](expand-contract-releases.md). Do not reverse schema migrations during an incident rollback.

For read-only incident diagnostics against the Coolify runtime:

1. Check `https://aishippinglabs.com/ping` and `https://dev.aishippinglabs.com/ping`.
2. Run `uv run python deploy/coolify/verify_dual_runtime.py` to inspect `web-mekt9kz74fvbixla6olinuma` and `worker-mekt9kz74fvbixla6olinuma` container health, shared image identity, networks, and the worker's own-host heartbeat probe without exposing environment secrets.
3. Query `uv run asl raw GET /api/diagnostics/boot-timing`, `uv run asl raw GET /api/diagnostics/schedules`, and `uv run asl worker failed-tasks` for application boot and worker state.

To roll back to a prior known-good immutable GHCR release (`ghcr.io/ai-shipping-labs/website:deploy-<run_id>-<attempt>-<40-char-sha>`), either push a revert commit to `main` so `Deploy Dev` builds and rolls out the revert, or re-promote the prior image, digest, and 40-character SHA through `uv run python -m deploy.coolify.deploy_exact_release` as documented in [`coolify-deployment.md`](coolify-deployment.md#emergency-rollback). That helper updates both `web` and `worker` in Service `aisl-staging` (`mekt9kz74fvbixla6olinuma`) first, verifies fresh `boot-timing` evidence and `running:healthy` on both roles, and then updates Application `aishl-production-rolling-web` (`w3mxfr6wzjnpg3hqaw3y0obq`) and requires three consecutive matching `/ping` probes on production and DEV. Do not treat `https://aishippinglabs.com/ping` alone as worker recovery proof; `/ping` only observes web.

### Canonical-host redirect verification

After deploying the website canonical-host middleware and proxy routing rules,
run the read-only redirect contract check:

```bash
uv run python deploy/verify_canonical_redirects.py https://aishippinglabs.com
```

The verifier disables automatic redirect following. It checks HTTP apex,
HTTP `www`, and HTTPS `www` for both `/` and a path with a query string,
requires an exact `301` to the clean apex HTTPS URL, and confirms the target
returns `200` without another redirect.

## Production access model

Production URL: `https://aishippinglabs.com`.

Agents should not treat local files, local SQLite, or an ad hoc database
tunnel as production data. For live application records, use the
authenticated production API with a staff token:

```bash
curl -sS -H "Authorization: Token $API_SHIPPING_LABS_API_TOKEN" \
  https://aishippinglabs.com/api/users/alice@example.com
```

Use the API for production user state, SES event history, email logs,
integration-setting metadata, and narrow operator writes that already
exist as API endpoints. The settings metadata endpoint uses the same `Token`
authentication:

```bash
curl -sS -H "Authorization: Token $API_SHIPPING_LABS_API_TOKEN" \
  https://aishippinglabs.com/api/v1/settings/<key>
```

Coolify runtime inspection is separate from application data. To inspect the
live source containers (`web-mekt9kz74fvbixla6olinuma` and
`worker-mekt9kz74fvbixla6olinuma`) and public `/ping` without printing
secrets:

```bash
uv run python deploy/coolify/verify_dual_runtime.py
```

For remaining AWS infrastructure (S3/CloudFront CDN, SES/SNS email events,
RDS), inspect the `DataTalksClub/aws-infra` repo (AISL resources under
`main/aisl/`) and file/track infra issues there when changes are needed.

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
- `RDS_ENDPOINT` — RDS console, instance `ai-shipping-labs` (or `terraform output db_endpoint` in `DataTalksClub/aws-infra` under `main/aisl/`)

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

The bootstrap order on a fresh database is:

```
manage.py migrate                       # creates the slug+level skeleton tier rows
manage.py sync_content [--from-disk ...] # fills in name / prices / Stripe IDs / description / features
```

`migrate` seeds the four `payments.Tier` rows with only `slug`, `level`, and a placeholder `name` (the slug, title-cased). Every other editable tier field (`price_eur_month`, `price_eur_year`, `stripe_price_id_monthly`, `stripe_price_id_yearly`, `description`, `features`) is populated by the `tiers.yaml` content sync, which is the source of truth. Until `sync_content` runs against a yaml that carries those fields, the `/membership` page renders empty prices and empty feature lists; tier names still work via the placeholder, but the page is not customer-ready.

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

This registers the GitHub content sources so the sync page (`/studio/sync/`) can pull content. Replace `aisl_dev` with `aisl_prod` for production. The first `sync_content` run after that populates the tier fields above from `tiers.yaml`.

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

OAuth credentials for Google, GitHub, and Slack login are configured in Studio > Settings > Auth & Login. The full operator setup — including the provider-console steps, callback URLs, scopes, and per-provider foot-guns — is documented in [`_docs/configuration.md`](configuration.md) section 3.

Use this section only when you need a local-development shortcut that bypasses Studio. For any non-local environment (dev, prod, staging), use the Studio path documented in `configuration.md`.

### Local-development shortcut (`seed_data` + `.env`)

For local development you can pre-populate `socialaccount.SocialApp` entries from environment variables:

1. Register OAuth apps with each provider, using `http://localhost:8000/accounts/<provider>/login/callback/` as the callback URL. The full provider-console instructions live in [`configuration.md`](configuration.md) section 3.
2. Add the client ID and secret pairs to your local `.env`:
   ```
   GOOGLE_OAUTH_CLIENT_ID=your-client-id
   GOOGLE_OAUTH_CLIENT_SECRET=your-client-secret
   GITHUB_OAUTH_CLIENT_ID=your-client-id
   GITHUB_OAUTH_CLIENT_SECRET=your-client-secret
   ```
3. Run `uv run python manage.py seed_data` — it picks up any `*_OAUTH_CLIENT_ID` / `*_OAUTH_CLIENT_SECRET` pairs from the environment and creates the corresponding Social Application entries in the database.

This is a one-shot convenience for local dev; the canonical path on any deployed environment is Studio > Settings > Auth & Login.

### OAuth troubleshooting

- `redirect_uri_mismatch` (Google) — Add both `localhost` and `127.0.0.1` callback URIs in Google Console. allauth uses the host from the browser request.
- `The redirect_uri MUST match the registered callback URL` (GitHub) — GitHub only allows one callback URL per app. Create separate apps for each environment.
- `User has no field named 'username'` — Ensure `ACCOUNT_USER_MODEL_USERNAME_FIELD = None` is in `settings.py`.
- `SocialApp matching query does not exist` — No Social Application has been added for this provider. Add one via Studio > Settings > Auth & Login (any environment) or via `seed_data` (local dev shortcut).
- `Site matching query does not exist` — Run: `uv run python manage.py shell -c "from django.contrib.sites.models import Site; Site.objects.update_or_create(id=1, defaults={'domain': 'localhost:8000', 'name': 'AI Shipping Labs'})"`
- Google shows "App not verified" warning — Expected during development. Click Continue. For production, submit for Google verification.
- Login works but accounts not consolidated — Ensure `SOCIALACCOUNT_EMAIL_AUTHENTICATION = True` and `SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True` in settings.
