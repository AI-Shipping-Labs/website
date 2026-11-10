# Coolify DEV route

`dev.aishippinglabs.com` uses a dedicated `dev-web` container in the
`aisl-staging` Coolify Service. It runs migrations against `aisl_dev` and has
one worker with two threads. The existing source `web` and `worker` remain on
the production `aisl` database, and the public rolling Application remains
separate.

## Durable configuration

`deploy/coolify/dev-route.yml` owns an independent Traefik file-provider route.
Its routers match only the DEV hostname: HTTPS uses the existing `letsencrypt`
resolver, HTTP redirects to HTTPS, and HTTPS page requests use BasicAuth for
`aisl-scheduled`. A higher-priority `/ping` router remains unauthenticated for
readiness probes. A response-header middleware adds `X-Robots-Tag: noindex,
nofollow` at Traefik, including BasicAuth's 401 response. Its backend uses the
stable Docker name `dev-web-mekt9kz74fvbixla6olinuma:8000`, reachable from the
proxy on the existing Service UUID network.

The route source contains a placeholder for the BasicAuth hash. Before
installing it, replace that placeholder in the private temporary route copy
with the APR1 or bcrypt hash for the same password stored in the GitHub Actions
secret `AISL_DEV_SITE_PASSWORD`. Never put the password or generated hash in
git, a command transcript, or Actions logs. The scheduled workflow validates
the secret before its authenticated warmups; the Playwright browser fixture
adds BasicAuth to every remote browser context and preserves other context
options. `/ping` stays public so the readiness poll can run before the
authenticated job.

The source `web` and `worker` stay on production `aisl`. Do not add DEV to the
rolling Application's domains or alter its protected runtime contract.

## Install or update without replacing containers

Deploy the exact release first and run
`uv run python deploy/coolify/verify_dual_runtime.py`. It verifies the source
service has exactly `web`, `worker`, and `dev-web`, that the first two target
`aisl`, and that `dev-web` targets `aisl_dev`. Then record source container
and proxy IDs, start times, and health. Prepare the private route copy with
the BasicAuth hash replacement, then confirm the proxy resolves the stable
backend name and `dev-web` returns a non-empty HTTP 200 `/ping` for the DEV
Host. Keep any previous route file privately for rollback.

Copy the reviewed repository file to the existing host directory
`/data/coolify/proxy/dynamic/` as `aisl-dev-route.yml.tmp`, then atomically
rename it in that directory to `aisl-dev-route.yml`. Traefik watches this
mounted directory and loads the file without a proxy or application restart.
The `.tmp` suffix prevents the provider reading a partially copied YAML file.
No Compose deployment, Coolify Service start, or Application redeploy is
needed. Updating this route file does not restart the proxy or application
containers. It persists across normal deployments; Docker DNS keeps the
backend independent of changing container IP addresses.

After installation, require repeated direct HTTPS DEV `/ping` responses with
HTTP 200 and a non-empty full revision. Confirm anonymous HTTPS page requests
return 401 with `X-Robots-Tag: noindex, nofollow`, then run the scheduled
workflow and confirm its authenticated page checks pass. Check production
`/ping` and the public homepages, then compare all recorded container
identities, start times and health. For rollback, restore only the previous
DEV route file atomically (or remove only the newly introduced DEV file if
there was none), and let the provider reload. Do not restart the proxy or any
application container.

## Database isolation boundary

The release contract maintains three source-service roles. `web` remains the
production migrator, `worker` remains the production Django-Q worker, and
`dev-web` uses `AISL_DATABASE_NAME=aisl_dev` with the Coolify `DATABASE_URL`
credentials. Django accepts that override only for the exact `aisl_dev` name
and only with PostgreSQL; it fails startup for any other name or SQLite. The
dev container runs migrations against that selected database. The public
rolling Application has no `AISL_DATABASE_NAME` override and keeps its existing
production database configuration.

`verify_dual_runtime.py` reports only the database name from Docker metadata;
it never prints `DATABASE_URL` or other secrets. The scheduled browser
selection excludes `local_only` and `creates_data` journeys. Those eligible
checks are currently read-only, and any future scheduled writes stay in
`aisl_dev` through the dev runtime's database override.

For a later Traefik-only route edit, `[skip ci]` can avoid an unnecessary
Coolify deployment. After installing the route, manually dispatch
`scheduled-playwright-dev.yml` on `main`; its `Wait for Contabo dev endpoint`
job must pass. Record that job's verdict separately from any later Playwright
shard failures.

## Deploy verification

Normal exact-release deployments roll out `web`, `worker`, and isolated
`dev-web` before the production Application. Their final readiness gate
requires both production and
DEV `/ping` to equal the deployed full SHA in the same three consecutive probe
cycles. A stale or empty DEV response resets that count. A failed request or
redirect fails closed with the affected endpoint identified; the existing
HTTP reader never follows redirects. The configured deployment deadline
bounds unsuccessful revision polling.

Offline deployment regressions exercise production success with DEV missing,
empty, stale or redirecting, plus joint consecutive success. The narrow
affected-test mapping for these deployment/checker files runs those offline
owners and the repository guards without starting a local browser. Any mixed
app, template or unreviewed infrastructure change retains its usual browser
selection. Live routing proof comes from repeated direct HTTP and the
manually dispatched DEV readiness job.
