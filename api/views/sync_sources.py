"""Content sync source API endpoints (issue #634).

This operator API intentionally exposes only inventory and trigger actions:
API callers can list configured content sources and enqueue sync jobs, but
cannot create, edit, or delete source rows.
"""

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.safety import error_response
from api.utils import parse_json_body, require_methods
from integrations.models import ContentSource
from integrations.services.content_sync_queue import enqueue_content_sync

DELETE_NOT_AVAILABLE_MESSAGE = (
    "Content sync source deletion is not available through the API. "
    "Go to Studio to delete this source manually."
)


def _iso(value):
    return value.isoformat() if value is not None else None


def _serialize_source(source):
    return {
        "id": str(source.pk),
        "repo_name": source.repo_name,
        "short_name": source.short_name,
        "is_private": source.is_private,
        "last_sync_status": source.last_sync_status,
        "last_synced_at": _iso(source.last_synced_at),
        "sync_locked_at": _iso(source.sync_locked_at),
        "sync_requested": source.sync_requested,
        "last_synced_commit": source.last_synced_commit,
        "short_synced_commit": source.short_synced_commit,
        "synced_commit_url": source.synced_commit_url,
        "max_files": source.max_files,
        "created_at": _iso(source.created_at),
        "updated_at": _iso(source.updated_at),
    }


def _delete_not_available_response():
    return error_response(
        DELETE_NOT_AVAILABLE_MESSAGE,
        "sync_source_delete_not_available",
        status=405,
    )


def _force_requested(request, data):
    if request.GET.get("force") in {"1", "true", "True", "yes", "on"}:
        return True
    return data.get("force") is True


@token_required
@csrf_exempt
@require_methods("GET", "DELETE")
def sync_sources_collection(request):
    """GET/DELETE ``/api/sync/sources``."""
    if request.method == "DELETE":
        return _delete_not_available_response()

    sources = ContentSource.objects.order_by("repo_name")
    return JsonResponse({
        "sources": [_serialize_source(source) for source in sources],
    })


@token_required
@csrf_exempt
@require_methods("POST", "DELETE")
def sync_source_trigger(request, source_id):
    """POST/DELETE ``/api/sync/sources/<uuid>/trigger``."""
    if request.method == "DELETE":
        return _delete_not_available_response()

    if request.body:
        data, error = parse_json_body(request)
        if error is not None:
            return error
        if not isinstance(data, dict):
            return error_response(
                "Body must be a JSON object",
                "invalid_type",
                details={"field": "body", "expected": "object"},
            )
    else:
        data = {}

    source = get_object_or_404(ContentSource, pk=source_id)
    result = enqueue_content_sync(
        source,
        force=_force_requested(request, data),
        task_source="API sync source trigger",
    )
    if not result.ok:
        return error_response(
            result.message,
            "sync_enqueue_failed",
            status=500,
            details={"source_id": str(source.pk), "error": result.error},
        )

    source.refresh_from_db()
    return JsonResponse(
        {
            "status": "queued" if result.queued else "completed",
            "source": _serialize_source(source),
            "batch_id": str(result.batch_id) if result.batch_id else None,
            "task_id": str(result.task_id) if result.task_id else None,
            "ran_inline": result.ran_inline,
            "message": result.message,
        },
        status=202 if result.queued else 200,
    )
