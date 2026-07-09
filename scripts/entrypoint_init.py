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

All containers share the same Docker ENTRYPOINT (it does NOT consume
``$@``, so an ECS ``command`` override cannot select a run mode). The
``BOOT_MODE`` env var selects what this process does. It untangles two
concerns the old ``RUN_MIGRATIONS`` flag conflated: web-vs-worker role
dispatch, and whether this boot runs ``migrate``.

* ``BOOT_MODE=predeploy`` -> ``django.setup`` -> ``migrate`` ->
  ``check --fail-level ERROR`` -> exit 0. NO schedules, NO gunicorn, NO
  qcluster. This is the single pre-deploy one-off task (``aws ecs
  run-task``) that ``deploy/deploy_dev.sh`` runs BEFORE rolling the
  service. A non-zero exit from migrate or check propagates so the task
  fails and the deploy aborts without rolling the service. It is the
  SINGLE migrator (#336) and it runs the #529 misconfig gate against the
  real serving env.
* ``BOOT_MODE=web`` -> ``django.setup`` -> register schedules -> gunicorn
  bind. SKIPS migrate and check (they ran in the pre-deploy task), so the
  serving container binds fast.
* ``BOOT_MODE=worker`` -> ``django.setup`` -> register schedules ->
  qcluster. SKIPS migrate and check.
* ``BOOT_MODE`` ABSENT -> no-infra serving path, keyed off
  ``RUN_MIGRATIONS`` as before: web (``RUN_MIGRATIONS=true``) migrates ->
  schedules -> gunicorn; worker schedules -> qcluster. The expensive
  ``check --fail-level ERROR`` step is skipped by default on serving boot
  because deploy CI already runs it before the image is built. Set
  ``SERVING_BOOT_CHECK_ENABLED=true`` to restore the old serving-boot gate.

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

Failures here MUST NOT crash the container: a regression in
``setup_schedules`` (e.g. a bad task path) cannot be allowed to take the
web tier down. We log and continue.
"""

import logging
import os
import sys
import time

import django

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

    The build ``tag`` comes from the ``VERSION`` env var
    (``{DATE}-{SHORT_SHA}``, set by ``deploy/update_task_def.py``), falling
    back to ``"unknown"`` for local runs.

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
    except Exception:
        # Log the traceback but keep booting. A bad schedule entry cannot
        # be allowed to take the web tier down; the worst-case fallout is
        # that one cron does not fire until the next deploy, which is
        # still better than a crash loop.
        logger.exception("setup_schedules failed during entrypoint boot")


def _gunicorn_worker_count():
    """Return the gunicorn ``--workers`` count from ``os.environ`` (default 3).

    Issue #1141 Phase 2C. This is read from ``GUNICORN_WORKERS`` and MUST NOT
    go through the ``IntegrationSetting`` / ``get_config`` framework. It is a
    deploy-time value consumed on the gunicorn master BEFORE the app serves a
    single request and before the runtime is fully wired: the DB override
    cannot be consulted pre-boot, and querying RDS here would re-introduce the
    exact pre-bind DB round-trip Phase 2 is removing. A non-integer /
    non-positive value falls back to the default 3 with a logged warning so a
    bad env var can never crash boot.
    """
    raw = os.environ.get("GUNICORN_WORKERS")
    if raw is None:
        return 3
    try:
        count = int(raw)
    except (TypeError, ValueError):
        count = 0
    if count <= 0:
        logger.warning(
            "Invalid GUNICORN_WORKERS=%r; falling back to 3 workers", raw,
        )
        return 3
    return count


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
    ses_enabled = _truthy_env("SES_ENABLED", default=False)
    if not debug and not ses_enabled:
        raise RuntimeError(
            "SERVING_BOOT_SMOKE_CHECK failed: DEBUG=false but "
            "SES_ENABLED is not true. Transactional email would silently "
            "no-op. Set SES_ENABLED=true or set "
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
    sys.argv = [
        "gunicorn",
        "website.wsgi:application",
        "--bind", "0.0.0.0:8000",
        "--workers", str(workers),
        "--preload",
    ]
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
    same database (issue #336). Under ``BOOT_MODE`` the sole migrator is the
    ``predeploy`` one-off task; in the legacy fallback it is the web container
    (``RUN_MIGRATIONS=true``).
    """
    from django.core.management import call_command

    print("Apply database migrations", flush=True)
    _timed(
        "migrate",
        lambda: call_command("migrate", interactive=False, verbosity=1),
        record=phases,
    )
    print("Database migrations applied successfully.", flush=True)


def _run_check(phases):
    """Run the #529 system-check gate, timed. Propagates on Error-level checks.

    Issue #529: defence-in-depth gate against a misconfigured deploy. Runs
    against the actual platform env vars (real DEBUG, real SES_ENABLED). If a
    registered system check fires at Error level (e.g. email_app.E001 when
    SES_ENABLED is missing from the task definition), ``call_command`` raises,
    the process exits non-zero, and the deploy halts. Under ``BOOT_MODE`` this
    runs in the ``predeploy`` one-off task (so a bad config fails the DEPLOY
    before any container serves); in the legacy fallback it runs on the
    serving boot as before.
    """
    from django.core.management import call_command

    print("Run Django system checks (fail on Error level)", flush=True)
    _timed(
        "check",
        lambda: call_command("check", "--fail-level", "ERROR"),
        record=phases,
    )


def _serving_boot_check_enabled():
    """Return whether legacy serving boot should run ``manage.py check``.

    This defaults OFF as the no-infra fast-start path: the deploy workflow
    already runs the same Error-level Django check before build, and paying it
    again inside every ECS serving container dominates cold-start time. Keep a
    simple env override for incident response and local parity checks.
    """
    value = os.environ.get("SERVING_BOOT_CHECK_ENABLED", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _run_predeploy(phases):
    """``BOOT_MODE=predeploy``: migrate + #529 check, then return (exit 0).

    Runs NO schedules, NO gunicorn, NO qcluster. A non-zero exit from migrate
    or check propagates (the exception is not caught here) so the pre-deploy
    one-off ECS task fails and ``deploy/deploy_dev.sh`` aborts the deploy
    WITHOUT rolling the service. This is the single migrator (#336) and the
    #529 misconfig gate, moved off every serving container's pre-bind path.

    We deliberately do NOT persist ``boot_timing:web``/``:worker`` from here:
    a predeploy task is not a serving container. The per-phase BOOT_TIMING
    lines for ``django_setup`` / ``migrate`` / ``check`` are still emitted by
    ``_timed`` so the pre-deploy task's CloudWatch logs remain diagnosable.
    """
    _run_migrate(phases)
    _run_check(phases)


def _finalize_serving(role, phases, boot_start):
    """Register schedules, emit the total line, and persist boot timing.

    Shared by the ``web`` and ``worker`` serving paths (issue #1141 Phase 2A)
    and the legacy fallback. ``migrate`` and ``check`` are NOT run here under
    ``BOOT_MODE`` -- they were decoupled into the pre-deploy task.
    """
    # Issue #708: register the django-q schedules on every boot for both web
    # and worker. Idempotent; failures are logged and swallowed so a bad
    # schedule cannot crash the container.
    _timed("setup_schedules", _register_schedules, record=phases)

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
    """``BOOT_MODE`` ABSENT: no-infra serving path.

    Keyed off ``RUN_MIGRATIONS`` as before so a partial rollout without
    ``BOOT_MODE`` never leaves the DB un-migrated:

    * web (``RUN_MIGRATIONS=true``): migrate -> schedules -> gunicorn
    * worker (``RUN_MIGRATIONS!=true``): schedules -> qcluster

    ``manage.py check --fail-level ERROR`` remains available behind
    ``SERVING_BOOT_CHECK_ENABLED=true`` but defaults off in deployed serving
    tasks because the deploy workflow runs the same check before build.
    """
    run_migrations = os.environ.get("RUN_MIGRATIONS") == "true"

    if run_migrations:
        _run_migrate(phases)
    else:
        print(
            "Skipping migrations on this container (RUN_MIGRATIONS != true)",
            flush=True,
        )

    if _serving_boot_check_enabled():
        # If explicitly enabled, keep the safe ordering: migrate first so a
        # fresh DB has tables such as django_q_cache before ORM-backed checks.
        _run_check(phases)
    else:
        print(
            "Skipping Django system checks on serving boot "
            "(SERVING_BOOT_CHECK_ENABLED is not true)",
            flush=True,
        )

    _finalize_serving("web" if run_migrations else "worker", phases, boot_start)

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

    # Issue #1141 Phase 2A: dispatch on BOOT_MODE (predeploy / web / worker),
    # falling back to the legacy RUN_MIGRATIONS behavior when it is absent.
    boot_mode = os.environ.get("BOOT_MODE")

    if boot_mode == "predeploy":
        # Migrate + #529 check, then return -> process exits 0 (unless a
        # phase raised, which propagates and fails the pre-deploy task).
        _run_predeploy(phases)
        return

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
