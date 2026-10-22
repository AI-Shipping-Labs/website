#!/bin/bash
# Hetzner-side auto-deploy for AISHL Coolify staging.
#
# Why this exists: GitHub Actions runners cannot reach the Coolify API at
# http://169.58.119.163:8000 (connection times out from the internet; the
# port is effectively Hetzner/DC-local). So the GHA `deploy` job only
# builds+pushes the GHCR image, and THIS poller (cron on RMTHZ, which
# reaches :8000 fine) pins the newest GHCR tag into the Coolify service
# compose and triggers the deploy. Same pin+deploy logic the GHA job used
# to run, minus the unreachable network path.
#
# Install (RMTHZ, run once):
#   cp deploy/coolify/hetzner-auto-deploy.sh ~/tmp/bin/coolify-aisl-auto-deploy.sh
#   chmod +x ~/tmp/bin/coolify-aisl-auto-deploy.sh
#   (crontab -l 2>/dev/null; echo "*/5 * * * * flock -n /tmp/coolify-aisl-auto-deploy.lock ~/tmp/bin/coolify-aisl-auto-deploy.sh >> /tmp/opencode/aisl-auto-deploy.log 2>&1") | crontab -
#
# Inputs (files, never CLI args so secrets stay out of ps/logs):
#   ~/tmp/secrets/coolify-ghcr-read.pat        classic PAT, read:packages
#   ~/tmp/secrets/coolify-api-169.58.119.163.token   Coolify API token
#
# Inputs (env, overridable): COOLIFY_URL, SERVICE_UUID, GHCR_IMAGE.
set -euo pipefail

COOLIFY_URL="${COOLIFY_URL:-http://169.58.119.163:8000}"
SERVICE_UUID="${SERVICE_UUID:-mekt9kz74fvbixla6olinuma}"
GHCR_IMAGE="${GHCR_IMAGE:-ai-shipping-labs/website}"
GH_PAT_FILE="${GH_PAT_FILE:-$HOME/tmp/secrets/coolify-ghcr-read.pat}"
COOLIFY_TOKEN_FILE="${COOLIFY_TOKEN_FILE:-$HOME/tmp/secrets/coolify-api-169.58.119.163.token}"

echo "--- $(date -u +%FT%TZ) aisl auto-deploy check ---"

GH_PAT="$(cat "$GH_PAT_FILE")"
COOLIFY_TOKEN="$(cat "$COOLIFY_TOKEN_FILE")"

LATEST_TAG="$(curl -sf -H "Authorization: Bearer ${GH_PAT}" \
  -H 'Accept: application/vnd.github+json' \
  "https://api.github.com/orgs/AI-Shipping-Labs/packages/container/website/versions?per_page=20" \
  | python3 -c "
import json, sys
versions = json.load(sys.stdin)
tagged = []
for v in versions:
    tags = ((v.get('metadata', {}) or {}).get('container', {}) or {}).get('tags', []) or []
    dated = [t for t in tags if t[:8].isdigit()]
    if dated:
        tagged.append((v['created_at'], sorted(dated)[0]))
tagged.sort()
print(tagged[-1][1] if tagged else '')
")"
if [[ -z "$LATEST_TAG" ]]; then
  echo "no dated GHCR tag found; giving up"
  exit 1
fi
echo "newest GHCR tag: ${GHCR_IMAGE}:${LATEST_TAG}"

CURRENT_IMAGE="$(curl -sf -H "Authorization: Bearer ${COOLIFY_TOKEN}" \
  -H 'Accept: application/json' \
  "${COOLIFY_URL}/api/v1/services/${SERVICE_UUID}" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['applications'][0]['image'])")"
echo "service image now: ${CURRENT_IMAGE}"

if [[ "$CURRENT_IMAGE" == "${GHCR_IMAGE}:${LATEST_TAG}" ]]; then
  echo "in sync; nothing to do"
  exit 0
fi

echo "pinning ${LATEST_TAG} and deploying..."
COOLIFY_URL="$COOLIFY_URL" COOLIFY_TOKEN="$COOLIFY_TOKEN" \
SERVICE_UUID="$SERVICE_UUID" IMAGE_REF="ghcr.io/${GHCR_IMAGE}" \
IMAGE_TAG="$LATEST_TAG" python3 - <<'PY'
import base64, json, os, re, urllib.request

base = os.environ["COOLIFY_URL"].rstrip("/")
token = os.environ["COOLIFY_TOKEN"]
uuid = os.environ["SERVICE_UUID"]
image_ref = os.environ["IMAGE_REF"]
image_tag = os.environ["IMAGE_TAG"]
prefix = f"{image_ref}:"

def call(method, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{base}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = resp.read()
        return resp.status, (json.loads(body) if body else {})

_st, svc = call("GET", f"/api/v1/services/{uuid}")
raw = svc.get("docker_compose_raw") or ""
new, n = re.subn(re.escape(prefix) + r"[^\s'\"]+", prefix + image_tag, raw, count=1)
if n != 1:
    raise SystemExit(f"Could not pin image {prefix}* in Coolify compose (matches={n})")
call("PATCH", f"/api/v1/services/{uuid}",
     {"docker_compose_raw": base64.b64encode(new.encode()).decode()})
_st, out = call("POST", f"/api/v1/deploy?uuid={uuid}&force=true")
print(json.dumps(out))
print(f"Pinned {prefix}{image_tag} and triggered Coolify deploy for {uuid}")
PY
echo "deploy triggered"
