#!/usr/bin/env bash
# Interactive helper to reconcile the "free + override" prod users against Stripe.
# Reads the staff token from .env. Runs diagnostics, prompts, runs dry-run, prompts,
# then runs the real apply.
#
# Usage:
#   ./scripts/tier_reconcile_prod.sh
#
# Endpoints (already on prod after the 2026-05-15 promotion):
#   GET  /api/payments/tier-reconcile/diagnostics
#   POST /api/payments/tier-reconcile

set -euo pipefail

HOST="${HOST:-https://aishippinglabs.com}"
ENV_FILE="${ENV_FILE:-.env}"

if [ ! -f "$ENV_FILE" ]; then
  echo "Error: $ENV_FILE not found. Run from the repo root or set ENV_FILE=path/to/.env" >&2
  exit 1
fi

# shellcheck disable=SC1090
TOKEN="$(grep -E '^API_SHIPPING_LABS_API_TOKEN=' "$ENV_FILE" | head -n 1 | cut -d= -f2-)"
if [ -z "$TOKEN" ]; then
  echo "Error: API_SHIPPING_LABS_API_TOKEN not set in $ENV_FILE" >&2
  exit 1
fi

AUTH_HEADER="Authorization: Token $TOKEN"

confirm() {
  local prompt="$1"
  read -r -p "$prompt [y/N] " ans
  case "$ans" in [yY]|[yY][eE][sS]) return 0 ;; *) return 1 ;; esac
}

echo "=== Step 1: diagnostics (read-only) ==="
diag_response="$(curl -sS -X GET "$HOST/api/payments/tier-reconcile/diagnostics" -H "$AUTH_HEADER")"
echo "$diag_response" | python3 -m json.tool

count="$(echo "$diag_response" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("count","?"))')"
echo
echo "Diagnostics says $count users have a tier mismatch with Stripe."

if [ "$count" = "0" ]; then
  echo "Nothing to reconcile. Done."
  exit 0
fi

echo
if ! confirm "Proceed to dry-run (no writes, but real Stripe lookups)?"; then
  echo "Aborted."
  exit 0
fi

echo
echo "=== Step 2: dry-run apply (no DB writes) ==="
dry_response="$(curl -sS -X POST "$HOST/api/payments/tier-reconcile" \
  -H "$AUTH_HEADER" \
  -H "Content-Type: application/json" \
  -d '{"dry_run": true}')"
echo "$dry_response" | python3 -m json.tool

echo
if ! confirm "Dry-run looks correct? Run the real reconcile?"; then
  echo "Aborted. No writes made."
  exit 0
fi

echo
echo "=== Step 3: real apply (writes user.tier) ==="
real_response="$(curl -sS -X POST "$HOST/api/payments/tier-reconcile" \
  -H "$AUTH_HEADER" \
  -H "Content-Type: application/json" \
  -d '{"dry_run": false}')"
echo "$real_response" | python3 -m json.tool

echo
echo "Done. Stale TierOverride rows (if any) are now harmless — user.tier is the source of truth."
echo "If you want to delete them too, that's a separate step (best done after confirming the reconcile result)."
