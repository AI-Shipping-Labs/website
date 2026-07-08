#!/usr/bin/env bash
#
# Local Docker boot-profiling harness (issue #1143).
#
# Reproduces the Fargate-dev cold-start under the SAME resource starvation the
# platform runs the web task at (cpu=256 / memory=512 == --cpus=0.25
# --memory=512m), runs the REAL instrumented boot (Dockerfile -> entrypoint.sh
# -> scripts/entrypoint_init.py), and prints the BOOT_TIMING per-phase
# breakdown. It does NOT touch production boot behavior and does NOT edit
# Dockerfile/entrypoint.sh/entrypoint_init.py — it only consumes their existing
# BOOT_TIMING stdout, so it cannot drift from the real boot.
#
# Postgres is a THROWAWAY isolated compose project (-p aisl-bootprofile) that
# reuses the existing compose `db` service, so it can never clobber the
# developer's dev DB or pgdata volume. It is torn down (down -v) on exit,
# including on failure, via a trap.
#
# Env knobs (all optional):
#   BOOT_PROFILE_ITERATIONS   warm-boot repeats per Logfire mode (default 3)
#   BOOT_PROFILE_LOGFIRE      off | on | both (default both)
#   BOOT_PROFILE_PHASE_A      1 to also capture the cold first-migrate boot
#
# shellcheck shell=bash
set -euo pipefail

# --- Configuration -----------------------------------------------------------
ITERATIONS="${BOOT_PROFILE_ITERATIONS:-3}"
LOGFIRE_MODE="${BOOT_PROFILE_LOGFIRE:-both}"
CAPTURE_PHASE_A="${BOOT_PROFILE_PHASE_A:-0}"

PROJECT="aisl-bootprofile"          # isolated compose project — never the dev DB
NETWORK="${PROJECT}_default"        # deterministic network name compose creates
IMAGE="aisl-boot:local"
DB_HOST="db"
DB_URL="postgres://aishippinglabs:aishippinglabs@${DB_HOST}:5432/aishippinglabs"

# The exact cgroup limits Fargate cpu=256/memory=512 map to, asserted below.
EXPECT_NANO_CPUS="250000000"        # --cpus=0.25
EXPECT_MEMORY="536870912"           # --memory=512m (512 MiB)

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKDIR="$(mktemp -d)"

log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[warn] %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m[fail] %s\033[0m\n' "$*" >&2; exit 1; }

# --- Cleanup trap ------------------------------------------------------------
# Tears down the app container AND the isolated Postgres project on ANY exit.
cleanup() {
    local status=$?
    docker rm -f aisl-boot-run >/dev/null 2>&1 || true
    log "Tearing down isolated Postgres project (${PROJECT})"
    docker compose -p "${PROJECT}" down -v >/dev/null 2>&1 || true
    rm -rf "${WORKDIR}" >/dev/null 2>&1 || true
    exit "${status}"
}
trap cleanup EXIT

print_caveats() {
    cat <<'CAVEAT'

------------------------------------------------------------------------------
FAITHFULNESS CAVEAT: local Postgres latency << cross-AZ RDS, so migrate/check
read OPTIMISTICALLY low here. This harness is faithful for CPU-bound levers
(Logfire import 2B, app import, gunicorn worker count 2C) and RELATIVE
before/after — it is NOT a substitute for measuring the RDS-bound migrate lever
(2A) on real dev (#1142). ECR pull + Fargate scheduling are not reproduced;
--cpus approximates but is not identical to Fargate vCPU. Trust RELATIVE numbers.
------------------------------------------------------------------------------
CAVEAT
}

# Run one constrained boot with the given LOGFIRE_ENABLED value; capture stdout
# to $1. Asserts the cgroup limits via `docker inspect` before harvesting logs.
#   $1 = output capture file
#   $2 = "true" | "false"  (LOGFIRE_ENABLED)
run_boot() {
    local out_file="$1" logfire="$2"
    docker rm -f aisl-boot-run >/dev/null 2>&1 || true

    local -a env_args=(
        -e "DATABASE_URL=${DB_URL}"
        -e "SECRET_KEY=boot-profile-dummy-secret"
        -e "DEBUG=False"
        -e "ALLOWED_HOSTS=localhost,127.0.0.1"
        -e "RUN_MIGRATIONS=true"
        -e "PYTHONUNBUFFERED=1"
        -e "LOGFIRE_ENABLED=${logfire}"
        # SES_ENABLED=true is the REAL production web-task config. Under
        # DEBUG=False the `check` phase raises email_app.E001 and aborts the
        # boot when SES_ENABLED is false, so a faithful boot MUST set it true
        # (this is exactly what prod does). It only flips a settings flag: no
        # boto3/Secrets-Manager/SES round trip happens at boot (those are
        # send-time only), and we still pass NO AWS credentials — the keys
        # stay blank, so nothing can authenticate against AWS.
        -e "SES_ENABLED=true"
    )
    # Dummy token ONLY on the Logfire-on run: opens the 3-part gate so
    # integrations.apps.ready() pays the import+configure tax in django_setup.
    if [ "${logfire}" = "true" ]; then
        env_args+=(-e "LOGFIRE_TOKEN=pylf_dummy_boot_profile_token")
    fi
    # NOTE: deliberately NO AWS creds passed — SES_ENABLED=true only sets the
    # flag; the AWS keys are blanked by settings, so no boot-time boto3 auth.

    docker run -d --name aisl-boot-run \
        --network "${NETWORK}" \
        --cpus=0.25 --memory=512m \
        "${env_args[@]}" \
        "${IMAGE}" >/dev/null

    # Assert the resource limits are actually enforced (fail loudly otherwise).
    local inspected nano_cpus memory
    inspected="$(docker inspect --format '{{.HostConfig.NanoCpus}} {{.HostConfig.Memory}}' aisl-boot-run)"
    nano_cpus="${inspected% *}"
    memory="${inspected#* }"
    if [ "${nano_cpus}" != "${EXPECT_NANO_CPUS}" ] || [ "${memory}" != "${EXPECT_MEMORY}" ]; then
        die "resource-limit assertion FAILED: got NanoCpus=${nano_cpus} Memory=${memory}; expected NanoCpus=${EXPECT_NANO_CPUS} Memory=${EXPECT_MEMORY}"
    fi
    printf '    limits OK: NanoCpus=%s (0.25 vCPU)  Memory=%s (512 MiB)\n' "${nano_cpus}" "${memory}"

    # Stream logs until the pre-serve boot completes (phase=total + handoff),
    # then stop the container (we never keep gunicorn running).
    local deadline=$(( $(date +%s) + 300 ))
    : > "${out_file}"
    docker logs -f aisl-boot-run > "${out_file}" 2>&1 &
    local logs_pid=$!
    while :; do
        if grep -q "BOOT_TIMING phase=total" "${out_file}" \
           && grep -qE "Starting server|Starting django-q cluster" "${out_file}"; then
            break
        fi
        if ! docker inspect aisl-boot-run >/dev/null 2>&1; then
            break  # container exited (crash) — surface whatever we captured
        fi
        if [ "$(date +%s)" -ge "${deadline}" ]; then
            kill "${logs_pid}" >/dev/null 2>&1 || true
            cat "${out_file}"
            die "boot did not reach phase=total within timeout"
        fi
        sleep 1
    done
    kill "${logs_pid}" >/dev/null 2>&1 || true
    wait "${logs_pid}" 2>/dev/null || true
    docker rm -f aisl-boot-run >/dev/null 2>&1 || true

    if ! grep -q "BOOT_TIMING phase=total" "${out_file}"; then
        cat "${out_file}"
        die "no BOOT_TIMING phase=total captured (boot crashed before serve)"
    fi
}

# --- Main --------------------------------------------------------------------
log "Boot-profile harness (issue #1143)"
print_caveats

command -v docker >/dev/null 2>&1 || die "docker not found on PATH"

log "Building image from the real Dockerfile: ${IMAGE}"
docker build -t "${IMAGE}" "${REPO_ROOT}" >/dev/null

log "Starting isolated throwaway Postgres (project ${PROJECT}, network ${NETWORK})"
docker compose -p "${PROJECT}" -f "${REPO_ROOT}/docker-compose.yml" up -d db >/dev/null

log "Waiting for Postgres healthcheck"
for _ in $(seq 1 30); do
    if docker compose -p "${PROJECT}" -f "${REPO_ROOT}/docker-compose.yml" ps db \
        --format '{{.Health}}' 2>/dev/null | grep -q healthy; then
        break
    fi
    sleep 2
done

# Optional Phase A: cold first-migrate boot against the fresh DB. Runs before
# the warm iterations so the schema is created here. Logfire-on variant proves
# it does not crash on the pre-migrate cache/settings tables.
declare cold_off_file="" cold_on_file=""
if [ "${CAPTURE_PHASE_A}" = "1" ]; then
    if [ "${LOGFIRE_MODE}" = "off" ]; then
        log "Phase A: cold first-migrate boot (Logfire off)"
        cold_off_file="${WORKDIR}/cold-off.log"
        run_boot "${cold_off_file}" "false"
    else
        # Deliberately run the fresh-DB first-migrate with Logfire ON: proves it
        # does not crash on the pre-migrate settings/cache tables. django.setup()
        # (and integrations.apps.ready() -> is_enabled()) runs BEFORE migrate, so
        # get_config hits a not-yet-created table; integrations/config.py catches
        # the DB-not-ready exception and falls back to env/default. See AC #6.
        log "Phase A: cold first-migrate boot (Logfire on — fresh-DB no-crash proof)"
        cold_on_file="${WORKDIR}/cold-on.log"
        run_boot "${cold_on_file}" "true"
    fi
fi

# Warm-schema (no-op-migrate) boots — the PRIMARY measurement.
declare -a warm_off_files=() warm_on_files=()

run_warm_set() {
    local mode="$1" logfire="$2"
    local -n target="$3"
    log "Phase B: ${ITERATIONS} warm boots (Logfire ${mode})"
    local i out
    for i in $(seq 1 "${ITERATIONS}"); do
        printf '  iteration %s/%s (Logfire %s)\n' "${i}" "${ITERATIONS}" "${mode}"
        out="${WORKDIR}/warm-${mode}-${i}.log"
        run_boot "${out}" "${logfire}"
        target+=("${out}")
    done
}

if [ "${LOGFIRE_MODE}" = "off" ] || [ "${LOGFIRE_MODE}" = "both" ]; then
    run_warm_set "off" "false" warm_off_files
fi
if [ "${LOGFIRE_MODE}" = "on" ] || [ "${LOGFIRE_MODE}" = "both" ]; then
    # Warm-on boots run a no-op migrate against the already-migrated schema
    # (migrated by the Phase A cold boot and/or the warm-off set above).
    run_warm_set "on" "true" warm_on_files
fi

# --- Report ------------------------------------------------------------------
log "Parsing captures -> report"
declare -a report_args=()
[ "${#warm_off_files[@]}" -gt 0 ] && report_args+=(--warm-off "${warm_off_files[@]}")
[ "${#warm_on_files[@]}" -gt 0 ] && report_args+=(--warm-on "${warm_on_files[@]}")
[ -n "${cold_off_file}" ] && report_args+=(--cold-off "${cold_off_file}")
[ -n "${cold_on_file}" ] && report_args+=(--cold-on "${cold_on_file}")

uv run --directory "${REPO_ROOT}" python scripts/boot_profile_report.py "${report_args[@]}"

log "Done. (Isolated Postgres torn down by trap.)"
