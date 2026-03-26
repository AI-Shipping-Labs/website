#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Creating .env from .env.example (if needed)"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "    Created .env — edit it with your credentials"
else
  echo "    .env already exists, skipping"
fi

echo "==> Installing Python dependencies"
uv sync

echo "==> Cloning content repo (if needed)"
CONTENT_REPO="_content-repo"
if [ ! -d "$CONTENT_REPO" ]; then
  git clone git@github.com:AI-Shipping-Labs/content.git "$CONTENT_REPO"
else
  echo "    $CONTENT_REPO already exists, pulling latest"
  git -C "$CONTENT_REPO" pull --ff-only || true
fi

echo "==> Running migrations"
uv run python manage.py migrate

echo "==> Seeding database"
uv run python manage.py seed_data

echo "==> Syncing content from local clone"
uv run python manage.py seed_content_sources
uv run python manage.py sync_content --from-disk "$CONTENT_REPO"

echo ""
echo "Done! Run 'make run' to start the dev server."
