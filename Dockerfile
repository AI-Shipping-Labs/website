FROM python:3.13-slim

WORKDIR /app

# System deps
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install Python deps (cached layer)
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen

# Copy application code
COPY . .

# Collect static files with the same storage backend used at runtime.
RUN DEBUG=False SECRET_KEY=collectstatic-build-secret ALLOWED_HOSTS=localhost \
    uv run python manage.py collectstatic --noinput

RUN chmod +x entrypoint.sh

EXPOSE 8000

# entrypoint.sh delegates to scripts/entrypoint_init.py, which imports
# Django settings ONCE, runs migrate / createcachetable / check, then
# spawns gunicorn (web) or qcluster (worker) in the same Python process.
# No CMD — the entrypoint does not consume "$@".
ENTRYPOINT ["/app/entrypoint.sh"]
