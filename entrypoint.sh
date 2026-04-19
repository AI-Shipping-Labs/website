#!/bin/sh

echo "Apply database migrations"
uv run python manage.py migrate

if [ $? -ne 0 ]; then
    echo "Failed to apply database migrations."
    exit 1
else
    echo "Database migrations applied successfully."
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

echo "Starting server"
exec "$@"
