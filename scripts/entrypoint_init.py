"""Single-process container entrypoint.

Replaces the entrypoint.sh that ran three separate ``manage.py`` processes
(migrate / createcachetable / check) before exec-ing gunicorn. Each
process re-imported ``website/settings.py`` and re-paid the eager
AWS-network cost (Secrets Manager + RDS DatabaseCache + IntegrationSetting
query), adding about 30s of avoidable startup latency.

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

import os
import sys
import time

import django

from scripts.entrypoint_routines import (
    _initialize_runtime_observability,
    _reconcile_r1_expand,
    _register_schedules,
    _suppress_r1_incompatible_schedules,
)
from scripts.entrypoint_timing import _emit_timing, _timed, persist_boot_timing
from scripts.gunicorn_runtime import (
    apply_web_statement_timeout,
    gunicorn_argv,
    gunicorn_thread_count,
    web_statement_timeout_ms,
)
from scripts.gunicorn_runtime import gunicorn_worker_count as _gunicorn_worker_count

_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})


def _truthy_env(name, *, default=False):
    if name not in os.environ:
        return default
    return os.environ[name].strip().lower() in _TRUTHY_ENV_VALUES


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
