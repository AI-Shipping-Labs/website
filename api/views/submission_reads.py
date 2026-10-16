"""Read-only staff access to existing homework submissions (#1971)."""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import require_methods
from api.views.submissions_on_behalf import submission_on_behalf as _record_submission
from content.services.submissions_on_behalf import (
    KIND_HOMEWORK,
    OnBehalfError,
    _homework_payload,
    _homework_submission_summary,
    resolve_homework,
    resolve_member,
)

GET_RESPONSE_EXAMPLE = {
    "kind": "homework",
    "user": {"id": 42, "email": "member@example.com"},
    "homework": {
        "id": 7, "slug": "week-2-homework", "title": "Week 2 homework",
        "course": "ai-buildcamp", "cohort": "1", "state": "CL",
        "accepting_submissions": False,
    },
    "submission": {
        "id": 88, "submitted_at": "2026-10-01T09:00:00+00:00",
        "homework_link": "", "learning_in_public_links": [],
        "time_spent_lectures": None, "time_spent_homework": None,
        "questions_score": 1, "total_score": 1,
        "answers": [{
            "question_id": 31, "question_key": "project-idea",
            "answer_text": "A RAG assistant", "is_correct": True,
        }],
        "submitted_on_behalf_by": "staff@example.com",
        "on_behalf_note": "Sent by email",
    },
}

GET_METHOD_SPEC = {
    "summary": "Inspect a member's homework submission",
    "description": (
        "Read-only staff lookup using ``user`` plus ``homework_id``, or "
        "``course`` + ``homework`` + optional ``cohort``."
    ),
    "query": {
        "user": {"type": "string", "required": True},
        "homework_id": {"type": "integer"},
        "course": {"type": "string"},
        "homework": {"type": "string"},
        "cohort": {"type": "string"},
    },
    "responses": {
        200: {"description": "Existing homework submission.", "example": GET_RESPONSE_EXAMPLE},
        404: {"description": "Member, homework, or submission not found."},
    },
}


@token_required(structured_errors=True)
def _show_homework_submission(request):
    """Resolve and return one existing homework submission without writes."""
    try:
        member = resolve_member(request.GET.get('user'))
        homework = resolve_homework(
            homework_id=request.GET.get('homework_id'), course=request.GET.get('course'),
            homework=request.GET.get('homework'), cohort=request.GET.get('cohort'), member=member,
        )
        submission = homework.submissions.filter(student=member).first()
        if submission is None:
            raise OnBehalfError(
                'The member has no submission for this homework',
                'submission_not_found', status=404,
            )
    except OnBehalfError as exc:
        return error_response(exc.message, exc.code, status=exc.status, details=exc.details)
    return JsonResponse({
        'kind': KIND_HOMEWORK,
        'user': {'id': member.pk, 'email': member.email},
        'homework': _homework_payload(homework),
        'submission': _homework_submission_summary(submission),
    })


@csrf_exempt
@require_methods('GET', 'POST', structured_errors=True)
@openapi_spec(
    tag="Coursework",
    summary="Read or record a member's submission on behalf of staff",
    methods={**_record_submission.__openapi_spec__["methods"], "GET": GET_METHOD_SPEC},
)
def submission_on_behalf(request):
    """Dispatch read and write methods through their existing auth boundaries."""
    if request.method == 'GET':
        return _show_homework_submission(request)
    return _record_submission(request)
