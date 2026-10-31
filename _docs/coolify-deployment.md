# Coolify deployment

`Deploy Dev` is the authoritative deployment path for the active AISL Coolify
runtime. The job keeps its historical name, but the target is not an isolated
development service. The existing split topology owns the production-facing
web routes, the source sslip route, and the production worker:

| Resource | UUID | Responsibility |
|---|---|---|
| Service `aisl-staging` | `mekt9kz74fvbixla6olinuma` | migration-capable source web and the production Django-Q worker |
| Application `aishl-production-rolling-web` | `w3mxfr6wzjnpg3hqaw3y0obq` | public web routes, including `aishippinglabs.com` |

Do not create a replacement resource or restore the retired AWS development
runtime. Deploy Dev run `39958312891` proved the old path obsolete: all eleven
validation jobs passed, then ECR rejected the push because repository
`ai-shipping-labs` did not exist in registry `387546586013`. An earlier ECS
rollout also could not verify through the hostname now served by Coolify.
Recreating ECR or ECS would not repair the active serving path.

## Authoritative workflow

`.github/workflows/deploy-dev.yml` preserves the eleven validation jobs and
starts its `Deploy to Dev` job only after all of them pass. That job:

1. builds one `linux/amd64` image;
2. pushes a run-unique tag to `ghcr.io/ai-shipping-labs/website`;
3. verifies that the tag resolves to the recorded registry digest;
4. embeds the full 40-character `GITHUB_SHA` in `VERSION` and the OCI revision;
5. runs `deploy.coolify.deploy_exact_release`.

The helper validates both resource identities and their protected topology
before writing. It pins the source Service web and worker to the same image
while preserving commands, health checks, environment ownership, unrelated
Compose data, and the exact external `coolify` and Service UUID networks.

Stock Coolify does not return a deployment UUID or expose terminal deployment
history for a Service deployment. The helper therefore records the
authenticated diagnostics server's `generated_at` immediately before the
Service request, then requires fresh `boot-timing` evidence for both roles.
Each record must have the correct role, the full expected SHA, an aware
`recorded_at` after the cutoff, and a positive boot total. At the same time,
Coolify must report the saved target image and `running:healthy` for source web
and worker.

The rolling web later replaces the cached web boot record. Before that update,
the helper retains the exact validated cutoff plus each source role's role,
full-SHA tag, and aware `recorded_at`. The GitHub step summary records only
that allowlisted receipt. A compact `COOLIFY_SOURCE_BOOT_RECEIPT` JSON log line
makes the same receipt available through Actions logs. Neither form includes
boot phases or raw diagnostics.

Only after that proof does the helper update the existing rolling Application.
It captures the real Application deployment UUID returned by Coolify, polls
that exact deployment to terminal `finished`, and requires three consecutive
probe cycles where production and DEV `/ping` both equal the full SHA. Configuration drift, redirects,
stale evidence, unhealthy containers, failed or unknown deployment states, and
timeouts fail the job closed.

The independent DEV hostname uses the file-provider route documented in
[`coolify-dev-route.md`](coolify-dev-route.md). A healthy rolling production
application alone does not prove that DEV has a router or a healthy backend.

## GitHub environment

The job uses the `coolify-staging` GitHub environment. Configure these names:

| Kind | Name |
|---|---|
| Secret | `COOLIFY_URL` |
| Secret | `COOLIFY_TOKEN` |
| Secret | `COOLIFY_AISL_SERVICE_UUID` |
| Secret | `AISL_DEPLOY_VERIFICATION_API_TOKEN` |
| Variable | `COOLIFY_AISL_WEB_APPLICATION_UUID` |

The expected HTTPS API origin and both UUIDs are also identity guards in the
helper. Coolify authentication uses `Bearer`; the AISL diagnostics endpoint
uses `Token`. Credentials are scoped to the final deployment step and must not
be printed with API response bodies or saved configuration.

## Competing deploy paths

`.github/workflows/deploy-coolify.yml` is a manual compatibility entry point.
It requests the authoritative `deploy-dev.yml` workflow and performs no image
build or Coolify mutation itself.

The installed shared minute poller also manages Relay and CMP. During the
reviewed cutover, remove only the `applications[name=aishl]` entry from
`/home/alexey/.config/coolify-rolling-web.json`. Preserve Relay, CMP, every
other configuration field, and the shared minute cron invoking
`/home/alexey/tmp/bin/coolify-rolling-web.py`. Disable only the separate AISL
five-minute cron line that invokes `/home/alexey/tmp/bin/aisl-auto-deploy.py`,
and retain its private rollback copy. Never disable the shared poller.

## Emergency diagnostics

The retired AWS ECS emergency workflows (`prod-emergency-diagnostics.yml`,
`prod-emergency-web-rollback.yml`, `prod-emergency-worker-rollback.yml`) were
deleted because they targeted the retired `ai-shipping-labs` ECS cluster. Use
the read-only Coolify and application diagnostics surfaces below instead:

1. Check public revision readiness on both endpoints:

```bash
curl -fsSL https://aishippinglabs.com/ping
curl -fsSL https://dev.aishippinglabs.com/ping
```

2. Run the read-only dual-runtime inventory check over SSH (uses
   `deploy/coolify/dual_runtime_snapshot.py` and never prints environment
   secrets):

```bash
uv run python deploy/coolify/verify_dual_runtime.py
```

Pass `--expected-image ghcr.io/ai-shipping-labs/website:<tag>` and
`--expected-sha <40-char-sha>` when verifying a specific release. The helper
confirms that `web-mekt9kz74fvbixla6olinuma` and
`worker-mekt9kz74fvbixla6olinuma` are `running` and `healthy`, share one
immutable GHCR image, stay attached to `coolify` and `mekt9kz74fvbixla6olinuma`,
and pass the worker's own-host Django-Q heartbeat probe within the last 90
seconds.

3. Inspect application-level boot timing, schedule reconciliation, and recent
   worker tasks through the authenticated production API (never print token
   values):

```bash
uv run asl raw GET /api/diagnostics/boot-timing
uv run asl raw GET /api/diagnostics/schedules
uv run asl worker failed-tasks
```

## Emergency rollback

Keep the expanded database schema and follow
[`expand-contract-releases.md`](expand-contract-releases.md). Never reverse
schema migrations during an incident rollback.

To roll back to a prior known-good release:

1. Record the current state with `uv run python deploy/coolify/verify_dual_runtime.py`
   and `curl -fsSL https://aishippinglabs.com/ping`.
2. Identify the target release from the last known-good `Deploy Dev` run
   summary or `COOLIFY_SOURCE_BOOT_RECEIPT`:
   - immutable image tag `ghcr.io/ai-shipping-labs/website:deploy-<run_id>-<attempt>-<40-char-sha>`
   - registry manifest digest `sha256:<64-hex>`
   - full 40-character commit SHA
3. Either push a revert commit to `main` so `Deploy Dev` builds and deploys a
   fresh exact-head image, or re-promote the known-good immutable GHCR image
   directly through the fail-closed release helper using the `coolify-staging`
   environment credentials:

```bash
uv run python -m deploy.coolify.deploy_exact_release \
  --image "ghcr.io/ai-shipping-labs/website:deploy-<run_id>-<attempt>-<sha>" \
  --digest "sha256:<64-hex>" \
  --sha "<40-char-sha>"
```

`deploy.coolify.deploy_exact_release` updates both `web` and `worker` in
Service `aisl-staging` (`mekt9kz74fvbixla6olinuma`) first, waits for fresh
`boot-timing` evidence and `running:healthy` on both source roles, and only then
promotes Application `aishl-production-rolling-web`
(`w3mxfr6wzjnpg3hqaw3y0obq`) and verifies three consecutive matching `/ping`
responses on production and DEV.

4. After the helper finishes, confirm both source containers and public `/ping`
   match the restored release:

```bash
uv run python deploy/coolify/verify_dual_runtime.py \
  --expected-image "ghcr.io/ai-shipping-labs/website:deploy-<run_id>-<attempt>-<sha>" \
  --expected-sha "<40-char-sha>"
```

Do not treat `https://aishippinglabs.com/ping` alone as worker recovery proof;
`/ping` only observes web. Worker recovery requires `worker-mekt9kz74fvbixla6olinuma`
in `running:healthy` state with its own-host heartbeat probe passing and a
fresh `boot_timing:worker` record for the target SHA.
