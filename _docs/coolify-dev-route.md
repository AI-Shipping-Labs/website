# Coolify DEV route

`dev.aishippinglabs.com` uses the existing source Service web container. The
current topology has no separate DEV container. The source web and worker
also serve production responsibilities, so restarting the source Service is
not a DEV-only operation. Production public web remains on the separate
rolling Application. Preserve all three containers and the shared proxy when
repairing DEV routing.

## Durable configuration

`deploy/coolify/dev-route.yml` owns an independent Traefik file-provider route.
Its routers match only the DEV hostname: HTTPS uses the existing `letsencrypt`
resolver, and HTTP redirects to HTTPS. Its backend uses the stable Docker
name `web-mekt9kz74fvbixla6olinuma:8000`, reachable from the proxy on the
existing Service UUID network. The proxy's active `/ping` healthcheck uses
the DEV hostname, a ten-second interval and a three-second timeout.

The source web's Docker routing labels remain disabled. Do not add DEV to
the rolling Application's domains or alter its protected runtime contract.
That would require a production container replacement and couple a DEV repair
to the production rolling topology.

## Install or update without replacing containers

Before installation, record source web/worker, rolling web and proxy container
IDs, start times and health. Confirm the proxy resolves the stable backend
name and the source returns a non-empty HTTP 200 `/ping` for the DEV Host.
Keep any previous route file privately for rollback.

Copy the reviewed repository file to the existing host directory
`/data/coolify/proxy/dynamic/` as `aisl-dev-route.yml.tmp`, then atomically
rename it in that directory to `aisl-dev-route.yml`. Traefik watches this
mounted directory and loads the file without a proxy or application restart.
The `.tmp` suffix prevents the provider reading a partially copied YAML file.
No Compose deployment, Coolify Service start, or Application redeploy is
needed. This host file persists across normal application deployments; Docker
DNS keeps the backend independent of changing source container IP addresses.

After installation, require repeated direct HTTPS DEV `/ping` responses with
HTTP 200 and a non-empty full revision. Check production `/ping` and the public
homepages, then compare all recorded container identities, start times and
health. For rollback, restore only the previous DEV route file atomically (or
remove only the newly introduced DEV file if there was none), and let the
provider reload. Do not restart the proxy or any application container.

For a repair requiring no application deployment, commit with `[skip ci]` so
the coupled `Deploy Dev` workflow does not recreate production source
containers. Manually dispatch `scheduled-playwright-dev.yml` on `main`; its
`Wait for Contabo dev endpoint` job must pass. Record that job's verdict
separately from any later Playwright shard failures.

## Deploy verification

Normal exact-release deployments still use the existing source and production
rollout sequence. Their final readiness gate now requires both production and
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
