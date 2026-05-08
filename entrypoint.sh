#!/bin/sh

# Migrations run from a single container per task. Two containers (web +
# worker) start in parallel from the same image and entrypoint, and any
# migration with both DDL and data steps (e.g. integrations.0021) deadlocks
# when run concurrently against the same database. Issue #336.
if [ "${RUN_MIGRATIONS}" = "true" ]; then
    echo "Apply database migrations"
    uv run python manage.py migrate

    if [ $? -ne 0 ]; then
        echo "Failed to apply database migrations."
        exit 1
    else
        echo "Database migrations applied successfully."
    fi
else
    echo "Skipping migrations on this container (RUN_MIGRATIONS != true)"
fi

# Create the django-q cache table. The /studio/worker/ dashboard reads
# cluster heartbeats from CACHES['django_q'] (DatabaseCache), and that
# backend requires a table created via createcachetable. The command is
# idempotent, so it's safe to run on every container start.
echo "Ensure django-q cache table exists"
uv run python manage.py createcachetable django_q_cache

if [ $? -ne 0 ]; then
    echo "Failed to create django-q cache table."
    exit 1
fi

# Issue #529: defence-in-depth gate against a misconfigured deploy. Runs
# against the actual platform env vars (real DEBUG, real SES_ENABLED). If
# a registered system check fires at Error level (e.g. email_app.E001
# when SES_ENABLED is missing from the prod task definition), the
# container exits non-zero, ECS marks it unhealthy, and the rollout halts.
# Order: migrate -> createcachetable -> check -> exec, so a fresh DB is
# migrated before any future check that hits the ORM runs.
echo "Run Django system checks (fail on Error level)"
uv run python manage.py check --fail-level ERROR

if [ $? -ne 0 ]; then
    echo "Django system checks failed. Refusing to start the container."
    exit 1
fi

echo "Starting server"
exec "$@"
