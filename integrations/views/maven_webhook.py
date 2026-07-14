"""Maven cohort webhook endpoint (issue #960).

``POST /api/webhooks/maven`` — shared-secret authenticated, CSRF-exempt,
POST-only. Maven exposes no signing secret, so authentication is a shared
secret supplied either as ``?secret=<token>`` (so an unguessable URL can be
pasted directly into Maven/Zapier) or an ``X-Maven-Secret`` header (preferred
when Zapier is the intermediary). Compared in constant time.

The endpoint is a thin shell: parse + authenticate + dispatch to
``integrations.services.maven.handle_maven_event``, which is shared with the
``replay_maven_event`` command so the live and replayed paths are identical.
"""

import hmac
import json
import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from integrations.maven_config import maven_enabled, maven_shared_secret
from integrations.services.maven import MavenTransientError, handle_maven_event

logger = logging.getLogger(__name__)


def _authenticated(request):
    """Constant-time check of the shared secret from query or header.

    Returns False when the configured secret is unset (the endpoint then
    refuses every request, even with the feature enabled) or does not match.
    """
    configured = maven_shared_secret()
    if not configured:
        return False
    presented = request.headers.get("X-Maven-Secret") or request.GET.get("secret") or ""
    return hmac.compare_digest(str(presented), str(configured))


@csrf_exempt
@require_POST
def maven_webhook(request):
    """Handle an inbound Maven cohort webhook."""
    # Feature toggle first: when off the endpoint is inert and does no work
    # (and does not leak whether the secret is configured).
    if not maven_enabled():
        return JsonResponse({"status": "disabled"}, status=200)

    if not _authenticated(request):
        logger.warning("Rejected Maven webhook request: authentication failed")
        return JsonResponse({"error": "forbidden"}, status=403)

    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "invalid_json"}, status=400)

    if not isinstance(payload, dict):
        return JsonResponse({"error": "invalid_json"}, status=400)

    try:
        result = handle_maven_event(payload)
    except ValueError as exc:
        # Currently only "missing_email".
        return JsonResponse({"error": str(exc)}, status=400)
    except MavenTransientError:
        # Retryable — the occurrence and failed entitlement attempt are
        # already persisted, so redelivery resumes only that failed step.
        logger.warning("Maven webhook transient failure; returning 500 for retry")
        return JsonResponse({"error": "processing_failed"}, status=500)

    return JsonResponse({"status": result.status}, status=200)
