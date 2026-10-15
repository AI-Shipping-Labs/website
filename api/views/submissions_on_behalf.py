"""Staff endpoints for recording coursework on a member's behalf.

- ``POST /api/submissions/on-behalf`` records (or, with ``replace``,
  overwrites) a member's homework or project submission -- e.g. answers
  the member sent by email. Optional ``submitted_at`` backdates it.
- ``GET /api/courses/<slug>/homework`` lists a course's homework with the
  question keys the ``answers`` object of the POST uses.

Both are staff-only (``token_required``). All writing goes through
``content.services.submissions_on_behalf``: the learner scoring path, an
on-behalf marker on the submission, and one ``CommunityAuditLog`` row
naming the acting staff user and token. The member is NOT notified unless
``notify: true``. ``dry_run: true`` validates and scores without writing.
"""

from django.db.models import Count
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import parse_json_body, require_methods
from content.models import Course
from content.models.homework import Homework
from content.services.submissions_on_behalf import (
    KIND_HOMEWORK,
    KINDS,
    Actor,
    OnBehalfError,
    resolve_homework,
    resolve_member,
    resolve_project,
    submit_homework_on_behalf,
    submit_project_on_behalf,
)

_HOMEWORK_FIELDS = (
    'answers', 'homework_link', 'learning_in_public_links',
    'time_spent_lectures', 'time_spent_homework',
)
_PROJECT_FIELDS = (
    'project_url', 'description', 'commit_id', 'learning_in_public_links',
    'time_spent',
)


def _actor_label(request):
    """The calling token's name, or its masked prefix when unnamed."""
    token = getattr(request, 'auth_token', None)
    if token is None:
        return 'unknown'
    return token.name or token.key_prefix


def _flag(data, name):
    value = data.get(name, False)
    if not isinstance(value, bool):
        raise OnBehalfError(
            f'{name} must be a boolean', 'invalid_type',
            details={'field': name, 'expected': 'boolean'},
        )
    return value


_RESPONSE_EXAMPLE = {
    "kind": "homework",
    "dry_run": False,
    "status": "created",
    "user": {"id": 42, "email": "member@example.com"},
    "homework": {
        "id": 7, "slug": "week-2-homework", "title": "Week 2 homework",
        "course": "ai-buildcamp", "cohort": "1", "state": "CL",
        "accepting_submissions": False,
    },
    "cohort_enrollment_created": False,
    "replaced_submission": None,
    "notify": False,
    "audit_log_id": 1301,
    "submission": {
        "id": 88,
        "submitted_at": "2026-10-01T09:00:00+00:00",
        "homework_link": "",
        "learning_in_public_links": [],
        "time_spent_lectures": None,
        "time_spent_homework": None,
        "questions_score": 1,
        "total_score": 1,
        "answers": [{
            "question_id": 31, "question_key": "project-idea",
            "answer_text": "A RAG assistant for ...", "is_correct": True,
        }],
        "submitted_on_behalf_by": "staff@example.com",
        "on_behalf_note": "Sent by email on 2026-10-01",
    },
}


@token_required(structured_errors=True)
@csrf_exempt
@require_methods('POST', structured_errors=True)
@openapi_spec(
    tag="Coursework",
    summary="Record a homework or project submission on a member's behalf (staff-only)",
    methods={
        "POST": {
            "summary": "Create or replace a member's submission",
            "description": (
                "Records a submission for ``user`` (email -- primary, then "
                "alias -- or numeric id) as if they had submitted it, "
                "scored by the same code as a learner's own submit. "
                "``kind: \"homework\"`` targets ``homework_id``, or "
                "``course`` + ``homework`` (slug) + optional ``cohort`` "
                "key; ``answers`` maps question keys (from "
                "``GET /api/courses/<slug>/homework``, or a question id) "
                "to answers -- text, or 1-based option index(es) / exact "
                "option text for choice questions. ``kind: \"project\"`` "
                "targets ``course`` + optional ``project`` (a dated "
                "attempt slug) and needs ``project_url``. Homework "
                "open/closed state is not checked. ``submitted_at`` "
                "(ISO-8601, not in the future) backdates the submission. "
                "The row is marked ``submitted_on_behalf_by`` with "
                "``note``, and one ``submission_on_behalf`` audit-log row "
                "names the acting staff user and token. An existing "
                "submission returns 409 ``submission_exists`` unless "
                "``replace: true``; a project already in peer review "
                "returns 409 ``submission_locked``. ``dry_run: true`` "
                "validates and scores but writes nothing (status "
                "``would_create`` / ``would_replace``). The member gets no "
                "notification unless ``notify: true`` (bell + "
                "``submission_recorded`` email)."
            ),
            "request_body": {
                "properties": {
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "user": {"type": "string", "description": "Email or numeric user id."},
                    "homework_id": {"type": "integer"},
                    "course": {"type": "string"},
                    "homework": {"type": "string", "description": "Homework slug."},
                    "cohort": {"type": "string", "description": "Cohort external key."},
                    "project": {"type": "string", "description": "Dated project attempt slug."},
                    "answers": {"type": "object"},
                    "homework_link": {"type": "string"},
                    "learning_in_public_links": {
                        "type": "array", "items": {"type": "string"},
                    },
                    "time_spent_lectures": {"type": "number"},
                    "time_spent_homework": {"type": "number"},
                    "project_url": {"type": "string"},
                    "description": {"type": "string"},
                    "commit_id": {"type": "string"},
                    "time_spent": {"type": "number"},
                    "submitted_at": {"type": "string", "format": "date-time"},
                    "note": {"type": "string"},
                    "replace": {"type": "boolean"},
                    "dry_run": {"type": "boolean"},
                    "notify": {"type": "boolean"},
                },
                "required": ["kind", "user"],
                "example": {
                    "kind": "homework",
                    "user": "member@example.com",
                    "course": "ai-buildcamp",
                    "homework": "week-2-homework",
                    "answers": {"project-idea": "A RAG assistant for ..."},
                    "submitted_at": "2026-10-01T09:00:00Z",
                    "note": "Sent by email on 2026-10-01",
                    "dry_run": True,
                },
            },
            "responses": {
                200: {
                    "description": "Dry-run preview (nothing written).",
                    "example": {**_RESPONSE_EXAMPLE, "dry_run": True, "status": "would_create", "audit_log_id": None},
                },
                201: {
                    "description": "Submission created or replaced.",
                    "example": _RESPONSE_EXAMPLE,
                },
                400: {
                    "description": (
                        "Invalid JSON or ``ambiguous_homework`` (pass "
                        "``cohort``; ``details.candidates`` lists them)."
                    ),
                },
                404: {
                    "description": (
                        "``user_not_found``, ``unknown_course``, "
                        "``unknown_cohort``, ``unknown_homework`` or "
                        "``unknown_project``."
                    ),
                },
                409: {
                    "description": (
                        "``submission_exists`` (pass ``replace``) or "
                        "``submission_locked`` (project in peer review); "
                        "``details.existing_submission`` shows it."
                    ),
                },
                422: {
                    "description": (
                        "Missing field, bad type, unknown question, invalid "
                        "answer, URL, link list, hours, note, or a future "
                        "``submitted_at``."
                    ),
                },
            },
        },
    },
)
def submission_on_behalf(request):
    """``POST /api/submissions/on-behalf``."""
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    try:
        if not isinstance(data, dict):
            raise OnBehalfError(
                'Body must be a JSON object', 'invalid_type',
                details={'field': 'body', 'expected': 'object'},
            )
        kind = data.get('kind')
        if kind not in KINDS:
            raise OnBehalfError(
                f'kind must be one of: {", ".join(KINDS)}', 'invalid_value',
                details={'field': 'kind', 'allowed': list(KINDS)},
            )
        other_fields = _PROJECT_FIELDS if kind == KIND_HOMEWORK else _HOMEWORK_FIELDS
        own_fields = _HOMEWORK_FIELDS if kind == KIND_HOMEWORK else _PROJECT_FIELDS
        stray = sorted(f for f in other_fields if f in data and f not in own_fields)
        if stray:
            raise OnBehalfError(
                f'Fields not used for kind {kind!r}: {", ".join(stray)}',
                'unknown_field', details={'fields': stray},
            )
        member = resolve_member(data.get('user'))
        common = {
            'actor': Actor(user=request.user, token_label=_actor_label(request)),
            'member': member,
            'submitted_at': data.get('submitted_at'),
            'note': data.get('note'),
            'replace': _flag(data, 'replace'),
            'dry_run': _flag(data, 'dry_run'),
            'notify': _flag(data, 'notify'),
        }
        if kind == KIND_HOMEWORK:
            homework = resolve_homework(
                homework_id=data.get('homework_id'),
                course=data.get('course'),
                homework=data.get('homework'),
                cohort=data.get('cohort'),
                member=member,
            )
            payload = submit_homework_on_behalf(
                homework=homework,
                **{field: data.get(field) for field in _HOMEWORK_FIELDS},
                **common,
            )
        else:
            course, attempt = resolve_project(
                course=data.get('course'), project=data.get('project'),
            )
            payload = submit_project_on_behalf(
                course=course, attempt=attempt,
                **{field: data.get(field) for field in _PROJECT_FIELDS},
                **common,
            )
    except OnBehalfError as exc:
        return error_response(exc.message, exc.code, status=exc.status, details=exc.details)
    return JsonResponse(payload, status=200 if payload['dry_run'] else 201)


@token_required(structured_errors=True)
@require_methods('GET', structured_errors=True)
@openapi_spec(
    tag="Coursework",
    summary="List a course's homework and question keys (staff-only)",
    methods={
        "GET": {
            "summary": "List homework with cohorts and question keys",
            "description": (
                "Every homework of the course, per cohort, with its state "
                "and questions. ``questions[].key`` is what "
                "``POST /api/submissions/on-behalf`` takes in ``answers``; "
                "choice questions list their ``options`` (answer with the "
                "1-based index or the exact text). Correct answers are "
                "never included."
            ),
            "responses": {
                200: {
                    "description": "Homework list.",
                    "example": {
                        "course": "ai-buildcamp",
                        "homework": [{
                            "id": 7, "slug": "week-2-homework",
                            "title": "Week 2 homework", "cohort": "1",
                            "state": "OP", "due_date": None,
                            "submission_count": 12,
                            "questions": [{
                                "id": 31, "key": "project-idea",
                                "text": "Describe your project idea",
                                "type": "FL", "options": [],
                            }],
                        }],
                    },
                },
                404: {"description": "``unknown_course``."},
            },
        },
    },
)
def course_homework_list(request, slug):
    """``GET /api/courses/<slug>/homework``."""
    course = Course.objects.filter(slug=slug).first()
    if course is None:
        return error_response('Course not found', 'unknown_course', status=404)
    homeworks = (
        Homework.objects.filter(cohort__course=course)
        .select_related('cohort')
        .annotate(submission_count=Count('submissions'))
        .prefetch_related('questions')
        .order_by('cohort__start_date', 'cohort_id', 'due_date', 'pk')
    )
    return JsonResponse({
        'course': course.slug,
        'homework': [
            {
                'id': homework.pk,
                'slug': homework.slug,
                'title': homework.title,
                'cohort': homework.cohort.external_key or None,
                'cohort_name': homework.cohort.name,
                'state': homework.state,
                'due_date': homework.due_date.isoformat() if homework.due_date else None,
                'submission_count': homework.submission_count,
                'questions': [
                    {
                        'id': question.pk,
                        'key': question.source_question_id or f'q-{question.pk}',
                        'text': question.text,
                        'type': question.question_type,
                        'options': question.options_list,
                    }
                    for question in homework.questions.all()
                ],
            }
            for homework in homeworks
        ],
    })
