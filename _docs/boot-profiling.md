# Local Docker boot profiling

A one-command local harness that reproduces the Fargate-dev container cold-start
under the same resource starvation the platform runs the web task at, runs the
REAL instrumented boot, and prints the `BOOT_TIMING` per-phase breakdown —
including a Logfire on-vs-off `django_setup` comparison.

This is dev tooling for the fast inner loop of boot optimization (issue #1141).
It does NOT change production boot behavior and does NOT touch any AWS infra.
`#1142` (diagnostics endpoint) is the complementary confirmation of the
RDS-bound levers on real dev.

## Why this exists

We cannot read `BOOT_TIMING` from CloudWatch (no infra/IAM access), and
deploying to dev for every boot-optimization attempt is far too slow to iterate.
The harness builds the real image, boots it under `--cpus=0.25 --memory=512m`
(the exact cgroup mapping of Fargate `cpu=256`/`memory=512`), and prints the
per-phase timing straight from container stdout.

## Usage

```bash
make boot-profile
```

That single target:

1. Builds the image from the real `Dockerfile` (`aisl-boot:local`) — same path
   as CI, no bespoke boot.
2. Brings up a THROWAWAY Postgres by reusing the existing compose `db` service
   under an ISOLATED compose project (`-p aisl-bootprofile`). It never clobbers
   your dev DB or `pgdata` volume and is torn down with `down -v` on exit
   (including on failure, via a trap).
3. Runs the app boot with raw `docker run --cpus=0.25 --memory=512m`, joining
   the isolated db network. It asserts the limits are enforced by reading them
   back with `docker inspect` (`NanoCpus == 250000000`, `Memory == 536870912`)
   and fails loudly on any mismatch.
4. Runs the warm-schema (no-op-migrate) boot `BOOT_PROFILE_ITERATIONS` times for
   each Logfire mode, parses the captures with `scripts/boot_profile_report.py`,
   and prints per-phase min/median plus the Logfire off-vs-on `django_setup`
   delta.

### Knobs

| Make var | Default | Meaning |
| --- | --- | --- |
| `BOOT_PROFILE_ITERATIONS` | `3` | Warm-boot repeats per Logfire mode. |
| `BOOT_PROFILE_LOGFIRE` | `both` | `off`, `on`, or `both`. `both` produces the side-by-side `django_setup` delta. |
| `BOOT_PROFILE_PHASE_A` | `0` | Set to `1` to also capture the optional cold first-migrate boot (Phase A). |

Examples:

```bash
BOOT_PROFILE_ITERATIONS=5 make boot-profile          # 5 warm boots per mode
BOOT_PROFILE_LOGFIRE=off make boot-profile           # only the Logfire-off runs
BOOT_PROFILE_PHASE_A=1 make boot-profile             # also capture cold first-migrate
```

## What it measures

The boot phases come straight from `scripts/entrypoint_init.py` (unmodified):

- `django_setup` — settings import + app-registry population, including
  `integrations.apps.ready()` -> Logfire configure. This is the phase the
  Logfire on-vs-off delta settles (#1141 Phase 2B).
- `migrate` — `manage.py migrate` (a no-op against the already-migrated warm DB;
  Django still queries migration state and runs `check`).
- `check` — `manage.py check --fail-level ERROR`.
- `setup_schedules` — django-q schedule registration.
- `total` — process start -> just before the gunicorn handoff.

### Migration phases

- Phase A (optional, run once): the FIRST boot against the fresh throwaway DB
  runs the full `migrate`. Captured as the "cold first-migrate" figure. Enable
  with `BOOT_PROFILE_PHASE_A=1`. A Logfire-on cold boot does not crash on the
  pre-migrate settings/cache tables — `integrations/config.py` catches the
  DB-not-ready exceptions and falls back to env/default.
- Phase B (primary, run N times): after the schema is applied, warm boots run a
  no-op `migrate`, mirroring a steady-state dev redeploy. Reported per-phase
  min/median so cold-vs-warm noise is visible.

### Minimal boot env

The harness passes exactly what a constrained boot needs to reach gunicorn and
NOTHING else:

| Env var | Value |
| --- | --- |
| `DATABASE_URL` | `postgres://aishippinglabs:aishippinglabs@db:5432/aishippinglabs` |
| `SECRET_KEY` | `boot-profile-dummy-secret` (required under `DEBUG=False`) |
| `DEBUG` | `False` |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` |
| `RUN_MIGRATIONS` | `true` |
| `PYTHONUNBUFFERED` | `1` |
| `LOGFIRE_ENABLED` | `true` or `false` |
| `LOGFIRE_TOKEN` | dummy, only on the Logfire-on run |
| `SES_ENABLED` | `true` (the real production web-task value) |

No AWS credentials are passed. `SES_ENABLED=true` is set because it is exactly
what the production web task sets: under `DEBUG=False`, the `check` phase raises
`email_app.E001` and aborts the boot when `SES_ENABLED` is false, so a faithful
boot must set it true. It only flips a settings flag — no
boto3/SES/Secrets-Manager round trip happens at boot (those are send-time only),
and because no AWS credentials are passed the keys stay blank, so nothing can
authenticate against AWS. (The earlier "disable SES by omission" plan was
corrected here after a real run showed `email_app.E001` crashing the `check`
phase.)

## Faithfulness caveats (READ THIS)

The harness is faithful for RELATIVE, fast iteration — it is not a source of
trustworthy absolute production numbers.

- Local Postgres has near-zero latency vs cross-AZ RDS, so `migrate`/`check`
  read OPTIMISTICALLY low locally. The harness is faithful for CPU-bound levers
  (Logfire import 2B, app import cost, gunicorn worker count 2C, and the
  RELATIVE before/after of any code change) but is NOT a substitute for
  measuring the RDS-bound `migrate` lever (2A) — confirm 2A on real dev via
  #1142.
- ECR image-pull time and Fargate scheduling are not reproduced.
- `--cpus` throttling approximates but is not identical to Fargate vCPU
  allocation.
- Trust RELATIVE numbers; do not over-trust the absolute figures.

These caveats are also echoed at runtime by the harness so they cannot be
missed.

## Files

- `scripts/boot_profile.sh` — the bash harness (shellcheck-clean).
- `scripts/boot_profile_report.py` — pure-Python parser/reporter (no Docker, no
  Django; unit-tested in `tests/test_boot_profile_report.py`).
- `make boot-profile` — the single documented entry point.

## Not in scope

- No infra/Terraform changes; no AWS resources.
- No change to production boot behavior, `Dockerfile`, `entrypoint.sh`, or
  `scripts/entrypoint_init.py` — the harness only consumes their existing
  `BOOT_TIMING` output, so it cannot drift from the real boot.
- No Studio/API product surface — the "user" here is the engineer optimizing
  boot.
