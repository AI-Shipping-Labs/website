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

Web vs worker container
=======================

Both containers share the same Docker ENTRYPOINT. The web task definition
sets ``RUN_MIGRATIONS=true``; the worker container (cloned from web with
``command`` overridden in ``deploy/update_task_def.py``) does not. We use
that env var as the dispatch flag:

* web (RUN_MIGRATIONS=true)  -> migrate, check, register schedules, gunicorn
* worker (RUN_MIGRATIONS!=true) -> check, register schedules, qcluster

The worker still benefits from the single-process boot: only one settings
import, no Secrets Manager / RDS round trips before django-q starts
polling.

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


def _emit_timing(phase, seconds):
    """Print a single flushed ``BOOT_TIMING`` line for CloudWatch.

    Format matches the ``CI_TIMING phase=... seconds=...`` convention used
    in ``.github/workflows/deploy-dev.yml`` so ops can grep both with the
    same tooling. ``flush=True`` is mandatory: the container may crash in a
    later phase before gunicorn binds, and the line must still reach
    CloudWatch. See issue #1141 Phase 1.
    """
    print(f"BOOT_TIMING phase={phase} seconds={seconds:.3f}", flush=True)


def _timed(phase, fn):
    """Run ``fn()``, emit its elapsed ``BOOT_TIMING`` line, return its result.

    Purely additive observability: it does NOT change ordering, behavior, or
    crash semantics of the wrapped phase. The timing line is emitted from a
    ``finally`` block so a phase that raises still records its elapsed time
    in CloudWatch before the exception propagates unchanged. ``fn``'s return
    value is passed straight through to the caller.

    Uses ``time.perf_counter()`` for a monotonic wall-clock measurement.
    """
    start = time.perf_counter()
    try:
        return fn()
    finally:
        _emit_timing(phase, time.perf_counter() - start)


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


def main():
    # Issue #1141 Phase 1: capture process-start reference for the final
    # ``BOOT_TIMING phase=total`` line. ``perf_counter()`` here is the
    # earliest point in main; the small pre-main interpreter/import cost is
    # negligible relative to the per-phase seconds we are measuring.
    boot_start = time.perf_counter()

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "website.settings")
    # Time settings import + app registry population (incl.
    # integrations.apps.ready() -> Logfire). See issue #1141 Phase 1.
    _timed("django_setup", django.setup)

    from django.core.management import call_command

    run_migrations = os.environ.get("RUN_MIGRATIONS") == "true"

    if run_migrations:
        # Migrations run from a single container per task. Two containers
        # (web + worker) start in parallel from the same image, and any
        # migration with both DDL and data steps (e.g. integrations.0021)
        # deadlocks when run concurrently against the same database.
        # See issue #336. ``deploy/update_task_def.py`` sets
        # ``RUN_MIGRATIONS=true`` on the web container only.
        print("Apply database migrations", flush=True)
        _timed(
            "migrate",
            lambda: call_command("migrate", interactive=False, verbosity=1),
        )
        print("Database migrations applied successfully.", flush=True)
    else:
        print(
            "Skipping migrations on this container (RUN_MIGRATIONS != true)",
            flush=True,
        )

    # Issue #529: defence-in-depth gate against a misconfigured deploy.
    # Runs against the actual platform env vars (real DEBUG, real
    # SES_ENABLED). If a registered system check fires at Error level
    # (e.g. email_app.E001 when SES_ENABLED is missing from the prod task
    # definition), the container exits non-zero, ECS marks it unhealthy,
    # and the rollout halts. Order: migrate -> check -> serve, so a fresh
    # DB is migrated (including the email_app migration that creates the
    # ``django_q_cache`` table) before any future check that hits the ORM
    # runs.
    print("Run Django system checks (fail on Error level)", flush=True)
    _timed("check", lambda: call_command("check", "--fail-level", "ERROR"))

    # Issue #708: register the django-q schedules (including
    # ``complete-finished-events``) on every boot for both web and worker.
    # Idempotent; failures are logged and swallowed so a bad schedule
    # cannot crash the container.
    _timed("setup_schedules", _register_schedules)

    # Issue #1141 Phase 1: total pre-serve path (process start -> just
    # before the gunicorn/qcluster handoff). This also captures the
    # "time to Starting server / Starting django-q cluster" handoff figure.
    _emit_timing("total", time.perf_counter() - boot_start)

    if run_migrations:
        # Web container -> hand off to gunicorn IN THIS PROCESS.
        # ``gunicorn.app.wsgiapp.run`` reads its CLI from sys.argv, so we
        # rewrite argv to look like the previous CMD line. ``--preload``
        # makes the master load the app once and fork workers without
        # re-importing -- the cold-start gain carries across all workers.
        print("Starting server", flush=True)
        sys.argv = [
            "gunicorn",
            "website.wsgi:application",
            "--bind", "0.0.0.0:8000",
            "--workers", "3",
            "--preload",
        ]
        from gunicorn.app.wsgiapp import run as gunicorn_run
        gunicorn_run()
    else:
        # Worker container -> django-q cluster, in-process.
        print("Starting django-q cluster", flush=True)
        os.environ["DJANGO_QCLUSTER_PROCESS"] = "true"
        call_command("qcluster", verbosity=1)


if __name__ == "__main__":
    main()
