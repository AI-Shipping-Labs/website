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
public `/ping` responses equal to the full SHA. Configuration drift, redirects,
stale evidence, unhealthy containers, failed or unknown deployment states, and
timeouts fail the job closed.

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
