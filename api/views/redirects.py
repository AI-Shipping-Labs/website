"""URL redirect API endpoints (issue #674).

Operator API for managing rows in ``integrations.models.Redirect``. The
Studio UI at ``/studio/redirects/`` covers the same model for humans; this
module is the JSON surface used by scripted onboarding, bulk imports, and
the Phase A route pin in issue #673.

Layout mirrors ``api/views/sync_sources.py`` (small ``_serialize_redirect``
helper + thin views). Bulk-upsert response shape blends
``api/views/plans.py::sprint_plans_bulk_import`` (counter + per-row +
atomic) with ``api/views/contacts.py::contacts_import`` (``warnings`` field
for non-fatal notes).

Every write path (POST, PATCH, DELETE, bulk) MUST call
``integrations.middleware.clear_redirect_cache`` after committing so the
5-minute middleware cache stays in sync with the database.
"""

from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import parse_json_body, require_methods
from integrations.middleware import clear_redirect_cache
from integrations.models import Redirect

_REDIRECT_EXAMPLE = {
    "id": 7,
    "source_path": "/old-page",
    "target_path": "/new-page",
    "redirect_type": 301,
    "is_active": True,
    "created_at": "2026-04-15T12:00:00+00:00",
    "updated_at": "2026-04-15T12:00:00+00:00",
}

VALID_REDIRECT_TYPES = {301, 302}

# Truthy ``?is_active`` query values. Anything else (including unset) is
# treated as "no filter".
_TRUE_VALUES = {"1", "true", "True", "yes", "on"}
_FALSE_VALUES = {"0", "false", "False", "no", "off"}


def _iso(value):
    return value.isoformat() if value is not None else None


def _serialize_redirect(redirect):
    return {
        "id": redirect.pk,
        "source_path": redirect.source_path,
        "target_path": redirect.target_path,
        "redirect_type": redirect.redirect_type,
        "is_active": redirect.is_active,
        "created_at": _iso(redirect.created_at),
        "updated_at": _iso(redirect.updated_at),
    }


def _normalize_path(value):
    """Trim and auto-prepend ``/`` for non-empty path strings."""
    if not isinstance(value, str):
        return value
    trimmed = value.strip()
    if trimmed and not trimmed.startswith("/"):
        trimmed = "/" + trimmed
    return trimmed


def _validate_payload(data, *, partial, index=None):
    """Return ``(cleaned_dict, error_response)`` for a redirect payload.

    ``partial=True`` skips "required" checks (for PATCH and bulk-update
    rows that supply only some fields). ``index`` is included in error
    ``details`` when present so bulk callers can tell which array entry
    failed.
    """

    def _detail(field, **extra):
        details = {"field": field, **extra}
        if index is not None:
            details["index"] = index
        return details

    if not isinstance(data, dict):
        details = {"expected": "object"}
        if index is not None:
            details["index"] = index
        return None, error_response(
            "Body must be a JSON object",
            "invalid_type",
            status=422 if index is not None else 400,
            details=details,
        )

    cleaned = {}

    if "source_path" in data:
        source_path = data["source_path"]
        if not isinstance(source_path, str):
            return None, error_response(
                "source_path must be a string",
                "validation_error",
                status=422,
                details=_detail("source_path"),
            )
        source_path = _normalize_path(source_path)
        if not source_path:
            return None, error_response(
                "source_path must not be empty",
                "validation_error",
                status=422,
                details=_detail("source_path"),
            )
        cleaned["source_path"] = source_path
    elif not partial:
        return None, error_response(
            "Missing required field: source_path",
            "missing_field",
            status=422,
            details=_detail("source_path"),
        )

    if "target_path" in data:
        target_path = data["target_path"]
        if not isinstance(target_path, str):
            return None, error_response(
                "target_path must be a string",
                "validation_error",
                status=422,
                details=_detail("target_path"),
            )
        target_path = _normalize_path(target_path)
        if not target_path:
            return None, error_response(
                "target_path must not be empty",
                "validation_error",
                status=422,
                details=_detail("target_path"),
            )
        cleaned["target_path"] = target_path
    elif not partial:
        return None, error_response(
            "Missing required field: target_path",
            "missing_field",
            status=422,
            details=_detail("target_path"),
        )

    if "redirect_type" in data:
        redirect_type = data["redirect_type"]
        # Reject bools (``isinstance(True, int)`` is ``True`` in Python).
        if isinstance(redirect_type, bool) or not isinstance(redirect_type, int):
            return None, error_response(
                "redirect_type must be 301 or 302",
                "validation_error",
                status=422,
                details=_detail("redirect_type"),
            )
        if redirect_type not in VALID_REDIRECT_TYPES:
            return None, error_response(
                "redirect_type must be 301 or 302",
                "validation_error",
                status=422,
                details=_detail("redirect_type"),
            )
        cleaned["redirect_type"] = redirect_type

    if "is_active" in data:
        is_active = data["is_active"]
        if not isinstance(is_active, bool):
            return None, error_response(
                "is_active must be a boolean",
                "validation_error",
                status=422,
                details=_detail("is_active"),
            )
        cleaned["is_active"] = is_active

    return cleaned, None


def _loop_check(source_path, target_path, *, index=None):
    if source_path is None or target_path is None:
        return None
    if source_path == target_path:
        details = {"source_path": source_path, "target_path": target_path}
        if index is not None:
            details["index"] = index
        return error_response(
            "source_path and target_path must differ",
            "validation_error",
            status=422,
            details=details,
        )
    return None


def _conflict_response(source_path, *, index=None):
    details = {"source_path": source_path}
    if index is not None:
        details["index"] = index
    return error_response(
        f"A redirect for {source_path!r} already exists",
        "source_path_conflict",
        status=409,
        details=details,
    )


@token_required
@csrf_exempt
@require_methods("GET", "POST")
@openapi_spec(
    tag="Redirects",
    summary="List or create URL redirects",
    methods={
        "GET": {
            "summary": "List redirects",
            "query": {
                "is_active": {
                    "type": "string",
                    "enum": ["true", "false"],
                    "required": False,
                    "description": "Filter on the activation flag.",
                },
            },
            "responses": {
                200: {
                    "description": "List of redirects.",
                    "example": {"redirects": [_REDIRECT_EXAMPLE]},
                },
            },
        },
        "POST": {
            "summary": "Create a redirect",
            "request_body": {
                "required": ["source_path", "target_path"],
                "properties": {
                    "source_path": {"type": "string"},
                    "target_path": {"type": "string"},
                    "redirect_type": {
                        "type": "integer",
                        "enum": [301, 302],
                    },
                    "is_active": {"type": "boolean"},
                },
                "example": {
                    "source_path": "/old-page",
                    "target_path": "/new-page",
                    "redirect_type": 301,
                },
            },
            "responses": {
                201: {
                    "description": "Redirect created.",
                    "example": _REDIRECT_EXAMPLE,
                },
                400: {"description": "Invalid JSON body."},
                409: {
                    "description": (
                        "A redirect with the same source_path already "
                        "exists."
                    ),
                    "example": {
                        "error": "A redirect for '/old-page' already exists",
                        "code": "source_path_conflict",
                    },
                },
                422: {
                    "description": (
                        "Validation error: source_path == target_path "
                        "(loop), unknown redirect_type, missing field."
                    ),
                },
            },
        },
    },
)
def redirects_collection(request):
    """``GET / POST /api/redirects``.

    GET lists every redirect (no pagination -- low scale). Optional
    ``?is_active=true|false`` filters by activation flag; any other query
    param is ignored. POST creates a single redirect.
    """
    if request.method == "GET":
        queryset = Redirect.objects.all()
        is_active_param = request.GET.get("is_active")
        if is_active_param in _TRUE_VALUES:
            queryset = queryset.filter(is_active=True)
        elif is_active_param in _FALSE_VALUES:
            queryset = queryset.filter(is_active=False)
        return JsonResponse({
            "redirects": [_serialize_redirect(r) for r in queryset],
        })

    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error

    cleaned, validation_error = _validate_payload(data, partial=False)
    if validation_error is not None:
        return validation_error

    loop_error = _loop_check(
        cleaned.get("source_path"), cleaned.get("target_path"),
    )
    if loop_error is not None:
        return loop_error

    if Redirect.objects.filter(source_path=cleaned["source_path"]).exists():
        return _conflict_response(cleaned["source_path"])

    redirect_obj = Redirect.objects.create(
        source_path=cleaned["source_path"],
        target_path=cleaned["target_path"],
        redirect_type=cleaned.get("redirect_type", 301),
        is_active=cleaned.get("is_active", True),
    )
    clear_redirect_cache()
    return JsonResponse(_serialize_redirect(redirect_obj), status=201)


@token_required
@csrf_exempt
@require_methods("GET", "PATCH", "DELETE")
@openapi_spec(
    tag="Redirects",
    summary="Retrieve, update, or delete a redirect",
    methods={
        "GET": {
            "summary": "Retrieve a redirect",
            "responses": {
                200: {
                    "description": "Redirect detail.",
                    "example": _REDIRECT_EXAMPLE,
                },
                404: {
                    "description": "Redirect not found.",
                    "example": {
                        "error": "Redirect not found",
                        "code": "redirect_not_found",
                    },
                },
            },
        },
        "PATCH": {
            "summary": "Update a redirect",
            "request_body": {
                "properties": {
                    "source_path": {"type": "string"},
                    "target_path": {"type": "string"},
                    "redirect_type": {
                        "type": "integer",
                        "enum": [301, 302],
                    },
                    "is_active": {"type": "boolean"},
                },
                "example": {"is_active": False},
            },
            "responses": {
                200: {
                    "description": "Redirect updated.",
                    "example": _REDIRECT_EXAMPLE,
                },
                400: {"description": "Invalid JSON body."},
                404: {"description": "Redirect not found."},
                409: {
                    "description": (
                        "Source path conflict with another redirect."
                    ),
                },
                422: {"description": "Validation error or loop check failed."},
            },
        },
        "DELETE": {
            "summary": "Delete a redirect",
            "responses": {
                204: {"description": "Redirect deleted (empty body)."},
                404: {"description": "Redirect not found."},
            },
        },
    },
)
def redirect_detail(request, redirect_id):
    """``GET / PATCH / DELETE /api/redirects/<id>``."""
    redirect_obj = Redirect.objects.filter(pk=redirect_id).first()
    if redirect_obj is None:
        return error_response(
            "Redirect not found",
            "redirect_not_found",
            status=404,
        )

    if request.method == "GET":
        return JsonResponse(_serialize_redirect(redirect_obj))

    if request.method == "DELETE":
        redirect_obj.delete()
        clear_redirect_cache()
        return JsonResponse({}, status=204)

    # PATCH
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error

    cleaned, validation_error = _validate_payload(data, partial=True)
    if validation_error is not None:
        return validation_error

    new_source = cleaned.get("source_path", redirect_obj.source_path)
    new_target = cleaned.get("target_path", redirect_obj.target_path)
    loop_error = _loop_check(new_source, new_target)
    if loop_error is not None:
        return loop_error

    if "source_path" in cleaned and cleaned["source_path"] != redirect_obj.source_path:
        clash = (
            Redirect.objects
            .filter(source_path=cleaned["source_path"])
            .exclude(pk=redirect_obj.pk)
            .exists()
        )
        if clash:
            return _conflict_response(cleaned["source_path"])

    update_fields = []
    for field in ("source_path", "target_path", "redirect_type", "is_active"):
        if field in cleaned:
            setattr(redirect_obj, field, cleaned[field])
            update_fields.append(field)

    if update_fields:
        update_fields.append("updated_at")
        redirect_obj.save(update_fields=update_fields)
        clear_redirect_cache()

    return JsonResponse(_serialize_redirect(redirect_obj))


@token_required
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Redirects",
    summary="Bulk upsert redirects",
    methods={
        "POST": {
            "summary": "Bulk upsert redirects",
            "description": (
                "Atomic upsert by ``source_path``. Any row failure "
                "rolls every row back. ``action`` is "
                "``created`` / ``updated`` / ``skipped`` (where "
                "``skipped`` means the row was identical to the "
                "existing record)."
            ),
            "request_body": {
                "required": ["redirects"],
                "properties": {
                    "redirects": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["source_path", "target_path"],
                            "properties": {
                                "source_path": {"type": "string"},
                                "target_path": {"type": "string"},
                                "redirect_type": {
                                    "type": "integer",
                                    "enum": [301, 302],
                                },
                                "is_active": {"type": "boolean"},
                            },
                        },
                    },
                },
                "example": {
                    "redirects": [
                        {
                            "source_path": "/old-page",
                            "target_path": "/new-page",
                            "redirect_type": 301,
                        },
                    ],
                },
            },
            "responses": {
                200: {
                    "description": "Bulk upsert summary.",
                    "example": {
                        "created": 1,
                        "updated": 0,
                        "skipped": 0,
                        "results": [
                            {
                                "index": 0,
                                "source_path": "/old-page",
                                "action": "created",
                                "id": 7,
                            },
                        ],
                        "warnings": [],
                    },
                },
                400: {"description": "Invalid JSON or missing field."},
                422: {"description": "Per-row validation error."},
            },
        },
    },
)
def redirects_bulk_upsert(request):
    """``POST /api/redirects/bulk``.

    Atomic upsert by ``source_path``. Any row failure rolls every row back
    so the caller never has to clean up partial state. Response shape:

        {"created": N, "updated": N, "skipped": N,
         "results": [{"index": i, "source_path": s, "action": ..., "id": id}, ...],
         "warnings": [...]}

    ``action`` is ``"created" | "updated" | "skipped"``. ``"skipped"``
    means the row was identical to the existing record (same target,
    type, is_active).
    """
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return error_response(
            "Body must be a JSON object",
            "invalid_type",
            details={"field": "body", "expected": "object"},
        )

    redirects_payload = data.get("redirects")
    if redirects_payload is None:
        return error_response(
            "Missing required field: redirects",
            "missing_field",
            details={"field": "redirects"},
        )
    if not isinstance(redirects_payload, list):
        return error_response(
            "redirects must be a list",
            "invalid_type",
            details={"field": "redirects", "expected": "list"},
        )

    created = 0
    updated = 0
    skipped = 0
    results = []
    warnings = []

    with transaction.atomic():
        for index, row in enumerate(redirects_payload):
            cleaned, validation_error = _validate_payload(
                row, partial=False, index=index,
            )
            if validation_error is not None:
                transaction.set_rollback(True)
                return validation_error

            loop_error = _loop_check(
                cleaned.get("source_path"),
                cleaned.get("target_path"),
                index=index,
            )
            if loop_error is not None:
                transaction.set_rollback(True)
                return loop_error

            existing = (
                Redirect.objects
                .filter(source_path=cleaned["source_path"])
                .first()
            )
            redirect_type = cleaned.get("redirect_type", 301)
            is_active = cleaned.get("is_active", True)

            if existing is None:
                obj = Redirect.objects.create(
                    source_path=cleaned["source_path"],
                    target_path=cleaned["target_path"],
                    redirect_type=redirect_type,
                    is_active=is_active,
                )
                created += 1
                action = "created"
            else:
                identical = (
                    existing.target_path == cleaned["target_path"]
                    and existing.redirect_type == redirect_type
                    and existing.is_active == is_active
                )
                if identical:
                    skipped += 1
                    action = "skipped"
                    obj = existing
                else:
                    existing.target_path = cleaned["target_path"]
                    existing.redirect_type = redirect_type
                    existing.is_active = is_active
                    existing.save(update_fields=[
                        "target_path",
                        "redirect_type",
                        "is_active",
                        "updated_at",
                    ])
                    updated += 1
                    action = "updated"
                    obj = existing

            results.append({
                "index": index,
                "source_path": obj.source_path,
                "action": action,
                "id": obj.pk,
            })

    clear_redirect_cache()
    return JsonResponse({
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "results": results,
        "warnings": warnings,
    })
