"""Interview-note endpoints for the plans API (issue #433).

The visibility gate is the security-critical part of this issue. This
module composes every read against ``visible_interview_notes_for`` from
``api/views/_permissions.py`` -- it does NOT inspect any user attribute
directly. A test in ``api/tests/test_interview_notes.py`` reads this
file as plain text and asserts the gate-related attribute name does not
appear here. Every staff-or-not branch goes through a helper exported
from ``_permissions.py``.

Endpoints:

- ``GET /api/plans/<id>/interview-notes/``
- ``GET /api/users/<email>/interview-notes/``
- ``GET /api/users/<email>/notes/``
- ``GET /api/interview-notes/<id>/``
- ``POST /api/interview-notes/``
- ``POST /api/member-notes/``
- ``PATCH /api/interview-notes/<id>/``
- ``DELETE /api/interview-notes/<id>/``
- ``GET / PATCH / DELETE /api/member-notes/<id>/``
"""

from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from accounts.utils.tags import normalize_tags
from api.openapi import openapi_spec
from api.safety import error_response
from api.serializers.plans import serialize_interview_note
from api.utils import (
    delete_not_available_response,
    parse_json_body,
    require_methods,
)
from api.views._permissions import (
    bearer_sees_internal_notes,
    visible_interview_notes_for,
    visible_plans_for,
)
from plans.models import (
    KIND_CHOICES,
    VISIBILITY_CHOICES,
    InterviewNote,
)

User = get_user_model()

VALID_VISIBILITIES = {choice for choice, _label in VISIBILITY_CHOICES}
VALID_KINDS = {choice for choice, _label in KIND_CHOICES}

_VISIBILITIES_ENUM = sorted(VALID_VISIBILITIES)
_KINDS_ENUM = sorted(VALID_KINDS)

# Issue #864 (human decision, 2026-06-13): interview-note hard-delete is not
# available through the API. DELETE is accepted but returns 405 pointing the
# operator to Studio, matching the events guard pattern.
INTERVIEW_NOTE_DELETE_NOT_AVAILABLE_MESSAGE = (
    "Interview note deletion is not available through the API. "
    "Go to Studio to delete this note manually."
)


_INTERVIEW_NOTE_EXAMPLE = {
    "id": 12,
    "plan_id": 5,
    "member_email": "alice@example.com",
    "created_by_email": "staff@example.com",
    "visibility": "external",
    "kind": "general",
    "body": "Great onboarding chat.",
    "tags": ["intake"],
    "source_type": "",
    "source_metadata": {},
    "created_at": "2026-04-15T12:00:00+00:00",
    "updated_at": "2026-04-15T12:00:00+00:00",
}


# All staff-or-not branches in this module go through
# ``bearer_sees_internal_notes`` from ``_permissions.py``. There is no
# local privilege check; ``api/views/_permissions.py`` is the single
# place in ``api/views/`` that may inspect user attributes for staff
# resolution.


def _validated_tags(data):
    if "tags" not in data:
        return None, None
    if not isinstance(data["tags"], list):
        return None, error_response(
            "tags must be a list",
            "invalid_type",
            details={"field": "tags", "expected": "array"},
        )
    return normalize_tags(data["tags"]), None


def _validated_source_type(data):
    if "source_type" not in data:
        return None, None
    if not isinstance(data["source_type"], str):
        return None, error_response(
            "source_type must be a string",
            "invalid_type",
            details={"field": "source_type", "expected": "string"},
        )
    source_type = data["source_type"].strip().lower()
    if len(source_type) > 40:
        return None, error_response(
            "source_type is too long",
            "validation_error",
            status=422,
            details={"source_type": "Must be 40 characters or fewer"},
        )
    return source_type, None


def _validated_source_metadata(data):
    if "source_metadata" not in data:
        return None, None
    if not isinstance(data["source_metadata"], dict):
        return None, error_response(
            "source_metadata must be a JSON object",
            "invalid_type",
            details={"field": "source_metadata", "expected": "object"},
        )
    return data["source_metadata"], None


@token_required
@csrf_exempt
@require_methods("GET")
@openapi_spec(
    tag="Interview Notes",
    summary="List interview notes attached to a plan",
    methods={
        "GET": {
            "summary": "List interview notes for a plan",
            "description": (
                "Visibility goes through "
                "``visible_interview_notes_for``: a non-staff bearer "
                "asking with ``?visibility=internal`` gets 403 "
                "``forbidden_internal_note``."
            ),
            "query": {
                "visibility": {
                    "type": "string",
                    "enum": _VISIBILITIES_ENUM,
                    "required": False,
                    "description": "Filter on note visibility.",
                },
            },
            "responses": {
                200: {
                    "description": "List of interview notes.",
                    "example": {
                        "interview_notes": [_INTERVIEW_NOTE_EXAMPLE],
                    },
                },
                403: {
                    "description": (
                        "Non-staff bearer asked for internal notes."
                    ),
                    "example": {
                        "error": "Internal notes are not visible to this token",
                        "code": "forbidden_internal_note",
                    },
                },
                404: {
                    "description": "Plan not found or not visible.",
                    "example": {
                        "error": "Plan not found",
                        "code": "unknown_plan",
                    },
                },
            },
        },
    },
)
def plan_interview_notes(request, plan_id):
    """``GET /api/plans/<plan_id>/interview-notes/``."""
    plan = visible_plans_for(request.user).filter(pk=plan_id).first()
    if plan is None:
        return error_response(
            "Plan not found",
            "unknown_plan",
            status=404,
        )

    visibility_filter = request.GET.get("visibility")
    if visibility_filter == "internal" and not bearer_sees_internal_notes(
        request.user,
    ):
        return error_response(
            "Internal notes are not visible to this token",
            "forbidden_internal_note",
            status=403,
        )

    qs = visible_interview_notes_for(request.user).filter(plan=plan)
    if visibility_filter in VALID_VISIBILITIES:
        qs = qs.filter(visibility=visibility_filter)
    qs = qs.select_related("member", "created_by").order_by("-created_at")

    return JsonResponse(
        {"interview_notes": [serialize_interview_note(n) for n in qs]},
        status=200,
    )


@token_required
@csrf_exempt
@require_methods("GET")
@openapi_spec(
    tag="Interview Notes",
    summary="List interview notes for a specific user",
    methods={
        "GET": {
            "summary": "List a user's interview notes",
            "description": (
                "Staff-only. Routed by three URL aliases under "
                "``/api/users/<email>/`` -- the OpenAPI spec emits one "
                "operation per alias. ``?plan=null`` preserves the "
                "inbox-only behaviour; ``?plan=<id>`` narrows to one "
                "plan."
            ),
            "query": {
                "plan": {
                    "type": "string",
                    "required": False,
                    "description": (
                        "Plan id, or the literal string ``null`` to "
                        "narrow to notes with no plan attached."
                    ),
                },
            },
            "responses": {
                200: {
                    "description": "List of interview notes.",
                    "example": {
                        "interview_notes": [_INTERVIEW_NOTE_EXAMPLE],
                    },
                },
                422: {
                    "description": "Unknown user or bad plan filter.",
                    "example": {
                        "error": "User not found",
                        "code": "unknown_user",
                        "details": {"email": "Unknown user"},
                    },
                },
            },
        },
    },
)
def user_interview_notes(request, email):
    """``GET /api/users/<email>/interview-notes/``.

    Staff-only endpoint. ``@token_required`` guarantees the bearer is
    staff (see ``accounts/auth.py``) and ``Token.clean()`` blocks
    non-staff token rows at the model layer, so there is no non-staff
    branch here. ``?plan=null`` preserves the old inbox-only behaviour;
    ``?plan=<id>`` narrows to one plan.
    """
    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        return error_response(
            "User not found",
            "unknown_user",
            status=422,
            details={"email": "Unknown user"},
        )

    qs = visible_interview_notes_for(request.user).filter(member=user)
    plan_filter = request.GET.get("plan")
    if plan_filter == "null":
        qs = qs.filter(plan__isnull=True)
    elif plan_filter is not None:
        if not plan_filter.isdigit():
            return error_response(
                "plan must be a plan id or null",
                "validation_error",
                status=422,
                details={"plan": "Expected integer id or null"},
            )
        qs = qs.filter(plan_id=int(plan_filter))
    qs = qs.select_related("member", "created_by").order_by("-created_at")

    return JsonResponse(
        {"interview_notes": [serialize_interview_note(n) for n in qs]},
        status=200,
    )


@token_required
@csrf_exempt
@require_methods("GET", "PATCH", "DELETE")
@openapi_spec(
    tag="Interview Notes",
    summary="Retrieve, update, or delete an interview note",
    description=(
        "Routed by three URL aliases: ``/api/interview-notes/<id>``, "
        "``/api/member-notes/<id>``, ``/api/member-notes/<id>/``. The "
        "OpenAPI spec emits one operation per alias automatically. A "
        "non-staff bearer asking for an internal note id gets 404 "
        "``unknown_note`` (the bearer cannot tell whether the row "
        "exists)."
    ),
    methods={
        "GET": {
            "summary": "Retrieve an interview note",
            "responses": {
                200: {
                    "description": "Interview note detail.",
                    "example": _INTERVIEW_NOTE_EXAMPLE,
                },
                404: {
                    "description": "Note not found or not visible.",
                    "example": {
                        "error": "Interview note not found",
                        "code": "unknown_note",
                    },
                },
            },
        },
        "PATCH": {
            "summary": "Update an interview note",
            "description": (
                "Non-staff bearers cannot promote a note to "
                "``internal`` visibility."
            ),
            "request_body": {
                "properties": {
                    "body": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": _KINDS_ENUM,
                    },
                    "visibility": {
                        "type": "string",
                        "enum": _VISIBILITIES_ENUM,
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "source_type": {"type": "string", "maxLength": 40},
                    "source_metadata": {"type": "object"},
                },
                "example": {
                    "body": "Updated body",
                    "tags": ["follow-up"],
                    "source_metadata": {"channel_id": "C123"},
                },
            },
            "responses": {
                200: {
                    "description": "Note updated.",
                    "example": _INTERVIEW_NOTE_EXAMPLE,
                },
                400: {"description": "Invalid JSON body."},
                403: {
                    "description": (
                        "Non-staff bearer tried to promote to internal."
                    ),
                    "example": {
                        "error": "Cannot create or promote an internal note",
                        "code": "forbidden_internal_note",
                    },
                },
                404: {"description": "Note not found."},
                422: {"description": "Unknown kind or visibility."},
            },
        },
        "DELETE": {
            "summary": "DELETE is not available on this route",
            "description": (
                "Interview note deletion is not available through the "
                "API (issue #864); use Studio to delete a note. DELETE "
                "returns a structured 405."
            ),
            "responses": {
                405: {
                    "description": "Note deletion is not available.",
                    "example": {
                        "error": INTERVIEW_NOTE_DELETE_NOT_AVAILABLE_MESSAGE,
                        "code": "interview_note_delete_not_available",
                    },
                },
            },
        },
    },
)
def interview_note_detail(request, note_id):
    """``GET / PATCH /api/interview-notes/<note_id>/``.

    DELETE is intentionally unavailable (issue #864): it returns 405 with a
    Studio pointer before any lookup. Lookups for GET/PATCH go through
    ``visible_interview_notes_for`` so a non-staff bearer asking for an
    internal note id gets a clean 404 ``unknown_note`` -- the bearer cannot
    even tell whether the row exists.
    """
    if request.method == "DELETE":
        return delete_not_available_response(
            INTERVIEW_NOTE_DELETE_NOT_AVAILABLE_MESSAGE,
            "interview_note_delete_not_available",
        )

    note = (
        visible_interview_notes_for(request.user)
        .select_related("member", "created_by")
        .filter(pk=note_id)
        .first()
    )
    if note is None:
        return error_response(
            "Interview note not found",
            "unknown_note",
            status=404,
        )

    if request.method == "GET":
        return JsonResponse(serialize_interview_note(note), status=200)

    # PATCH
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return error_response(
            "Body must be a JSON object",
            "invalid_type",
            details={"field": "body", "expected": "object"},
        )

    update_fields = []
    if "body" in data:
        note.body = data["body"] or ""
        update_fields.append("body")
    if "kind" in data:
        if data["kind"] not in VALID_KINDS:
            return error_response(
                "Invalid kind",
                "validation_error",
                status=422,
                details={"kind": "Unknown kind"},
            )
        note.kind = data["kind"]
        update_fields.append("kind")
    if "visibility" in data:
        if data["visibility"] not in VALID_VISIBILITIES:
            return error_response(
                "Invalid visibility",
                "validation_error",
                status=422,
                details={"visibility": "Unknown visibility"},
            )
        # A non-staff bearer cannot promote a note to internal.
        if data["visibility"] == "internal" and not bearer_sees_internal_notes(
            request.user,
        ):
            return error_response(
                "Cannot create or promote an internal note",
                "forbidden_internal_note",
                status=403,
            )
        note.visibility = data["visibility"]
        update_fields.append("visibility")
    tags, tag_error = _validated_tags(data)
    if tag_error is not None:
        return tag_error
    if tags is not None:
        note.tags = tags
        update_fields.append("tags")
    source_type, source_type_error = _validated_source_type(data)
    if source_type_error is not None:
        return source_type_error
    if source_type is not None:
        note.source_type = source_type
        update_fields.append("source_type")
    source_metadata, source_metadata_error = _validated_source_metadata(data)
    if source_metadata_error is not None:
        return source_metadata_error
    if source_metadata is not None:
        note.source_metadata = source_metadata
        update_fields.append("source_metadata")

    if update_fields:
        note.save(update_fields=update_fields + ["updated_at"])

    return JsonResponse(serialize_interview_note(note), status=200)


@token_required
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Interview Notes",
    summary="Create an interview note",
    description=(
        "Routed by three URL aliases: ``/api/interview-notes``, "
        "``/api/member-notes``, ``/api/member-notes/``. The OpenAPI "
        "spec emits one operation per alias automatically."
    ),
    methods={
        "POST": {
            "summary": "Create an interview note",
            "description": (
                "Non-staff bearers cannot create a note with "
                "``visibility=internal``. The plan_id is optional; "
                "when supplied it must reference a plan the bearer can "
                "see."
            ),
            "request_body": {
                "required": ["user_email", "body"],
                "properties": {
                    "user_email": {"type": "string", "format": "email"},
                    "body": {"type": "string"},
                    "plan_id": {"type": "integer", "nullable": True},
                    "visibility": {
                        "type": "string",
                        "enum": _VISIBILITIES_ENUM,
                    },
                    "kind": {
                        "type": "string",
                        "enum": _KINDS_ENUM,
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "source_type": {"type": "string", "maxLength": 40},
                    "source_metadata": {"type": "object"},
                },
                "example": {
                    "user_email": "alice@example.com",
                    "body": "Onboarding notes...",
                    "visibility": "external",
                    "kind": "general",
                    "tags": ["manual-review"],
                },
            },
            "responses": {
                201: {
                    "description": "Note created.",
                    "example": _INTERVIEW_NOTE_EXAMPLE,
                },
                400: {"description": "Invalid JSON or missing fields."},
                403: {
                    "description": (
                        "Non-staff bearer tried to create an internal "
                        "note."
                    ),
                    "example": {
                        "error": "Cannot create an internal note",
                        "code": "forbidden_internal_note",
                    },
                },
                404: {"description": "Plan not found or not visible."},
                422: {"description": "Unknown user, kind, or visibility."},
            },
        },
    },
)
def interview_notes_create(request):
    """``POST /api/interview-notes/``."""
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return error_response(
            "Body must be a JSON object",
            "invalid_type",
            details={"field": "body", "expected": "object"},
        )

    user_email = data.get("user_email")
    if not user_email:
        return error_response(
            "Missing required field: user_email",
            "missing_field",
            details={"field": "user_email"},
        )
    body = data.get("body")
    if not body:
        return error_response(
            "Missing required field: body",
            "missing_field",
            details={"field": "body"},
        )

    member = User.objects.filter(email__iexact=user_email).first()
    if member is None:
        return error_response(
            "Unknown user",
            "unknown_user",
            status=422,
            details={"user_email": "Unknown user"},
        )

    visibility = data.get("visibility", "external")
    if visibility not in VALID_VISIBILITIES:
        return error_response(
            "Invalid visibility",
            "validation_error",
            status=422,
            details={"visibility": "Unknown visibility"},
        )
    if visibility == "internal" and not bearer_sees_internal_notes(request.user):
        return error_response(
            "Cannot create an internal note",
            "forbidden_internal_note",
            status=403,
        )

    kind = data.get("kind", "general")
    if kind not in VALID_KINDS:
        return error_response(
            "Invalid kind",
            "validation_error",
            status=422,
            details={"kind": "Unknown kind"},
        )

    tags, tag_error = _validated_tags(data)
    if tag_error is not None:
        return tag_error
    source_type, source_type_error = _validated_source_type(data)
    if source_type_error is not None:
        return source_type_error
    source_metadata, source_metadata_error = _validated_source_metadata(data)
    if source_metadata_error is not None:
        return source_metadata_error

    plan_id = data.get("plan_id")
    plan = None
    if plan_id is not None:
        if not isinstance(plan_id, int):
            return error_response(
                "plan_id must be an integer",
                "invalid_type",
                details={"field": "plan_id", "expected": "int"},
            )
        plan = visible_plans_for(request.user).filter(pk=plan_id).first()
        if plan is None:
            return error_response(
                "Plan not found",
                "unknown_plan",
                status=404,
            )

    note = InterviewNote.objects.create(
        plan=plan,
        member=member,
        visibility=visibility,
        kind=kind,
        body=body,
        tags=tags or [],
        source_type=source_type or "",
        source_metadata=source_metadata or {},
        created_by=request.user,
    )
    note = (
        InterviewNote.objects.select_related("member", "created_by")
        .get(pk=note.pk)
    )
    return JsonResponse(serialize_interview_note(note), status=201)
