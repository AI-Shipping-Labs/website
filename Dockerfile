FROM node:24-slim AS css-builder

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts

# Tailwind scans templates plus first-party Python/JavaScript producers. Copy
# the source tree only into this disposable build stage and export the one
# generated bundle; Node/npm/node_modules never enter the Python runtime image.
COPY . .
RUN npm run css:build


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
COPY --from=css-builder /app/static/css/tailwind.css /app/static/css/tailwind.css

# Collect static files with the same storage backend used at runtime.
RUN DEBUG=False SECRET_KEY=collectstatic-build-secret ALLOWED_HOSTS=localhost \
    uv run python manage.py collectstatic --noinput

RUN chmod +x entrypoint.sh

EXPOSE 8000

# entrypoint.sh execs "$@" when Compose / docker run supply a command.
# With empty argv (ECS web/worker/predeploy) it delegates to
# scripts/entrypoint_init.py, which imports Django settings ONCE, runs
# migrate / check, then spawns gunicorn (web) or qcluster (worker) in the
# same Python process. The django_q_cache table is created by an
# email_app migration during migrate, not as a separate boot step.
# No CMD — Compose supplies command:; ECS relies on empty argv.
ENTRYPOINT ["/app/entrypoint.sh"]
