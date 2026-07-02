"""Read-only onboarding API endpoints (issue #837).

A staff-token JSON surface over the ``questionnaires`` app so an external
plan generator can (A) read the live survey shape and (B) read each
member's onboarding answers as an ordered flat Q&A feed. READ ONLY -- no
write surface, no plan-generation logic.

Four endpoints, all ``@token_required`` (staff-only 401) + ``GET``-only
(405 otherwise):

A. Survey definition
   - ``GET /api/onboarding/questionnaires`` -- questionnaires with nested
     ordered base questions + options (BASE template, not snapshots).
   - ``GET /api/onboarding/personas``       -- persona archetypes + linked
     questionnaire slug.

B. Member responses (the plan-generation feed)
   - ``GET /api/onboarding/responses/<email>`` -- one member's onboarding
     ``Response`` (draft OR submitted).
   - ``GET /api/onboarding/responses``         -- bulk feed (offset/limit/
     total, since + status + persona filters; defaults to submitted).

Both B endpoints emit the SAME response object shape, defined once in
``api/serializers/onboarding.serialize_response``. Answers are read from
the SNAPSHOT rows (``ResponseQuestion`` / ``Answer`` /
``ResponseQuestionOption``), never the base ``Question`` -- so editing or
deleting a base question after submit never changes the API output.

Persona resolution mirrors ``accounts.views.onboarding._current_self_id``:
the active ``Persona`` whose ``default_questionnaire`` is the response's
questionnaire (first by model ordering when several point at it); the
generic ``onboarding-general`` fallback -- which no persona points at --
resolves to ``null``.

Conventions are shared with ``api/views/users.py`` and
``api/views/ses_events_list.py``: ``_parse_limit`` / ``_parse_since``
(reused verbatim) and a local ``_parse_offset`` give the same 422
``validation_error`` shape; ``count`` is the TOTAL match set before
slicing.
"""

from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.serializers.onboarding import (
    serialize_persona,
    serialize_questionnaire,
    serialize_response,
)
from api.utils import require_methods
from api.views.users import _find_user, _parse_limit, _parse_since, _user_not_found_response
from questionnaires.models import (
    PURPOSE_CHOICES,
    Persona,
    Questionnaire,
    Response,
)

User = get_user_model()

# Slug of the persona-agnostic onboarding questionnaire (mirrors
# ``questionnaires.onboarding.GENERIC_ONBOARDING_SLUG``). It intentionally
# has no single persona, so responses on it resolve to ``persona=null``.
_GENERIC_ONBOARDING_SLUG = "onboarding-general"

# Allowed ``purpose`` filter values for the questionnaires endpoint -- the
# model's choice keys are the source of truth.
_VALID_PURPOSES = [choice for choice, _label in PURPOSE_CHOICES]

# Allowed ``status`` filter values for the bulk responses endpoint. ``all``
# is a convenience value (no status filter); the two concrete values match
# ``RESPONSE_STATUS_CHOICES``.
_VALID_BULK_STATUSES = ["draft", "submitted", "all"]


def _parse_offset(raw, *, field="offset"):
    """Parse the ``offset`` query param into a non-negative int.

    Mirrors ``api/views/ses_events_list._parse_offset``: zero is the first
    page, negatives are a 422 ``validation_error``.
    """
    if raw is None or raw == "":
        return 0, None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, error_response(
            f"Invalid integer: {raw!r}",
            "validation_error",
            status=422,
            details={"field": field, "value": raw},
        )
    if value < 0:
        return None, error_response(
            f"{field} must be a non-negative integer",
            "validation_error",
            status=422,
            details={"field": field, "value": raw},
        )
    return value, None


def _parse_bool(raw):
    """Parse a ``true``/``false`` query param into a bool, or ``None``.

    ``None`` means "no filter". Any value other than the two literals is
    treated as no-filter rather than an error -- the spec only filters on
    the explicit ``true``/``false`` strings.
    """
    if raw is None or raw == "":
        return None
    lowered = raw.strip().lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return None


def _resolve_persona(questionnaire, *, personas_by_questionnaire=None):
    """Resolve a questionnaire back to its persona, or ``None``.

    Mirrors ``_current_self_id`` (``accounts/views/onboarding.py``): the
    first ACTIVE ``Persona`` whose ``default_questionnaire`` is this
    questionnaire, by the model's default ordering (``order, name``). The
    generic fallback questionnaire -- which no active persona points at --
    resolves to ``None`` (it serves both "none" and "more than one").

    ``personas_by_questionnaire`` is an optional precomputed map
    ``{questionnaire_id: persona}`` so the bulk path resolves N responses
    without N queries.
    """
    if questionnaire is None:
        return None
    if personas_by_questionnaire is not None:
        return personas_by_questionnaire.get(questionnaire.id)
    return (
        Persona.objects.filter(
            default_questionnaire=questionnaire,
            is_active=True,
        )
        .order_by("order", "name")
        .first()
    )


def _persona_map():
    """Build ``{questionnaire_id: persona}`` for all active personas.

    The first active persona (by ``order, name``) wins for each
    questionnaire -- matching ``_resolve_persona``'s single-lookup
    ordering. Used by the bulk endpoint so resolving every response's
    persona is one query, not one per row.
    """
    mapping = {}
    for persona in Persona.objects.filter(is_active=True).order_by("order", "name"):
        qid = persona.default_questionnaire_id
        if qid is not None and qid not in mapping:
            mapping[qid] = persona
    return mapping


# ---- A1. Survey definition: questionnaires ---------------------------------

_QUESTIONNAIRE_EXAMPLE = {
    "slug": "onboarding-engineer",
    "title": "Engineer onboarding",
    "purpose": "onboarding",
    "description": "For engineers moving into AI.",
    "is_active": True,
    "question_count": 2,
    "questions": [
        {
            "prompt": "What is your current role?",
            "question_type": "text",
            "help_text": "",
            "is_required": True,
            "order": 0,
            "scale_min": None,
            "scale_max": None,
            "options": [],
        },
        {
            "prompt": "Which areas interest you?",
            "question_type": "multiple_choice",
            "help_text": "",
            "is_required": False,
            "order": 1,
            "scale_min": None,
            "scale_max": None,
            "options": [
                {"label": "LLM apps", "order": 0},
                {"label": "MLOps", "order": 1},
            ],
        },
    ],
}


@token_required
@csrf_exempt
@require_methods("GET")
@openapi_spec(
    tag="Onboarding",
    summary="List questionnaires with nested base questions",
    methods={
        "GET": {
            "summary": "List questionnaires (survey shape)",
            "description": (
                "Lists ``Questionnaire`` rows with nested ordered base "
                "``Question`` rows and their ordered ``QuestionOption`` "
                "rows. Reads the BASE template -- this describes the live "
                "survey shape, not any member's snapshot. Questionnaires "
                "are newest-first; questions and options follow their "
                "model ordering. No pagination (the set is small and "
                "staff-authored)."
            ),
            "query": {
                "purpose": {
                    "type": "string",
                    "required": False,
                    "description": (
                        "Exact match on ``purpose`` (one of ``onboarding`` "
                        "/ ``feedback`` / ``general``). Unknown value -> 422."
                    ),
                },
                "active": {
                    "type": "string",
                    "required": False,
                    "description": "``true``/``false`` filter on ``is_active``.",
                },
            },
            "responses": {
                200: {
                    "description": "Questionnaire list.",
                    "example": {
                        "questionnaires": [_QUESTIONNAIRE_EXAMPLE],
                        "count": 1,
                    },
                },
                422: {
                    "description": "Unknown ``purpose`` value.",
                    "example": {
                        "error": "Invalid purpose: 'bogus'",
                        "code": "validation_error",
                        "details": {
                            "field": "purpose",
                            "value": "bogus",
                            "allowed": _VALID_PURPOSES,
                        },
                    },
                },
            },
        },
    },
)
def onboarding_questionnaires(request):
    """``GET /api/onboarding/questionnaires`` -- survey shape."""
    purpose = request.GET.get("purpose") or ""
    if purpose and purpose not in _VALID_PURPOSES:
        return error_response(
            f"Invalid purpose: {purpose!r}",
            "validation_error",
            status=422,
            details={
                "field": "purpose",
                "value": purpose,
                "allowed": list(_VALID_PURPOSES),
            },
        )

    active = _parse_bool(request.GET.get("active"))

    qs = Questionnaire.objects.prefetch_related("questions__options")
    if purpose:
        qs = qs.filter(purpose=purpose)
    if active is not None:
        qs = qs.filter(is_active=active)

    rows = [serialize_questionnaire(q) for q in qs]
    return JsonResponse(
        {"questionnaires": rows, "count": len(rows)},
        status=200,
    )


# ---- A2. Survey definition: personas ---------------------------------------

_PERSONA_EXAMPLE = {
    "slug": "priya",
    "name": "Priya",
    "archetype": "The Engineer transitioning to AI",
    "description": "Mid-career engineer pivoting into AI.",
    "is_active": True,
    "order": 0,
    "default_questionnaire": "onboarding-engineer",
}


@token_required
@csrf_exempt
@require_methods("GET")
@openapi_spec(
    tag="Onboarding",
    summary="List persona archetypes",
    methods={
        "GET": {
            "summary": "List personas",
            "description": (
                "Lists ``Persona`` rows -- internal archetypes, never shown "
                "to members (staff-token only). ``default_questionnaire`` is "
                "the linked questionnaire ``slug`` or ``null``. Ordered by "
                "the model default (``order, name``)."
            ),
            "query": {
                "active": {
                    "type": "string",
                    "required": False,
                    "description": "``true``/``false`` filter on ``is_active``.",
                },
            },
            "responses": {
                200: {
                    "description": "Persona list.",
                    "example": {
                        "personas": [_PERSONA_EXAMPLE],
                        "count": 1,
                    },
                },
            },
        },
    },
)
def onboarding_personas(request):
    """``GET /api/onboarding/personas`` -- internal persona archetypes."""
    active = _parse_bool(request.GET.get("active"))

    qs = Persona.objects.select_related("default_questionnaire")
    if active is not None:
        qs = qs.filter(is_active=active)

    rows = [serialize_persona(p) for p in qs]
    return JsonResponse(
        {"personas": rows, "count": len(rows)},
        status=200,
    )


# ---- Shared response-object example for the OpenAPI docs --------------------

_RESPONSE_EXAMPLE = {
    "email": "alex@example.com",
    "questionnaire_slug": "onboarding-engineer",
    "status": "submitted",
    "submitted_at": "2026-05-19T08:30:00+00:00",
    "persona": {
        "slug": "priya",
        "name": "Priya",
        "archetype": "The Engineer transitioning to AI",
    },
    "crm_record": {
        "id": 42,
        "status": "active",
        "studio_url": "/studio/crm/42/#onboarding",
    },
    "questions": [
        {
            "prompt": "What is your current role?",
            "question_type": "text",
            "order": 0,
            "answer": "Backend engineer",
        },
        {
            "prompt": "On a scale of 1-5, how confident are you with ML?",
            "question_type": "scale",
            "order": 1,
            "answer": 3,
        },
        {
            "prompt": "Which areas interest you?",
            "question_type": "multiple_choice",
            "order": 2,
            "answer": ["LLM apps", "MLOps"],
        },
        {
            "prompt": "Anything else we should know?",
            "question_type": "long_text",
            "order": 3,
            "answer": None,
        },
    ],
}


def _onboarding_response_queryset():
    """Base queryset of onboarding responses with snapshot rows prefetched.

    Scoped to ``questionnaire__purpose == 'onboarding'`` so every read --
    per-member and bulk -- only ever considers onboarding responses.
    """
    return (
        Response.objects.filter(questionnaire__purpose="onboarding")
        .select_related("respondent", "respondent__crm_record", "questionnaire")
        .prefetch_related(
            "response_questions",
            "answers__selected_options",
            "answers__option_texts",
        )
    )


# ---- B1. Per-member response -----------------------------------------------


@token_required
@csrf_exempt
@require_methods("GET")
@openapi_spec(
    tag="Onboarding",
    summary="Get a member's onboarding response",
    methods={
        "GET": {
            "summary": "Get one member's onboarding response",
            "description": (
                "Returns the member's single onboarding ``Response`` (draft "
                "OR submitted -- ``status`` / ``submitted_at`` tell which) as "
                "the shared response object: ``email``, "
                "``questionnaire_slug``, ``status``, ``submitted_at``, "
                "resolved ``persona`` (or ``null``), additive ``crm_record`` "
                "relationship (or ``null``), and an ordered flat "
                "``questions`` Q&A list read from the SNAPSHOT "
                "(``ResponseQuestion`` / ``Answer`` / "
                "``ResponseQuestionOption``), never the base ``Question``. "
                "Email lookup is case-insensitive."
            ),
            "responses": {
                200: {
                    "description": "The member's onboarding response.",
                    "example": _RESPONSE_EXAMPLE,
                },
                404: {
                    "description": (
                        "Unknown email (``user_not_found``) or known user "
                        "with no onboarding response "
                        "(``onboarding_response_not_found``)."
                    ),
                    "example": {
                        "error": "No onboarding response",
                        "code": "onboarding_response_not_found",
                    },
                },
            },
        },
    },
)
def onboarding_response_detail(request, email):
    """``GET /api/onboarding/responses/<email>`` -- per-member feed."""
    user = _find_user(email)
    if user is None:
        return _user_not_found_response()

    response = (
        _onboarding_response_queryset().filter(respondent=user).first()
    )
    if response is None:
        return error_response(
            "No onboarding response",
            "onboarding_response_not_found",
            status=404,
        )

    persona = _resolve_persona(response.questionnaire)
    return JsonResponse(
        serialize_response(response, persona=persona),
        status=200,
    )


# ---- B2. Bulk responses feed -----------------------------------------------


@token_required
@csrf_exempt
@require_methods("GET")
@openapi_spec(
    tag="Onboarding",
    summary="List onboarding responses (bulk feed)",
    methods={
        "GET": {
            "summary": "List onboarding responses for plan generation",
            "description": (
                "Lists onboarding responses (the shared response object per "
                "item) for batch plan generation. Defaults to "
                "``status=submitted`` (only finished responses); pass "
                "``status=draft`` or ``status=all`` to widen. ``since`` is "
                "an inclusive lower bound on ``submitted_at`` when filtering "
                "submitted, else on ``created_at``. ``persona`` filters by "
                "resolved persona ``slug`` (or the literal ``none`` for "
                "responses with no resolved persona). Each item includes an "
                "additive ``crm_record`` relationship when a CRM record "
                "exists. ``count`` is the TOTAL matching set BEFORE slicing."
            ),
            "query": {
                "status": {
                    "type": "string",
                    "required": False,
                    "description": (
                        "``draft`` / ``submitted`` / ``all``. Default "
                        "``submitted``. Unknown value -> 422."
                    ),
                },
                "since": {
                    "type": "string",
                    "required": False,
                    "description": (
                        "ISO-8601 datetime; inclusive lower bound on "
                        "``submitted_at`` (submitted) or ``created_at``."
                    ),
                },
                "persona": {
                    "type": "string",
                    "required": False,
                    "description": (
                        "Persona ``slug`` to filter by resolved persona, or "
                        "``none`` for responses with no resolved persona. "
                        "Unknown slug -> 422."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "required": False,
                    "description": "Default 50; clamped to 200.",
                },
                "offset": {
                    "type": "integer",
                    "required": False,
                    "description": "Default 0.",
                },
            },
            "responses": {
                200: {
                    "description": "Onboarding responses page.",
                    "example": {
                        "responses": [_RESPONSE_EXAMPLE],
                        "count": 1,
                        "limit": 50,
                        "offset": 0,
                    },
                },
                422: {
                    "description": "Invalid ``status`` / ``persona`` / filters.",
                    "example": {
                        "error": "Invalid status: 'done'",
                        "code": "validation_error",
                        "details": {
                            "field": "status",
                            "value": "done",
                            "allowed": _VALID_BULK_STATUSES,
                        },
                    },
                },
            },
        },
    },
)
def onboarding_responses_collection(request):
    """``GET /api/onboarding/responses`` -- bulk plan-generation feed."""
    limit, err = _parse_limit(request.GET.get("limit"))
    if err is not None:
        return err
    offset, err = _parse_offset(request.GET.get("offset"))
    if err is not None:
        return err
    since, err = _parse_since(request.GET.get("since"))
    if err is not None:
        return err

    status = request.GET.get("status")
    if status is None or status == "":
        status = "submitted"
    if status not in _VALID_BULK_STATUSES:
        return error_response(
            f"Invalid status: {status!r}",
            "validation_error",
            status=422,
            details={
                "field": "status",
                "value": status,
                "allowed": list(_VALID_BULK_STATUSES),
            },
        )

    persona_filter = request.GET.get("persona")
    if persona_filter == "":
        persona_filter = None
    if persona_filter is not None and persona_filter != "none":
        if not Persona.objects.filter(slug=persona_filter).exists():
            return error_response(
                f"Unknown persona: {persona_filter!r}",
                "validation_error",
                status=422,
                details={"field": "persona", "value": persona_filter},
            )

    filtering_submitted = status == "submitted"

    qs = _onboarding_response_queryset()
    if status != "all":
        qs = qs.filter(status=status)
    if since is not None:
        if filtering_submitted:
            qs = qs.filter(submitted_at__gte=since)
        else:
            qs = qs.filter(created_at__gte=since)

    # Newest-first by the meaningful timestamp for the chosen status.
    order_field = "-submitted_at" if filtering_submitted else "-created_at"
    qs = qs.order_by(order_field)

    # Resolve personas in one query, then (optionally) filter in Python so
    # the persona slug / ``none`` filter uses the same resolution logic the
    # serialized payload reports.
    persona_by_questionnaire = _persona_map()

    matched = []
    for response in qs:
        persona = _resolve_persona(
            response.questionnaire,
            personas_by_questionnaire=persona_by_questionnaire,
        )
        if persona_filter is not None:
            if persona_filter == "none":
                if persona is not None:
                    continue
            elif persona is None or persona.slug != persona_filter:
                continue
        matched.append((response, persona))

    total = len(matched)
    page = matched[offset:offset + limit]
    rows = [
        serialize_response(response, persona=persona)
        for response, persona in page
    ]
    return JsonResponse(
        {
            "responses": rows,
            "count": total,
            "limit": limit,
            "offset": offset,
        },
        status=200,
    )
