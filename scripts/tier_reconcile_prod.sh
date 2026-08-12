#!/usr/bin/env bash

set -euo pipefail

echo "Warning: scripts/tier_reconcile_prod.sh is deprecated; delegating to the read-only cohort report." >&2
exec uv run asl tier-reconcile run "$@"
