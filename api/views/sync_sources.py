"""Read-only content sync observability and source trigger endpoints.

The operator API exposes source inventory, history reads, and trigger actions,
but cannot create, edit, or delete source rows.
"""

from uuid import UUID

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import (
    delete_not_available_response,
    parse_json_body,
    require_methods,
    validation_response,
)
from integrations.models import ContentSource
from integrations.services.content_sync_queue import enqueue_content_sync
from integrations.services.sync_observability import (
    SYNC_HISTORY_STATUSES,
    compact_summary,
    enrich_sources_with_health,
    logical_history_page,
    logs_for_history_id,
)

DELETE_NOT_AVAILABLE_MESSAGE = (
    "Content sync source deletion is not available through the API. "
    "Go to Studio to delete this source manually."
)

_SYNC_SOURCE_EXAMPLE = {
    "id": "5b4c0e3f-1f3c-4f8f-9c9d-2e5d0e2c8a51",
    "repo_name": "AI-Shipping-Labs/content",
    "short_name": "content",
    "is_private": True,
    "webhook_secret_configured": True,
    "webhook_security_status": "configured",
    "last_sync_status": "success",
    "last_synced_at": "2026-04-15T12:00:00+00:00",
    "sync_locked_at": None,
    "sync_requested": False,
    "last_synced_commit": "a1b2c3d4e5f6",
    "short_synced_commit": "a1b2c3d",
    "synced_commit_url": "https://github.com/AI-Shipping-Labs/content/commit/a1b2c3d4e5f6",
    "max_files": 5000,
    "created_at": "2026-04-15T12:00:00+00:00",
    "updated_at": "2026-04-15T12:00:00+00:00",
}

_SYNC_SOURCE_WITH_HEALTH_EXAMPLE = {
    **_SYNC_SOURCE_EXAMPLE,
    "health": {
        "status": "success",
        "status_label": "success",
        "content_fresh_at": "2026-04-15T12:00:00+00:00",
        "content_age_seconds": 3600,
        "stale": False,
        "stale_after_days": 7,
        "latest_history_id": "906dbf60-4091-4f09-b1c7-2d291d702b34",
        "errors_total": 4,
        "errors_unique": 2,
    },
}

_SYNC_HISTORY_SUMMARY_EXAMPLE = {
    "history_id": "906dbf60-4091-4f09-b1c7-2d291d702b34",
    "batch_id": "906dbf60-4091-4f09-b1c7-2d291d702b34",
    "source_ids": ["5b4c0e3f-1f3c-4f8f-9c9d-2e5d0e2c8a51"],
    "repo_names": ["AI-Shipping-Labs/content"],
    "started_at": "2026-07-17T10:00:00+00:00",
    "finished_at": "2026-07-17T10:02:00+00:00",
    "status": "partial",
    "status_label": "Completed with errors",
    "log_count": 1,
    "commits": ["a1b2c3d4e5f6"],
    "counts": {"created": 1, "updated": 2, "unchanged": 3, "deleted": 0},
    "tiers": {"synced": False, "count": 0},
    "errors_total": 3,
    "errors_unique": 1,
}

_SYNC_HISTORY_DETAIL_EXAMPLE = {
    **_SYNC_HISTORY_SUMMARY_EXAMPLE,
    "errors": [{
        "file": "articles/example.md",
        "message": "Frontmatter is invalid",
        "count": 3,
        "target": {
            "type": "article",
            "id": "42",
            "slug": "example",
            "studio_url": "/studio/articles/42/edit",
        },
    }],
    "per_type": [{
        "content_type": "article",
        "source_ids": ["5b4c0e3f-1f3c-4f8f-9c9d-2e5d0e2c8a51"],
        "status": "partial",
        "items": [{
            "content_type": "article",
            "slug": "example",
            "title": "Example",
            "action": "updated",
        }],
        "counts": {"created": 0, "updated": 1, "deleted": 0},
    }],
}


def _iso(value):
    return value.isoformat() if value is not None else None


def _serialize_source(source, health=None):
    payload = {
        "id": str(source.pk),
        "repo_name": source.repo_name,
        "short_name": source.short_name,
        "is_private": source.is_private,
        "webhook_secret_configured": source.webhook_secret_configured,
        "webhook_security_status": source.webhook_security_status,
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
    if health is not None:
        payload["health"] = {
            **health,
            "content_fresh_at": _iso(health["content_fresh_at"]),
        }
    return payload


def _force_requested(request, data):
    if request.GET.get("force") in {"1", "true", "True", "yes", "on"}:
        return True
    return data.get("force") is True


@token_required
@csrf_exempt
@require_methods("GET", "DELETE")
@openapi_spec(
    tag="Sync Sources",
    summary="List sync sources or attempt to delete (not available)",
    methods={
        "GET": {
            "summary": "List content sync sources",
            "responses": {
                200: {
                    "description": "List of content sync sources.",
                    "example": {"sources": [_SYNC_SOURCE_WITH_HEALTH_EXAMPLE]},
                },
            },
        },
        "DELETE": {
            "summary": "DELETE is not available on this route",
            "description": (
                "Source deletion is intentionally unavailable through "
                "the API; use Studio."
            ),
            "responses": {
                405: {
                    "description": "Source deletion is not available.",
                    "example": {
                        "error": DELETE_NOT_AVAILABLE_MESSAGE,
                        "code": "sync_source_delete_not_available",
                    },
                },
            },
        },
    },
)
def sync_sources_collection(request):
    """GET/DELETE ``/api/sync/sources``."""
    if request.method == "DELETE":
        return delete_not_available_response(
            DELETE_NOT_AVAILABLE_MESSAGE,
            "sync_source_delete_not_available",
        )

    sources = list(ContentSource.objects.order_by("repo_name"))
    enriched = enrich_sources_with_health(sources)
    return JsonResponse({
        "sources": [
            _serialize_source(source, health)
            for source, health, _result in enriched
        ],
    })


def _validation_error(field, message):
    return validation_response({field: message})


def _positive_integer(request, name, default):
    raw = request.GET.get(name)
    if raw in (None, ""):
        return default, None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, _validation_error(name, "Must be a positive integer.")
    if value < 1:
        return None, _validation_error(name, "Must be a positive integer.")
    return value, None


@token_required
@csrf_exempt
@require_methods("GET")
@openapi_spec(
    tag="Sync Sources",
    summary="List logical content sync history",
    methods={
        "GET": {
            "summary": "List logical content sync history",
            "description": "Staff-only read of deduplicated logical sync batches.",
            "query": {
                "source": {"type": "string", "format": "uuid", "required": False},
                "status": {"type": "string", "enum": list(SYNC_HISTORY_STATUSES), "required": False},
                "page": {"type": "integer", "minimum": 1, "default": 1},
                "page_size": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            },
            "responses": {
                200: {
                    "description": "A page of compact logical sync history.",
                    "example": {
                        "items": [_SYNC_HISTORY_SUMMARY_EXAMPLE],
                        "pagination": {"page": 1, "page_size": 50, "count": 1, "total_pages": 1, "has_next": False, "has_previous": False},
                    },
                },
                404: {"description": "Source not found."},
                422: {"description": "Invalid source, status, page, or page size."},
            },
        },
    },
)
def sync_history_collection(request):
    source_value = (request.GET.get("source") or "").strip()
    status = (request.GET.get("status") or "").strip()
    source = None
    if source_value:
        try:
            source_uuid = UUID(source_value)
        except (TypeError, ValueError):
            return _validation_error("source", "Must be a UUID.")
        source = ContentSource.objects.filter(pk=source_uuid).first()
        if source is None:
            return error_response("Sync source not found", "not_found", status=404)
    if status and status not in SYNC_HISTORY_STATUSES:
        return _validation_error("status", "Unknown sync status.")
    page, error = _positive_integer(request, "page", 1)
    if error:
        return error
    page_size, error = _positive_integer(request, "page_size", 50)
    if error:
        return error
    page_size = min(page_size, 200)
    page_obj, groups = logical_history_page(
        source=source,
        status=status or None,
        page=page,
        page_size=page_size,
    )
    return JsonResponse({
        "items": [compact_summary(logs) for _row, logs in groups],
        "pagination": {
            "page": page_obj.number,
            "page_size": page_size,
            "count": page_obj.paginator.count,
            "total_pages": page_obj.paginator.num_pages,
            "has_next": page_obj.has_next(),
            "has_previous": page_obj.has_previous(),
        },
    })


@token_required
@csrf_exempt
@require_methods("GET")
@openapi_spec(
    tag="Sync Sources",
    summary="Get one logical content sync history item",
    methods={
        "GET": {
            "summary": "Get logical sync history detail",
            "responses": {
                200: {
                    "description": "Logical summary, item details, and deduplicated structured errors.",
                    "example": _SYNC_HISTORY_DETAIL_EXAMPLE,
                },
                404: {"description": "History item not found."},
            },
        },
    },
)
def sync_history_detail(request, history_id):
    try:
        logs = logs_for_history_id(history_id)
    except (TypeError, ValueError):
        logs = []
    if not logs:
        return error_response("Sync history item not found", "not_found", status=404)
    return JsonResponse(compact_summary(
        logs,
        include_errors=True,
        resolve_targets=True,
    ))


@token_required
@csrf_exempt
@require_methods("POST", "DELETE")
@openapi_spec(
    tag="Sync Sources",
    summary="Trigger a sync run (POST); DELETE is not available",
    methods={
        "POST": {
            "summary": "Trigger a content sync run",
            "description": (
                "Enqueues a sync job for the given source. Pass "
                "``force=true`` as a query param or in the JSON body to "
                "bypass the per-source idempotency lock."
            ),
            "query": {
                "force": {
                    "type": "string",
                    "enum": ["1", "true", "True", "yes", "on"],
                    "required": False,
                    "description": (
                        "Truthy values bypass the idempotency lock."
                    ),
                },
            },
            "request_body": {
                "properties": {
                    "force": {"type": "boolean"},
                },
                "example": {"force": True},
            },
            "responses": {
                200: {
                    "description": (
                        "Sync ran inline (e.g. in tests)."
                    ),
                    "example": {
                        "status": "completed",
                        "source": _SYNC_SOURCE_EXAMPLE,
                        "batch_id": None,
                        "task_id": None,
                        "ran_inline": True,
                        "message": "Sync completed inline",
                    },
                },
                202: {
                    "description": "Sync job queued.",
                    "example": {
                        "status": "queued",
                        "source": _SYNC_SOURCE_EXAMPLE,
                        "batch_id": "batch_xyz",
                        "task_id": "task_xyz",
                        "ran_inline": False,
                        "message": "Sync queued",
                    },
                },
                400: {"description": "Invalid JSON body."},
                404: {"description": "Sync source not found."},
                500: {
                    "description": "Sync enqueue failed.",
                    "example": {
                        "error": "Failed to enqueue sync",
                        "code": "sync_enqueue_failed",
                    },
                },
            },
        },
        "DELETE": {
            "summary": "DELETE is not available on this route",
            "responses": {
                405: {
                    "description": "Source deletion is not available.",
                    "example": {
                        "error": DELETE_NOT_AVAILABLE_MESSAGE,
                        "code": "sync_source_delete_not_available",
                    },
                },
            },
        },
    },
)
def sync_source_trigger(request, source_id):
    """POST/DELETE ``/api/sync/sources/<uuid>/trigger``."""
    if request.method == "DELETE":
        return delete_not_available_response(
            DELETE_NOT_AVAILABLE_MESSAGE,
            "sync_source_delete_not_available",
        )

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
