"""Staff state changes for a course's homework and project (capstone) attempts.

- ``GET|PATCH /api/courses/<slug>/homework/<homework_id>`` -- read or set a
  homework's state (``CL`` closed, ``OP`` open, ``SC`` scored).
- ``GET|PATCH /api/courses/<slug>/projects/<project_id>`` -- read or set a
  project attempt's state (``CL``, ``CS``, ``PR``, ``CO``).

Homework follows CMP and ``community_base.coursework.homework_state``
(DataTalksClub/community-base#423): the value is applied as is, with no
transition table and no side effects, and an unknown value is
``400 invalid_homework_state`` with ``valid_states``. The package service
works on the package's ``Homework`` model; AISL keeps its own mirror model
(``content.Homework``), so the parsing comes from the package and the
row-locked write is done here the same way.

Project attempts go through ``content.services.project_state``, which refuses
self-paced attempts and only completes a project that is peer reviewing.
"""

import logging

from community_base.coursework.homework_state import (
    VALID_HOMEWORK_STATES,
    InvalidHomeworkState,
    parse_homework_state,
)
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.actors import actor_label
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import body_must_be_object_response, parse_json_body, require_methods
from content.models import Course, CourseProject, Homework
from content.models.peer_review import ProjectState
from content.services.project_state import ProjectStateError, parse_state, set_project_state

logger = logging.getLogger(__name__)

VALID_PROJECT_STATES = tuple(state.value for state in ProjectState)

_HOMEWORK_EXAMPLE = {
    "id": 7,
    "slug": "week-2-homework",
    "title": "Week 2 homework",
    "course": "ai-buildcamp",
    "cohort": "4",
    "state": "CL",
    "state_label": "Closed",
    "due_date": "2026-11-16T22:59:00+00:00",
    "accepting_submissions": False,
}

_PROJECT_EXAMPLE = {
    "id": 45,
    "slug": "attempt-1",
    "title": "Project Attempt 1",
    "course": "ai-buildcamp",
    "cohort": "4",
    "self_paced": False,
    "state": "CL",
    "state_label": "Closed",
    "submission_due_at": "2026-11-16T22:59:00+00:00",
    "review_due_at": "2026-11-23T22:59:00+00:00",
}


def _iso(value):
    return value.isoformat() if value else None


def _serialize_homework(course, homework):
    return {
        "id": homework.pk,
        "slug": homework.slug,
        "title": homework.title,
        "course": course.slug,
        "cohort": homework.cohort.external_key or None,
        "state": homework.state,
        "state_label": homework.get_state_display(),
        "due_date": _iso(homework.due_date),
        "accepting_submissions": homework.is_accepting_submissions,
    }


def _serialize_project(course, project):
    return {
        "id": project.pk,
        "slug": project.slug,
        "title": project.title,
        "course": course.slug,
        "cohort": project.cohort.external_key if project.cohort else None,
        "self_paced": project.is_self_paced,
        "state": project.state,
        "state_label": project.get_state_display(),
        "submission_due_at": _iso(project.submission_due_at),
        "review_due_at": _iso(project.review_due_at),
    }


def _state_from_body(request):
    """Return ``(raw_state, error_response)`` from a ``{"state": ...}`` body."""
    body, err = parse_json_body(request)
    if err is not None:
        return None, err
    if not isinstance(body, dict):
        return None, body_must_be_object_response()
    state = body.get("state")
    if not isinstance(state, str) or not state.strip():
        return None, error_response(
            "Body must include a non-empty 'state' string.",
            "missing_state",
            status=400,
        )
    return state, None


def _invalid_state_response(message, code, valid_states):
    """CMP's shape: ``{"error", "code", "valid_states"}`` with status 400."""
    return JsonResponse(
        {"error": message, "code": code, "valid_states": list(valid_states)},
        status=400,
    )


def _change_payload(serialized, previous):
    return {**serialized, "previous_state": previous, "changed": previous != serialized["state"]}


@transaction.atomic
def set_homework_state(homework, state):
    """Set an AISL homework's state; returns ``(previous, new)`` codes.

    Same contract as ``community_base.coursework.homework_state.set_homework_state``:
    the row is re-read under a lock, only ``state`` is saved, and the passed
    instance is updated in place.
    """
    target = parse_homework_state(state)
    locked = Homework.objects.select_for_update().get(pk=homework.pk)
    previous = locked.state
    if previous != target.value:
        locked.state = target.value
        locked.save(update_fields=["state"])
    homework.state = target.value
    return previous, target.value


_STATE_BODY = {
    "properties": {
        "state": {
            "type": "string",
            "description": "New state: a code or a name, case-insensitive.",
        },
    },
    "required": ["state"],
}


@token_required
@csrf_exempt
@require_methods("GET", "PATCH")
@openapi_spec(
    tag="Course Homework",
    summary="Read or change a homework's state (staff-only)",
    methods={
        "GET": {
            "summary": "Get a homework's state (staff-only)",
            "description": "Read one homework of the course with its state.",
            "responses": {
                200: {"description": "Homework.", "example": _HOMEWORK_EXAMPLE},
                404: {"description": "``unknown_course`` or ``unknown_homework``."},
            },
        },
        "PATCH": {
            "summary": "Set a homework's state (staff-only)",
            "description": (
                "Set ``state`` to ``CL`` (closed: stops new submissions), ``OP`` "
                "(open) or ``SC`` (scored). Names (``closed``, ``open``, "
                "``scored``) work too. CMP parity: the value is applied as is, "
                "with no transition table and no side effects (no scoring, no "
                "emails). Setting the current state is a no-op with "
                "``changed: false``. Homework ids come from ``GET "
                "/api/courses/<slug>/homework``."
            ),
            "request_body": _STATE_BODY,
            "responses": {
                200: {
                    "description": "Updated homework.",
                    "example": {**_HOMEWORK_EXAMPLE, "previous_state": "OP", "changed": True},
                },
                400: {
                    "description": "``invalid_homework_state`` (with ``valid_states``) or ``missing_state``.",
                    "example": {
                        "error": "Invalid state 'done'. Must be one of: ['CL', 'OP', 'SC']",
                        "code": "invalid_homework_state",
                        "valid_states": ["CL", "OP", "SC"],
                    },
                },
                404: {"description": "``unknown_course`` or ``unknown_homework``."},
            },
        },
    },
)
def course_homework_detail(request, slug, homework_id):
    """``GET|PATCH /api/courses/<slug>/homework/<homework_id>``."""
    course = Course.objects.filter(slug=slug).first()
    if course is None:
        return error_response("Course not found", "unknown_course", status=404)
    homework = (
        Homework.objects.filter(pk=homework_id, cohort__course=course)
        .select_related("cohort")
        .first()
    )
    if homework is None:
        return error_response("Homework not found", "unknown_homework", status=404)
    if request.method == "GET":
        return JsonResponse(_serialize_homework(course, homework))

    raw_state, err = _state_from_body(request)
    if err is not None:
        return err
    try:
        previous, new = set_homework_state(homework, raw_state)
    except InvalidHomeworkState as exc:
        return _invalid_state_response(str(exc), "invalid_homework_state", VALID_HOMEWORK_STATES)
    if previous != new:
        logger.info(
            "Homework state changed via API: course=%s homework=%s %s -> %s by %s",
            course.slug, homework.pk, previous, new, actor_label(request),
        )
    return JsonResponse(_change_payload(_serialize_homework(course, homework), previous))


@token_required
@csrf_exempt
@require_methods("GET", "PATCH")
@openapi_spec(
    tag="Course Peer Reviews",
    summary="Read or change a project attempt's state (staff-only)",
    methods={
        "GET": {
            "summary": "Get a project attempt's state (staff-only)",
            "description": "Read one project (capstone) attempt of the course with its state.",
            "responses": {
                200: {"description": "Project attempt.", "example": _PROJECT_EXAMPLE},
                404: {"description": "``unknown_course`` or ``unknown_project``."},
            },
        },
        "PATCH": {
            "summary": "Set a project attempt's state (staff-only)",
            "description": (
                "Set ``state`` to ``CL`` (closed), ``CS`` (collecting "
                "submissions), ``PR`` (peer reviewing) or ``CO`` (completed). "
                "Names (``closed``, ``collecting_submissions``, ...) work too. "
                "Only a project in ``PR`` can be completed, and self-paced "
                "attempts have no state (both ``409 invalid_transition``). "
                "Changing the state sends nothing; use "
                "``POST /api/courses/<slug>/peer-reviews/assignments`` to start "
                "peer review with assignments and emails. Project ids come from "
                "``GET /api/courses/<slug>/peer-reviews``."
            ),
            "request_body": _STATE_BODY,
            "responses": {
                200: {
                    "description": "Updated project attempt.",
                    "example": {**_PROJECT_EXAMPLE, "previous_state": "CS", "changed": True},
                },
                400: {
                    "description": "``invalid_project_state`` (with ``valid_states``) or ``missing_state``.",
                },
                404: {"description": "``unknown_course`` or ``unknown_project``."},
                409: {
                    "description": "``invalid_transition``.",
                    "example": {
                        "error": "Only a project in Peer reviewing can be completed (it is Closed).",
                        "code": "invalid_transition",
                    },
                },
            },
        },
    },
)
def course_project_detail(request, slug, project_id):
    """``GET|PATCH /api/courses/<slug>/projects/<project_id>``."""
    course = Course.objects.filter(slug=slug).first()
    if course is None:
        return error_response("Course not found", "unknown_course", status=404)
    project = (
        CourseProject.objects.filter(pk=project_id, course=course)
        .select_related("cohort")
        .first()
    )
    if project is None:
        return error_response("Project attempt not found", "unknown_project", status=404)
    if request.method == "GET":
        return JsonResponse(_serialize_project(course, project))

    raw_state, err = _state_from_body(request)
    if err is not None:
        return err
    try:
        target = parse_state(raw_state)
    except ProjectStateError as exc:
        return _invalid_state_response(str(exc), "invalid_project_state", VALID_PROJECT_STATES)
    try:
        with transaction.atomic():
            locked = CourseProject.objects.select_for_update().select_related("cohort").get(pk=project.pk)
            previous = set_project_state(locked, target, actor=request.user)
    except ProjectStateError as exc:
        return error_response(str(exc), "invalid_transition", status=409)
    if previous != locked.state:
        logger.info(
            "Project state changed via API: course=%s project=%s %s -> %s by %s",
            course.slug, locked.pk, previous, locked.state, actor_label(request),
        )
    return JsonResponse(_change_payload(_serialize_project(course, locked), str(previous)))

