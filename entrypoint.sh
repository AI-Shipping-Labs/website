#!/bin/sh

echo "Apply database migrations"
uv run python manage.py migrate

if [ $? -ne 0 ]; then
    echo "Failed to apply database migrations."
    exit 1
else
    echo "Database migrations applied successfully."
fi

echo "Starting server"
exec "$@"
