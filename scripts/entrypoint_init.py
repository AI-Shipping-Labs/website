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

* web (RUN_MIGRATIONS=true)  -> migrate, check, gunicorn
* worker (RUN_MIGRATIONS!=true) -> check, qcluster

The worker still benefits from the single-process boot: only one settings
import, no Secrets Manager / RDS round trips before django-q starts
polling.

The ``django_q_cache`` table (used by the worker-heartbeat
``DatabaseCache`` backend) is created by an ``email_app`` migration, so
it lands during ``migrate`` on the web container rather than on every
boot.
"""

import os
import sys

import django


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "website.settings")
    django.setup()

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
        call_command("migrate", interactive=False, verbosity=1)
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
    call_command("check", "--fail-level", "ERROR")

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
