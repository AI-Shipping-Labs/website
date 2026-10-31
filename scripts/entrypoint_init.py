"""Single-process container entrypoint.

Replaces the entrypoint.sh that ran three separate ``manage.py`` processes
(migrate / createcachetable / check) before exec-ing gunicorn. Each
process re-imported ``website/settings.py`` and re-paid the eager
AWS-network cost (Secrets Manager + RDS DatabaseCache + IntegrationSetting
query), adding ~30s of redundant cold-start that raced the ALB
unhealthy-threshold (3 x 30s).

This script imports settings ONCE and runs all four steps in one Python
process, then hands off to gunicorn via ``gunicorn.app.wsgiapp.run`` --
same Python interpreter, no second Django boot. Combined with
``gunicorn --preload`` (master forks workers without re-importing the
WSGI module), the cold-start cost is paid exactly once per container.

Boot mode dispatch (issue #1141 Phase 2A)
=========================================

All containers share the same Docker ENTRYPOINT. ``entrypoint.sh`` execs
``$@`` when arguments are present (local Compose ``command:``), and only
starts this module when argv is empty. Deployed web and worker containers
start with no arguments and the ``BOOT_MODE`` / ``RUN_MIGRATIONS`` env vars
select what this process does:

* ``BOOT_MODE=web`` -> ``django.setup`` -> R1 reconcile/suppression ->
  register schedules -> gunicorn bind. SKIPS migrate (the source Service
  ``web`` container owns migrations), so the rolling serving container binds
  fast.
* ``BOOT_MODE=worker`` -> ``django.setup`` -> R1 reconcile/suppression ->
  register schedules -> qcluster. SKIPS migrate.
* ``BOOT_MODE`` ABSENT -> legacy serving path keyed off
  ``RUN_MIGRATIONS``: web (``RUN_MIGRATIONS=true``) migrates ->
  R1 reconcile/suppression -> schedules -> gunicorn; worker
  (``RUN_MIGRATIONS!=true``) runs R1 reconcile/suppression -> schedules ->
  qcluster.

Before Django setup, every mode runs a cheap raw-env smoke check. It catches
the high-value production email misconfig guarded by ``email_app.E001`` without
invoking Django's full URL/model check framework on the serving path.

Every mode still benefits from the single-process boot: only one settings
import, no Secrets Manager / RDS round trips before serving / polling.

The ``django_q_cache`` table (used by the worker-heartbeat
``DatabaseCache`` backend) is created by an ``email_app`` migration, so
it lands during ``migrate`` on the web container rather than on every
boot.

Schedule registration (issue #708)
==================================

``setup_schedules`` registers the django-q ``Schedule`` rows that drive
recurring tasks (``complete-finished-events``, ``health-check``,
``event-reminders``, etc.). We run it on every container boot for BOTH
web and worker — running on web alone is not enough because web and worker
start in parallel from the same image (see issue #336), and at any given
moment either tier may be the one whose boot lands first. The command is
idempotent (uses ``update_or_create`` via ``jobs.tasks.schedule``), so
running it twice produces exactly one row per schedule.

Ordinary registration failures MUST NOT crash the container: a regression in
``setup_schedules`` (e.g. a bad task path) cannot be allowed to take the web
tier down. We log and continue. R1-incompatible schedule suppression is a
separate fail-closed phase: a stale schedule could let an overlapping worker
claim incompatible work, so service startup must stop if deletion fails.
"""

import logging
import os
import sys
import time

import django

from scripts.gunicorn_runtime import (
    apply_web_statement_timeout,
    gunicorn_argv,
    gunicorn_thread_count,
    web_statement_timeout_ms,
)
from scripts.gunicorn_runtime import gunicorn_worker_count as _gunicorn_worker_count

logger = logging.getLogger(__name__)

_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})


def _truthy_env(name, *, default=False):
    if name not in os.environ:
        return default
    return os.environ[name].strip().lower() in _TRUTHY_ENV_VALUES


def _emit_timing(phase, seconds):
    """Print a single flushed ``BOOT_TIMING`` line for CloudWatch.

    Format matches the ``CI_TIMING phase=... seconds=...`` convention used
    in ``.github/workflows/deploy-dev.yml`` so ops can grep both with the
    same tooling. ``flush=True`` is mandatory: the container may crash in a
    later phase before gunicorn binds, and the line must still reach
    CloudWatch. See issue #1141 Phase 1.
    """
    print(f"BOOT_TIMING phase={phase} seconds={seconds:.3f}", flush=True)


def _timed(phase, fn, record=None):
    """Run ``fn()``, emit its elapsed ``BOOT_TIMING`` line, return its result.

    Purely additive observability: it does NOT change ordering, behavior, or
    crash semantics of the wrapped phase. The timing line is emitted from a
    ``finally`` block so a phase that raises still records its elapsed time
    in CloudWatch before the exception propagates unchanged. ``fn``'s return
    value is passed straight through to the caller.

    Uses ``time.perf_counter()`` for a monotonic wall-clock measurement.

    The elapsed value is computed exactly once. When ``record`` is a dict, the
    same number that ``_emit_timing`` prints is stored under ``phase`` so the
    persisted boot-timing payload (issue #1142) is the single source of truth
    -- there is no recomputation. See ``persist_boot_timing``.
    """
    start = time.perf_counter()
    try:
        return fn()
    finally:
        elapsed = time.perf_counter() - start
        _emit_timing(phase, elapsed)
        if record is not None:
            record[phase] = elapsed


def persist_boot_timing(role, phases):
    """Persist one boot-timing payload for ``role`` to the shared cache.

    Issue #1142: writes ``{tag, recorded_at, role, phases}`` to the shared
    ``django_q`` ``DatabaseCache`` (``django_q_cache`` table) under
    ``boot_timing:<role>`` with ``timeout=None`` so the web diagnostics
    endpoint can read the latest numbers for BOTH tiers (the worker's numbers
    are produced in a different container; the per-process ``default``
    ``LocMemCache`` cannot carry them across the container boundary).

    The build ``tag`` comes from the ``VERSION`` env var (the full commit
    SHA baked into the image at build time), falling back to ``"unknown"`` for
    local runs.

    Fail-safe: the entire write is wrapped in a broad ``except`` that logs the
    traceback and swallows it -- exactly the discipline ``_register_schedules``
    uses. A store/cache failure at boot (e.g. the ``django_q_cache`` table not
    yet created on a first-ever deploy) must NEVER crash the container or halt
    a rollout; the worst case is that this one boot's numbers are missing and
    the endpoint returns ``null`` for that tier until the next boot.
    """
    try:
        from django.core.cache import caches
        from django.utils import timezone

        payload = {
            "tag": os.environ.get("VERSION") or "unknown",
            "recorded_at": timezone.now().isoformat(),
            "role": role,
            "phases": dict(phases),
        }
        caches["django_q"].set(
            f"boot_timing:{role}", payload, timeout=None,
        )
    except Exception:
        # Log the traceback but keep booting. Persisting diagnostics is
        # non-critical for serving requests or running the qcluster; a bad
        # cache/DB state cannot be allowed to take a tier down.
        logger.exception("persist_boot_timing failed during entrypoint boot")


def _register_schedules():
    """Register recurring django-q schedules. Idempotent and fail-safe.

    Runs on every container boot for both web and worker variants. Wrapped
    in a broad ``except`` because schedule registration is non-critical for
    serving requests or running the qcluster — a failure here must not
    prevent the container from starting. See issue #708.
    """
    from django.core.management import call_command

    print("Register recurring job schedules", flush=True)
    try:
        call_command("setup_schedules", verbosity=0)
    except Exception as exc:
        # Log the traceback but keep booting. A bad schedule entry cannot
        # be allowed to take the web tier down; the worst-case fallout is
        # that one cron does not fire until the next deploy, which is
        # still better than a crash loop.
        logger.exception("setup_schedules failed during entrypoint boot")
        try:
            from jobs.schedule_reconciliation import (
                build_schedule_definitions,
                expected_schedule_names,
                publish_schedule_reconciliation_state,
            )
            from website.release_phase import background_work_enabled

            definitions = build_schedule_definitions(
                background_enabled=background_work_enabled(),
            )
            publish_schedule_reconciliation_state(
                status="degraded",
                expected_names=expected_schedule_names(definitions),
                error=exc,
            )
        except Exception:
            # Diagnostics are valuable but remain subordinate to the explicit
            # fail-open boot contract.
            logger.exception(
                "schedule reconciliation degraded state publication failed"
            )


def _suppress_r1_incompatible_schedules():
    """Delete stale R2 work vocabulary and propagate any failure.

    Unlike ordinary schedule registration, this is release safety-critical:
    continuing after a failed delete could let an overlapping old worker claim
    work it does not understand.
    """
    from django.core.management import call_command

    print("Suppress R1-incompatible recurring schedules", flush=True)
    call_command("suppress_r1_incompatible_schedules", verbosity=0)


def _reconcile_r1_expand():
    """Repair compatibility writes before web serves or a worker consumes."""

    from django.core.management import call_command

    print("Reconcile R1 expanded-schema compatibility writes", flush=True)
    call_command("reconcile_r1_expand", verbosity=0)


def _serving_boot_smoke_check_enabled():
    return _truthy_env("SERVING_BOOT_SMOKE_CHECK_ENABLED", default=True)


def _run_serving_boot_smoke_check():
    """Run cheap deploy-critical checks before Django setup.

    This is intentionally much narrower than ``manage.py check``. It does not
    import URLconfs, models, templates, or hit the database; it only validates
    raw env values whose absence would create a known production incident.
    """
    if not _serving_boot_smoke_check_enabled():
        print(
            "Skipping serving boot smoke check "
            "(SERVING_BOOT_SMOKE_CHECK_ENABLED is not true)",
            flush=True,
        )
        return

    debug = _truthy_env("DEBUG", default=True)
    relay_enabled = bool(os.environ.get("RELAY_API_KEY", "").strip() or os.environ.get("RELAY_BASE_URL", "").strip())
    if not debug and not relay_enabled:
        raise RuntimeError(
            "SERVING_BOOT_SMOKE_CHECK failed: DEBUG=false but "
            "Relay is not configured (and SES send paths no longer exist). Transactional email would fail. "
            "Set RELAY_API_KEY / RELAY_BASE_URL or set "
            "SERVING_BOOT_SMOKE_CHECK_ENABLED=false for an intentional "
            "non-sending environment."
        )

    print("Serving boot smoke check passed", flush=True)


def _start_gunicorn(workers):
    """Hand off to gunicorn IN THIS PROCESS (web serving path).

    ``gunicorn.app.wsgiapp.run`` reads its CLI from ``sys.argv``, so we
    rewrite argv to look like the previous CMD line. ``--preload`` makes the
    master load the app once and fork workers without re-importing -- the
    cold-start gain carries across all workers.
    """
    print("Starting server", flush=True)
    # Issue #1854: only this serving process caps SQL statements; migrate
    # (already finished above on the legacy path), qcluster and management
    # commands never reach this function.
    apply_web_statement_timeout(web_statement_timeout_ms())
    sys.argv = gunicorn_argv(workers, gunicorn_thread_count())
    from gunicorn.app.wsgiapp import run as gunicorn_run
    gunicorn_run()


def _start_qcluster():
    """Hand off to the django-q cluster IN THIS PROCESS (worker path)."""
    # Inline import mirrors the rest of this entrypoint: Django management is
    # imported lazily so importing the module stays cheap and does not touch
    # Django before settings are configured.
    from django.core.management import call_command

    print("Starting django-q cluster", flush=True)
    os.environ["DJANGO_QCLUSTER_PROCESS"] = "true"
    call_command("qcluster", verbosity=1)


def _run_migrate(phases):
    """Apply DB migrations, timed. Raises (propagates) on migration failure.

    Migrations run from a SINGLE container per deploy. Two containers (web +
    worker) starting in parallel from the same image race any migration with
    both DDL and data steps (e.g. integrations.0021) and deadlock against the
    same database (issue #336). The sole migrator is the source Service web
    container (``RUN_MIGRATIONS=true`` with ``BOOT_MODE`` absent).
    """
    from django.core.management import call_command

    print("Apply database migrations", flush=True)
    _timed(
        "migrate",
        lambda: call_command("migrate", interactive=False, verbosity=1),
        record=phases,
    )
    print("Database migrations applied successfully.", flush=True)


def _initialize_runtime_observability():
    """Apply Studio observability settings after Django setup, before serve."""
    from integrations.services.observability import init_logfire

    init_logfire(use_runtime_config=True)


def _finalize_serving(role, phases, boot_start):
    """Register schedules, emit the total line, and persist boot timing.

    Shared by the ``web`` and ``worker`` serving paths (issue #1141 Phase 2A)
    and the legacy fallback.
    """
    # R1 web runs this after migrate and before serving. The worker rolls only
    # after web is verified, then reconciles any writes made by the overlapping
    # production web before it can consume queue work.
    _timed("reconcile_r1_expand", _reconcile_r1_expand, record=phases)

    # Safety-critical and intentionally outside best-effort registration.
    _timed(
        "suppress_r1_schedules",
        _suppress_r1_incompatible_schedules,
        record=phases,
    )

    # Issue #708: register the django-q schedules on every boot for both web
    # and worker. Idempotent; failures are logged and swallowed so a bad
    # schedule cannot crash the container.
    _timed("setup_schedules", _register_schedules, record=phases)

    # AppConfig.ready() cannot read IntegrationSetting while Django is still
    # populating apps. This serving-only pass runs after django.setup() and may
    # apply a Studio override before gunicorn binds or qcluster starts polling.
    _initialize_runtime_observability()

    # Issue #1141 Phase 1: total pre-serve path (process start -> just before
    # the gunicorn/qcluster handoff).
    total = time.perf_counter() - boot_start
    _emit_timing("total", total)
    phases["total"] = total

    # Issue #1142: persist the captured per-phase numbers to the shared
    # ``django_q`` cache BEFORE the blocking handoff so the boot-timing
    # diagnostics endpoint can read them. Fail-safe: a store failure is logged
    # and swallowed and never crashes the container.
    persist_boot_timing(role, phases)


def _run_legacy(phases, boot_start):
    """``BOOT_MODE`` ABSENT: legacy serving path keyed off ``RUN_MIGRATIONS``.

    * web (``RUN_MIGRATIONS=true``): migrate -> schedules -> gunicorn
    * worker (``RUN_MIGRATIONS!=true``): schedules -> qcluster
    """
    run_migrations = os.environ.get("RUN_MIGRATIONS") == "true"

    if run_migrations:
        _run_migrate(phases)
    else:
        print(
            "Skipping migrations on this container (RUN_MIGRATIONS != true)",
            flush=True,
        )

    role = "worker"
    if run_migrations:
        role = "web"
    _finalize_serving(role, phases, boot_start)

    if run_migrations:
        _start_gunicorn(_gunicorn_worker_count())
    else:
        _start_qcluster()


def main():
    # Issue #1141 Phase 1: capture process-start reference for the final
    # ``BOOT_TIMING phase=total`` line. ``perf_counter()`` here is the
    # earliest point in main; the small pre-main interpreter/import cost is
    # negligible relative to the per-phase seconds we are measuring.
    boot_start = time.perf_counter()

    # Issue #1142: accumulate the per-phase elapsed seconds captured by
    # ``_timed`` (the same numbers ``_emit_timing`` prints, single source of
    # truth) so the whole boot can be persisted once at the end.
    phases = {}

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "website.settings")
    _timed("smoke_check", _run_serving_boot_smoke_check, record=phases)
    # Time settings import + app registry population (incl.
    # integrations.apps.ready() -> Logfire). See issue #1141 Phase 1.
    _timed("django_setup", django.setup, record=phases)

    # Dispatch on BOOT_MODE (web / worker), falling back to the legacy
    # RUN_MIGRATIONS behavior when it is absent.
    boot_mode = os.environ.get("BOOT_MODE")

    if boot_mode == "web":
        _finalize_serving("web", phases, boot_start)
        _start_gunicorn(_gunicorn_worker_count())
        return

    if boot_mode == "worker":
        _finalize_serving("worker", phases, boot_start)
        _start_qcluster()
        return

    # BOOT_MODE absent -> exact legacy behavior.
    _run_legacy(phases, boot_start)


if __name__ == "__main__":
    main()
