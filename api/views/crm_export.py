"""CRM export endpoint (issue #1079).

One read-only, staff-token-gated endpoint that returns the COMPLETE
per-user CRM aggregate in a single response, so a member-base analysis
("what do people struggle with across all plans and notes") no longer has
to fan out across ``/api/users/<email>`` + ``/notes`` + ``/aliases`` +
``/api/sprints/<slug>/plans`` + ``/api/plans/<id>`` + onboarding +
enrollments per user.

``GET /api/crm/export`` -- read-only. ``@token_required`` (staff-owned
tokens only; non-staff tokens already 401 via ``accounts.auth``),
``@require_methods("GET")`` (any other method -> 405).

The per-user aggregate REUSES the existing single-resource serializers so
the shapes stay identical to the per-resource endpoints:

- core state          -> ``serialize_user_state`` (full, non-compact)
- ``crm_record``      -> a full record dict (issue extends the
                         ``_serialize_crm_record_summary`` shape from
                         ``api/views/users.py`` with summary / next_steps /
                         timestamps; persona resolution is shared)
- ``notes``           -> ``serialize_interview_note`` (member + interview
                         notes, internal-visibility INCLUDED for the staff
                         bearer)
- ``plans``           -> ``serialize_plan_detail`` (weeks -> checkpoints,
                         resources, deliverables, next-steps, plan-level
                         interview notes)
- ``sprint_enrollments``  -> the enrollment shape from
                             ``api/views/enrollments._serialize_enrollment``
- ``course_enrollments``  -> the shape from
                             ``api/views/course_enrollments._serialize_enrollment``
- ``onboarding_responses`` -> ``serialize_response``

Security: notes / plans are read through ``visible_interview_notes_for`` /
``visible_plans_for`` (queryset-layer gates) so the staff bearer's view is
unforgeable. A non-staff token cannot reach the endpoint at all (401).

Scope / paging mirror ``users_collection`` / ``onboarding_responses_collection``:
``scope`` (``crm`` default / ``all``), ``limit`` (clamped to the
configurable ``CRM_EXPORT_MAX_LIMIT`` ceiling), ``offset``, ``since``, and
``q``. Deterministic ordering by ``User.id``. ``email`` is an exact,
case-insensitive targeted lookup path that constrains to one user before
any aggregate prefetch / serialization work; when present it wins over
``q``.
"""

from django.contrib.auth import get_user_model
from django.db.models import Prefetch
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from accounts.lifecycle import account_lifecycle_q
from accounts.models.email_alias import EmailAlias
from accounts.utils.tags import normalize_tag
from api.openapi import openapi_spec
from api.safety import error_response
from api.serializers.onboarding import serialize_response
from api.serializers.plans import serialize_interview_note, serialize_plan_detail
from api.serializers.users import serialize_user_state
from api.utils import require_methods
from api.views._permissions import (
    visible_interview_notes_for,
    visible_plans_for,
)
from api.views.course_enrollments import (
    _serialize_enrollment as serialize_course_enrollment,
)
from api.views.enrollments import (
    _serialize_enrollment as serialize_sprint_enrollment,
)
from api.views.onboarding import _parse_offset, _persona_map, _resolve_persona
from api.views.users import (
    _parse_account_lifecycle,
    _parse_limit,
    _parse_since,
    resolve_crm_persona,
)
from integrations.config import get_config
from plans.models import (
    Checkpoint,
    Deliverable,
    InterviewNote,
    NextStep,
    Resource,
    Week,
    WeekNote,
)

User = get_user_model()

# Default page size + the registry key whose value caps it. The ceiling is
# resolved at request time via ``get_config`` (DB override -> env ->
# default) so an operator can change it from Studio with no redeploy.
EXPORT_LIMIT_DEFAULT = 200
EXPORT_MAX_LIMIT_KEY = "CRM_EXPORT_MAX_LIMIT"
EXPORT_MAX_LIMIT_DEFAULT = 200

_VALID_SCOPES = ["crm", "all"]


def _isoformat_or_none(value):
    if value is None:
        return None
    return value.isoformat()


def serialize_crm_record_full(record):
    """Full CRM-record dict for the export aggregate (issue #1079).

    Extends the compact ``serialize_crm_record_summary`` shape from
    ``api/views/users.py`` (``id`` / ``status`` / ``persona``) with
    ``summary`` / ``next_steps`` / ``created_at`` / ``updated_at``. Persona
    resolution is shared via ``resolve_crm_persona`` so both serializers
    agree. ``record`` may be ``None`` (no ``CRMRecord``), in which case
    ``None`` is returned.
    """
    if record is None:
        return None
    return {
        "id": record.pk,
        "status": record.status,
        "persona": resolve_crm_persona(record),
        "summary": record.summary or "",
        "next_steps": record.next_steps or "",
        "created_at": _isoformat_or_none(record.created_at),
        "updated_at": _isoformat_or_none(record.updated_at),
    }


def _export_max_limit():
    """Resolve the export page-size ceiling from config (issue #1079).

    Reads ``CRM_EXPORT_MAX_LIMIT`` (DB override -> env -> default 200) and
    coerces to a positive int. A blank / non-numeric / non-positive
    override falls back to the default rather than raising.
    """
    raw = get_config(EXPORT_MAX_LIMIT_KEY, EXPORT_MAX_LIMIT_DEFAULT)
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return EXPORT_MAX_LIMIT_DEFAULT
    if value <= 0:
        return EXPORT_MAX_LIMIT_DEFAULT
    return value


def _parse_export_limit(raw):
    """Parse ``limit`` and clamp to the configurable export ceiling.

    Reuses ``api.views.users._parse_limit`` for the 422 ``validation_error``
    body shape (so callers see the same error as ``users_collection``), then
    re-clamps the result to ``_export_max_limit()`` because the shared
    helper hard-caps at its own ``LIMIT_MAX`` (200) constant.
    """
    value, err = _parse_limit(raw, default=EXPORT_LIMIT_DEFAULT)
    if err is not None:
        return None, err
    return min(value, _export_max_limit()), None


def _system_tag(tag):
    """Return True for an auto-applied source-namespace tag.

    Operator-applied labels (``early-adopter``, ``paid``, ``slack-member``)
    carry no namespace; auto-applied source tags use a ``prefix:value``
    form (``stripe:active``, ``course:llm-zoomcamp``, ``sprint:may-2026``)
    -- see ``accounts/utils/tags.py``. Only a NON-system tag counts as CRM
    signal under ``scope=crm``.
    """
    return isinstance(tag, str) and ":" in tag


def _has_non_system_tag(tags):
    if not isinstance(tags, list):
        return False
    return any(not _system_tag(t) for t in tags)


def _q_matches(user, q_normalized, q_lower):
    """Mirror ``users_collection``'s ``q`` semantics for one user.

    Substring match across email / first / last name / ``stripe_customer_id``
    / ``slack_user_id`` (case-insensitive), plus a normalized substring
    match inside any tag.
    """
    haystacks = [
        user.email or "",
        user.first_name or "",
        user.last_name or "",
        user.stripe_customer_id or "",
        user.slack_user_id or "",
    ]
    for value in haystacks:
        if q_lower in value.lower():
            return True
    if q_normalized:
        for tag in user.tags or []:
            if isinstance(tag, str) and q_normalized in tag.lower():
                return True
    return False


_MEMBER_EXAMPLE = {
    "email": "alice@example.com",
    "display_name": "Alice Doe",
    "tier": {"slug": "main", "level": 20, "source": "override"},
    "tags": ["early-adopter"],
    "signup_source": "signup",
    "account_activated": True,
    "account_lifecycle": "full_account",
    "crm_record": {
        "id": 42,
        "status": "active",
        "persona": "Sam - Technical Professional",
        "summary": "Mid-career engineer pivoting into AI.",
        "next_steps": "Pair on week-2 deliverable.",
        "created_at": "2026-04-15T12:00:00+00:00",
        "updated_at": "2026-05-19T08:30:00+00:00",
    },
    "notes": [],
    "plans": [],
    "sprint_enrollments": [
        {
            "sprint_slug": "may-2026",
            "enrolled_at": "2026-04-15T12:00:00+00:00",
            "enrolled_by": "staff@example.com",
        },
    ],
    "course_enrollments": [],
    "onboarding_responses": [],
}

_EXPORT_EXAMPLE = {
    "members": [_MEMBER_EXAMPLE],
    "count": 1,
    "total": 1,
    "limit": 200,
    "offset": 0,
    "scope": "crm",
    "generated_at": "2026-06-25T10:00:00+00:00",
}


def _base_queryset():
    """Users with the per-member aggregate relations prefetched.

    Deterministic ordering by ``id``. Prefetches the relations the
    per-member serializer reads directly from cache (enrollments,
    onboarding response snapshots, crm_record, aliases) plus the
    lightweight ``plans`` / ``interview_notes`` relations the ``scope=crm``
    signal check needs. The heavy nested plan / note shapes are batch-
    fetched separately through the visibility gates (see
    ``_gated_plans_by_member`` / ``_gated_notes_by_member``) so a single
    gated query covers the whole page rather than one per member.
    """
    return (
        User.objects.select_related("tier", "attribution")
        .prefetch_related(
            "plans",
            "interview_notes",
            "sprint_enrollments__sprint",
            "sprint_enrollments__enrolled_by",
            "enrollments__course",
            "questionnaire_responses__questionnaire",
            "questionnaire_responses__response_questions",
            "questionnaire_responses__answers__selected_options",
            "questionnaire_responses__answers__option_texts",
            "crm_record__persona_ref",
            # Matched ordering so serialize_user_state's
            # ``email_aliases.order_by("email")`` reads the prefetch cache.
            Prefetch(
                "email_aliases",
                queryset=EmailAlias.objects.order_by("email"),
            ),
        )
        .order_by("id")
    )


def _has_crm_signal(user, crm_record):
    """True when the user carries any CRM signal (``scope=crm`` gate).

    Any of: a ``CRMRecord``, >=1 interview/member note, >=1 plan, >=1
    onboarding response, or any non-system tag. Reads from prefetched
    relations so this stays inside the bounded query budget.
    """
    if crm_record is not None:
        return True
    if user.interview_notes.all():
        return True
    if user.plans.all():
        return True
    if any(
        r.questionnaire_id is not None
        and r.questionnaire.purpose == "onboarding"
        for r in user.questionnaire_responses.all()
    ):
        return True
    if _has_non_system_tag(user.tags):
        return True
    return False


def _gated_notes_by_member(bearer, users):
    """Batch-fetch the bearer-visible notes for every page user at once.

    Reads through the ``visible_interview_notes_for`` queryset gate (the
    unforgeable security boundary) but filters with a single
    ``member__in`` query instead of one query per member, then groups the
    rows by member id. Avoids the N+1 a per-member ``.filter(member=user)``
    would produce across the page.
    """
    notes = (
        visible_interview_notes_for(bearer)
        .filter(member__in=users)
        .select_related("member", "created_by")
        .order_by("-created_at")
    )
    grouped = {}
    for note in notes:
        grouped.setdefault(note.member_id, []).append(note)
    return grouped


def _gated_plans_by_member(bearer, users):
    """Batch-fetch the bearer-visible plans for every page user at once.

    Reads through the ``visible_plans_for`` queryset gate (the unforgeable
    security boundary) with a single ``member__in`` query, fully
    prefetching the nested plan children so ``serialize_plan_detail`` reads
    from cache. Grouped by member id.
    """
    # ``Prefetch`` querysets match ``serialize_plan_detail`` /
    # ``serialize_week``'s ``.order_by()`` clauses exactly so the serializer
    # reads the prefetch cache instead of issuing a fresh ordered query per
    # plan / week.
    plans = (
        visible_plans_for(bearer)
        .filter(member__in=users)
        .select_related("sprint", "member")
        .prefetch_related(
            Prefetch(
                "weeks",
                queryset=Week.objects.order_by(
                    "position", "week_number",
                ).prefetch_related(
                    Prefetch(
                        "checkpoints",
                        queryset=Checkpoint.objects.order_by(
                            "position", "id",
                        ),
                    ),
                    Prefetch(
                        "notes",
                        queryset=WeekNote.objects.order_by("-created_at"),
                    ),
                ),
            ),
            Prefetch(
                "resources",
                queryset=Resource.objects.order_by("position", "id"),
            ),
            Prefetch(
                "deliverables",
                queryset=Deliverable.objects.order_by("position", "id"),
            ),
            Prefetch(
                "next_steps",
                queryset=NextStep.objects.order_by("position", "id"),
            ),
            Prefetch(
                "interview_notes",
                queryset=InterviewNote.objects.select_related(
                    "member", "created_by",
                ).order_by("-created_at"),
            ),
        )
        .order_by("-created_at", "id")
    )
    grouped = {}
    for plan in plans:
        grouped.setdefault(plan.member_id, []).append(plan)
    return grouped


def _serialize_member(
    user, bearer, persona_by_questionnaire, notes_by_member, plans_by_member,
):
    """Build one member's full CRM aggregate dict.

    Notes / plans are read through the queryset-layer gates
    (``visible_interview_notes_for`` / ``visible_plans_for``), batched once
    per page in ``_gated_notes_by_member`` / ``_gated_plans_by_member`` so
    the staff bearer's full-fidelity view is unforgeable without an N+1
    across the member set. A non-staff token (which cannot reach this
    endpoint anyway) would see only its own external rows.
    """
    payload = serialize_user_state(user)

    crm_record = getattr(user, "crm_record", None)
    payload["crm_record"] = serialize_crm_record_full(crm_record)

    payload["notes"] = [
        serialize_interview_note(n)
        for n in notes_by_member.get(user.id, [])
    ]

    payload["plans"] = [
        serialize_plan_detail(plan, viewer=bearer)
        for plan in plans_by_member.get(user.id, [])
    ]

    payload["sprint_enrollments"] = [
        {
            "sprint_slug": enrollment.sprint.slug,
            "enrolled_at": serialize_sprint_enrollment(enrollment)["enrolled_at"],
            "enrolled_by": serialize_sprint_enrollment(enrollment)["enrolled_by"],
        }
        for enrollment in user.sprint_enrollments.all()
    ]

    payload["course_enrollments"] = [
        serialize_course_enrollment(enrollment)
        for enrollment in user.enrollments.all()
    ]

    onboarding_responses = []
    for response in user.questionnaire_responses.all():
        if (
            response.questionnaire_id is None
            or response.questionnaire.purpose != "onboarding"
        ):
            continue
        persona = _resolve_persona(
            response.questionnaire,
            personas_by_questionnaire=persona_by_questionnaire,
        )
        onboarding_responses.append(
            serialize_response(response, persona=persona)
        )
    payload["onboarding_responses"] = onboarding_responses

    return payload


@token_required
@csrf_exempt
@require_methods("GET")
@openapi_spec(
    tag="CRM",
    summary="Export the full per-user CRM aggregate in one call",
    methods={
        "GET": {
            "summary": "Export the per-user CRM aggregate",
            "description": (
                "Returns the complete per-user CRM aggregate (core user "
                "state, ``crm_record``, ``notes``, nested ``plans``, sprint "
                "and course enrollments, and ``onboarding_responses``) in a "
                "single response, reusing the per-resource serializers so the "
                "shapes are identical. Staff-token only. Internal-visibility "
                "notes ARE included (this is the staff CRM surface). Defaults "
                "to ``scope=crm`` (only members carrying CRM signal); pass "
                "``scope=all`` for the full user table. Ordered by "
                "``User.id``; ``count`` is the page size, ``total`` the full "
                "match before slicing. Operator automation that needs one "
                "user should pass ``email=`` for an exact, case-insensitive "
                "lookup; this constrains the queryset before aggregate "
                "serialization instead of scanning a broad export. When "
                "``email`` and ``q`` are both supplied, ``email`` wins."
            ),
            "query": {
                "scope": {
                    "type": "string",
                    "enum": _VALID_SCOPES,
                    "required": False,
                    "description": (
                        "``crm`` (default) returns only users with CRM "
                        "signal (a CRMRecord, a note, a plan, an onboarding "
                        "response, or a non-system tag). ``all`` returns "
                        "every user. Unknown value -> 422."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "required": False,
                    "description": (
                        "Default 200; clamped to the configurable "
                        "``CRM_EXPORT_MAX_LIMIT`` ceiling."
                    ),
                },
                "offset": {
                    "type": "integer",
                    "required": False,
                    "description": "Default 0. Pages deterministically by id.",
                },
                "since": {
                    "type": "string",
                    "required": False,
                    "description": (
                        "ISO-8601 datetime; only users with "
                        "``date_joined >= since``."
                    ),
                },
                "q": {
                    "type": "string",
                    "required": False,
                    "description": (
                        "Substring search across email / name / Stripe id / "
                        "Slack id / tags (same semantics as "
                        "``GET /api/users``). Ignored when ``email`` is "
                        "supplied."
                    ),
                },
                "email": {
                    "type": "string",
                    "required": False,
                    "description": (
                        "Exact case-insensitive user email lookup for fast "
                        "targeted CRM automation. Surrounding whitespace is "
                        "trimmed. Unknown users return 404 "
                        "``user_not_found``. Use this instead of broad "
                        "``scope=all`` exports when fetching one user."
                    ),
                },
                "account_lifecycle": {
                    "type": "string",
                    "enum": ["newsletter_only", "full_account", "imported_or_unknown"],
                    "required": False,
                    "description": (
                        "Optional derived account lifecycle filter. Composes "
                        "with scope, email, q, since, limit, and offset."
                    ),
                },
            },
            "responses": {
                200: {
                    "description": "The per-user CRM aggregate page.",
                    "example": _EXPORT_EXAMPLE,
                },
                422: {
                    "description": (
                        "Invalid ``scope`` / ``limit`` / ``offset`` / "
                        "``since``."
                    ),
                    "example": {
                        "error": "Invalid scope: 'everyone'",
                        "code": "validation_error",
                        "details": {
                            "field": "scope",
                            "value": "everyone",
                            "allowed": _VALID_SCOPES,
                        },
                    },
                },
                404: {
                    "description": (
                        "``email`` was supplied but no exact user exists."
                    ),
                    "example": {
                        "error": "User not found",
                        "code": "user_not_found",
                    },
                },
            },
        },
    },
)
def crm_export(request):
    """``GET /api/crm/export`` -- the full per-user CRM aggregate."""
    scope = request.GET.get("scope")
    if scope is None or scope == "":
        scope = "crm"
    if scope not in _VALID_SCOPES:
        return error_response(
            f"Invalid scope: {scope!r}",
            "validation_error",
            status=422,
            details={
                "field": "scope",
                "value": scope,
                "allowed": list(_VALID_SCOPES),
            },
        )

    limit, err = _parse_export_limit(request.GET.get("limit"))
    if err is not None:
        return err
    offset, err = _parse_offset(request.GET.get("offset"))
    if err is not None:
        return err
    since, err = _parse_since(request.GET.get("since"))
    if err is not None:
        return err
    account_lifecycle, err = _parse_account_lifecycle(
        request.GET.get("account_lifecycle"),
    )
    if err is not None:
        return err

    q = (request.GET.get("q") or "").strip()
    q_lower = q.lower()
    q_normalized = normalize_tag(q) if q else ""
    email = (request.GET.get("email") or "").strip()

    qs = _base_queryset()
    bearer = request.user
    persona_by_questionnaire = _persona_map()

    if email:
        target_id = (
            User.objects
            .filter(email__iexact=email)
            .values_list("id", flat=True)
            .first()
        )
        if target_id is None:
            return error_response(
                "User not found", "user_not_found", status=404,
            )

        qs = qs.filter(pk=target_id)
        if since is not None:
            qs = qs.filter(date_joined__gte=since)
        if account_lifecycle:
            qs = qs.filter(account_lifecycle_q(account_lifecycle))
        matched = list(qs)

        if scope == "crm" and matched:
            user = matched[0]
            crm_record = getattr(user, "crm_record", None)
            if not _has_crm_signal(user, crm_record):
                matched = []

        page = matched[offset:offset + limit]
        return _export_response(
            page=page,
            total=len(matched),
            limit=limit,
            offset=offset,
            scope=scope,
            bearer=bearer,
            persona_by_questionnaire=persona_by_questionnaire,
        )

    if since is not None:
        qs = qs.filter(date_joined__gte=since)
    if account_lifecycle:
        qs = qs.filter(account_lifecycle_q(account_lifecycle))

    # Resolve the scope/filter match set once (the full ``total``), then
    # slice. ``_has_crm_signal`` and ``_q_matches`` read prefetched
    # relations so this loop stays inside the bounded query budget.
    matched = []
    for user in qs:
        if q and not _q_matches(user, q_normalized, q_lower):
            continue
        if scope == "crm":
            crm_record = getattr(user, "crm_record", None)
            if not _has_crm_signal(user, crm_record):
                continue
        matched.append(user)

    total = len(matched)
    page = matched[offset:offset + limit]

    return _export_response(
        page=page,
        total=total,
        limit=limit,
        offset=offset,
        scope=scope,
        bearer=bearer,
        persona_by_questionnaire=persona_by_questionnaire,
    )


def _export_response(
    *, page, total, limit, offset, scope, bearer, persona_by_questionnaire,
):
    # Batch the two gated reads once for the whole page so the gate stays
    # the unforgeable boundary without an N+1 across members.
    notes_by_member = _gated_notes_by_member(bearer, page)
    plans_by_member = _gated_plans_by_member(bearer, page)

    members = [
        _serialize_member(
            user,
            bearer,
            persona_by_questionnaire,
            notes_by_member,
            plans_by_member,
        )
        for user in page
    ]

    return JsonResponse(
        {
            "members": members,
            "count": len(members),
            "total": total,
            "limit": limit,
            "offset": offset,
            "scope": scope,
            "generated_at": timezone.now().isoformat(),
        },
        status=200,
    )
