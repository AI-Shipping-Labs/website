# Multi-platform OCI digests verified on 2026-11-10.
FROM node:24-slim@sha256:01d566e8d94d8c619591d78652babfccd3c6992a3dffa9ed5ab39e11cf6c70e9 AS css-builder

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts

# Tailwind scans templates plus first-party Python/JavaScript producers. Copy
# the source tree only into this disposable build stage and export the one
# generated bundle; Node/npm/node_modules never enter the Python runtime image.
COPY . .
RUN npm run css:build


FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

ARG VERSION=""
ENV VERSION=${VERSION}
LABEL org.opencontainers.image.revision=${VERSION}

WORKDIR /app

# System deps
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

# The application only reads files under /app at runtime. Run it without root
# privileges; writable runtime state belongs in /tmp or external services.
RUN adduser --system --group --home /home/app app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 /uv /usr/local/bin/uv

# Install Python deps (cached layer)
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen
ENV UV_NO_SYNC=1

# Copy application code
COPY . .
COPY --from=css-builder /app/static/css/tailwind.css /app/static/css/tailwind.css

# Collect static files with the same storage backend used at runtime.
RUN DEBUG=False SECRET_KEY=collectstatic-build-secret ALLOWED_HOSTS=localhost \
    uv run python manage.py collectstatic --noinput
RUN chown -R app:app /app/staticfiles

RUN chmod +x entrypoint.sh

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1
USER app:app

EXPOSE 8000

# entrypoint.sh execs "$@" when Compose / docker run supply a command.
# With empty argv (ECS web/worker/predeploy) it delegates to
# scripts/entrypoint_init.py, which imports Django settings ONCE, runs
# migrate / check, then spawns gunicorn (web) or qcluster (worker) in the
# same Python process. The django_q_cache table is created by an
# email_app migration during migrate, not as a separate boot step.
# No CMD — Compose supplies command:; ECS relies on empty argv.
ENTRYPOINT ["/app/entrypoint.sh"]
