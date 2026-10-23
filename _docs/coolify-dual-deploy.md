# Coolify dual web and worker deployment

The authoritative deployment controller belongs to issue #1996. It updates
both roles in the existing source Service before promoting the existing public
rolling Application. Do not deploy from the historical web-only template or
start a competing poller/controller. The saved Coolify raw Compose owns the
runtime configuration. [Creation-time networks](coolify-networking.md) and
[expand/contract releases](expand-contract-releases.md) remain required.

| Resource | Role | Required runtime behavior |
|---|---|---|
| `aisl-staging` Service | Source web | Sole migrator, legacy `RUN_MIGRATIONS=true`, then Gunicorn |
| Same Service | Worker | Same immutable image, `BOOT_MODE=worker`, `RUN_MIGRATIONS=false`, `qcluster` |
| `aishl-production-rolling-web` Application | Public web | Same release, `BOOT_MODE=web`, migrations disabled, Gunicorn through existing rolling launcher |

## Read-only baseline and post-deploy inventory

From a checkout with the existing operator SSH configuration and identity:

```bash
uv run python deploy/coolify/verify_dual_runtime.py
```

For a known release, add `--expected-image` with the full immutable GHCR image
reference and `--expected-sha` with the full 40-character revision. The helper
never changes containers, Compose, schedules, or production records. It gathers
Docker metadata remotely and removes environment secrets and healthcheck output
before returning it. Only the two boot flags and `VERSION` are collected from
the environment; environment values are not printed in the final result.

Inventory includes stopped containers and renamed containers with the source
Compose project label. Exactly one named source web and worker must exist.
Extra, missing, `Created`, unhealthy, or stale-health containers fail. Both
roles must share the image tag and actual Docker image configuration ID, retain
both existing networks, and retain boot/migration ownership. Worker health must
come from its approved own-host Django-Q heartbeat probe. Docker must have
successfully run that probe within 90 seconds and during the current boot.

The helper checks both roles before and after the public `/ping` request and
fails if either source container changed during that window. Retry after a
controlled replacement settles. A successful `healthy-snapshot` is a bounded
observation, not proof of deployment history or uninterrupted worker execution.
A baseline `/ping` result reports `exact_sha_verified=false`; an HTTP 200 body
such as `N/A` never counts as exact revision evidence. Supply the full expected
SHA for post-deployment evidence. `release_verified` is true only when both
expected arguments pass; it remains false for a baseline snapshot. The Docker
image configuration ID is not the
registry manifest digest: record the latter from the authoritative deploy job.

## Controlled deployment ordering

1. Record a timestamped healthy baseline with the helper, current source IDs
   and start times, the prior pinned release pair, and the public revision.
   Obtain scheduled-task and monitor evidence through authenticated production
   APIs and the existing monitor. Do not query the production database directly.
2. Serialize deployment operations. The #1996 controller checks resource
   identities and drift, pins both source roles to one immutable image, and
   reads the saved definition back before requesting the source deployment.
3. Observe the source worker replacement interval. The installed Service path
   runs Compose with `--force-recreate`; it does not roll the worker. The
   existing `330s` stop grace period bounds Docker's wait, but does not prove
   every in-memory task completed: queued local work can exceed one task's
   `300s` timeout. Preserve durable queue/history rows and report forced kills
   or new failures honestly. Do not remove a draining worker manually.
4. Require fresh exact-release source boot records and both source roles
   healthy, including the worker's own-host heartbeat, before public promotion.
   The #1996 controller must recheck source runtime health immediately before
   public mutation, during rollout, and through final public readiness. A
   public HTTP 200 alone cannot establish coupled deployment success.
5. Track the real public Application deployment identity to terminal success;
   require consecutive exact-SHA public `/ping` results. Run the inventory
   helper with both expected arguments after replacement settles. Record the
   observed worker drain/replacement interval and confirm no obsolete source
   worker remains. Public rolling availability and worker continuity are
   separate observations; preserve the existing public readiness/drain design.

If source health is lost, do not promote public web or report successful
coupled deployment. Preserve the healthy public release while recovering the
source pair. If the worker is lost after public promotion, the deployment is
incomplete even when the public `/ping` revision matches. Failed verification
never authorizes manual orphan removal or another overlapping deploy.

A temporary overlapping worker is a future coordinated deployment design,
not part of this verifier. Adding another worker stanza to the same source
Service does not provide continuity: the Service recreates all its containers.

## Scheduled work and alerts

Read fresh task success/failure counts through the production API before and
after the deployment. Compare failures since the deploy cutoff, not only a
rolling aggregate. Issues #2001 and #2002 own historical peer-review failures
and their natural clearance. The unchanged `aisl-q` check fails when
`failed_last_24h > 10`; it may remain `FAIL` while current scheduled work
succeeds. Record the actual verdict and attribute new errors separately.
Never mute the check, change its threshold/window, delete failed history,
or relabel historical `FAIL` as green to complete deployment verification.

## Recovery

Use the authoritative #1996 path and the recorded prior pinned image for both
source roles, preserving current commands, environment, healthchecks, and
networks. Read back the pair and verify a healthy own-host worker before
promoting or rolling public web. Check exact public `/ping` and the read-only
inventory again. Follow expand/contract compatibility rules; never reverse
schema migrations or replay historical tasks blindly. Preserve private
rollback snapshots. No CloudFront, DNS, database, or secret deletion is part
of this procedure.
