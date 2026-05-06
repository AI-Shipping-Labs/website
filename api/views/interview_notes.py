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
- ``GET /api/interview-notes/<id>/``
- ``POST /api/interview-notes/``
- ``PATCH /api/interview-notes/<id>/``
- ``DELETE /api/interview-notes/<id>/``
"""

from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.safety import error_response
from api.serializers.plans import serialize_interview_note
from api.utils import parse_json_body, require_methods
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


# All staff-or-not branches in this module go through
# ``bearer_sees_internal_notes`` from ``_permissions.py``. There is no
# local privilege check; ``api/views/_permissions.py`` is the single
# place in ``api/views/`` that may inspect user attributes for staff
# resolution.


@token_required
@csrf_exempt
@require_methods("GET")
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
def user_interview_notes(request, email):
    """``GET /api/users/<email>/interview-notes/``.

    Staff: every note for this user (including the ``plan IS NULL``
    inbox). Non-staff: only when ``email`` matches their own and the
    result is filtered to ``external``.
    """
    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        return error_response(
            "User not found",
            "unknown_user",
            status=422,
            details={"email": "Unknown user"},
        )

    # Non-staff: only allowed for the bearer's own email. We detect
    # "non-staff" by checking whether the bearer can see internal notes
    # for this user; if not, and the email isn't their own, deny.
    if not bearer_sees_internal_notes(request.user):
        if user.pk != request.user.pk:
            return error_response(
                "Cannot read another user's interview notes",
                "forbidden_other_user_plan",
                status=403,
            )

    qs = visible_interview_notes_for(request.user).filter(
        member=user, plan__isnull=True,
    ).select_related("member", "created_by").order_by("-created_at")

    return JsonResponse(
        {"interview_notes": [serialize_interview_note(n) for n in qs]},
        status=200,
    )


@token_required
@csrf_exempt
@require_methods("GET", "PATCH", "DELETE")
def interview_note_detail(request, note_id):
    """``GET / PATCH / DELETE /api/interview-notes/<note_id>/``.

    Lookups go through ``visible_interview_notes_for`` so a non-staff
    bearer asking for an internal note id gets a clean 404 ``unknown_note``
    -- the bearer cannot even tell whether the row exists.
    """
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

    if request.method == "DELETE":
        note.delete()
        return JsonResponse({}, status=204)

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

    if update_fields:
        note.save(update_fields=update_fields + ["updated_at"])

    return JsonResponse(serialize_interview_note(note), status=200)


@token_required
@csrf_exempt
@require_methods("POST")
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
        created_by=request.user,
    )
    note = (
        InterviewNote.objects.select_related("member", "created_by")
        .get(pk=note.pk)
    )
    return JsonResponse(serialize_interview_note(note), status=201)
