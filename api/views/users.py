"""User Management API endpoints (issue #764).

Seven endpoints under ``/api/users/`` that let operators answer the
common "is this user unsubscribed / bounced?" / "manually unsubscribe X"
questions over HTTP instead of via a Studio session or
``manage.py shell``:

Reads:

- ``GET  /api/users``                         -- search / list (compact rows).
- ``GET  /api/users/<email>``                 -- single-user state.
- ``GET  /api/users/<email>/ses-events``      -- inbound SES history.
- ``GET  /api/users/<email>/email-log``       -- outbound email log.

Writes (audited, narrow, idempotent):

- ``PATCH  /api/users/<email>``               -- ``unsubscribed`` / ``email_verified``.
- ``POST   /api/users/<email>/tags``          -- add one tag.
- ``DELETE /api/users/<email>/tags/<tag>``    -- remove one tag.

NOT exposed by design (Studio-only):

- ``DELETE /api/users/<email>``               -- destructive.
- email rename / password reset                -- PII cascade.
- tier change                                  -- Stripe webhooks + TierOverride own this.

Cross-cutting:

- All endpoints are ``@token_required`` -> staff-owned tokens (see
  ``accounts.auth.token_required`` which already gates non-staff tokens
  to 401).
- Every endpoint carries ``@openapi_spec`` so the
  ``AllApiViewsHaveOpenApiSpecTest`` walker stays green.
- Writes wrap user-mutation + audit-log insert in
  ``transaction.atomic()`` so the audit row can't drift from the user
  write.
- Every write produces exactly one ``CommunityAuditLog`` row attributed
  to the token's ``name`` (falling back to ``key_prefix``) -- including
  no-op writes (attempting an action is itself an auditable operator
  decision).
"""

from datetime import datetime

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from accounts.services.email_resolution import resolve_user_by_email
from accounts.utils.bounce import mark_permanent_bounce, record_soft_bounce
from accounts.utils.tags import add_tag, normalize_tag, remove_tag
from api.openapi import openapi_spec
from api.safety import error_response
from api.serializers.users import (
    serialize_email_log,
    serialize_ses_event,
    serialize_user_state,
)
from api.utils import parse_json_body, require_methods
from community.models import CommunityAuditLog
from crm.models import CRMRecord
from crm.services.activity_context import (
    ACTIVITY_CATEGORIES,
    ACTIVITY_CATEGORY_ALL,
    DEFAULT_ACTIVITY_LIMIT,
    MAX_ACTIVITY_LIMIT,
    build_activity_context,
    is_valid_activity_category,
    normalize_activity_category,
    serialize_activity_for_api,
)
from email_app.models import EmailLog, SesEvent
from questionnaires.onboarding import get_onboarding_response

User = get_user_model()


# ---- Parameter parsing helpers ---------------------------------------------
#
# Mirrors ``api/views/worker.py``'s shape so callers see the same
# 422 ``validation_error`` body for ``limit`` / ``since`` parse
# failures regardless of which endpoint they hit. The two helpers are
# intentionally duplicated rather than extracted to ``api/utils`` for
# now -- if a third caller appears, lifting them is a one-PR refactor.

LIMIT_DEFAULT = 50
LIMIT_MAX = 200


def _parse_limit(raw, *, default=LIMIT_DEFAULT, field="limit"):
    if raw is None or raw == "":
        return default, None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, error_response(
            f"Invalid integer: {raw!r}",
            "validation_error",
            status=422,
            details={"field": field, "value": raw},
        )
    if value < 1:
        return None, error_response(
            f"{field} must be a positive integer",
            "validation_error",
            status=422,
            details={"field": field, "value": raw},
        )
    return min(value, LIMIT_MAX), None


def _parse_since(raw, *, field="since"):
    if raw is None or raw == "":
        return None, None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        return None, error_response(
            f"Invalid ISO-8601 datetime: {raw!r}",
            "validation_error",
            status=422,
            details={"field": field, "value": raw},
        )
    return value, None


def _parse_activity_limit(raw):
    limit, err = _parse_limit(
        raw,
        default=DEFAULT_ACTIVITY_LIMIT,
        field="limit",
    )
    if err is not None:
        return None, err
    return min(limit, MAX_ACTIVITY_LIMIT), None


def _parse_activity_category(raw):
    value = (raw or ACTIVITY_CATEGORY_ALL).strip().lower()
    if is_valid_activity_category(value):
        return normalize_activity_category(value), None
    return None, error_response(
        f"Invalid activity category: {raw!r}",
        "validation_error",
        status=422,
        details={
            "field": "category",
            "value": raw,
            "allowed": [ACTIVITY_CATEGORY_ALL, *ACTIVITY_CATEGORIES],
        },
    )


def _find_user(email):
    """Look up a ``User`` by case-insensitive email."""
    if not email:
        return None
    return User.objects.select_related("tier").filter(email__iexact=email).first()


def _user_not_found_response():
    return error_response(
        "User not found",
        "user_not_found",
        status=404,
    )


def resolve_crm_persona(record):
    """Resolve a ``CRMRecord``'s persona label.

    Prefers the structured ``persona_ref.display_label`` when set,
    otherwise falls back to the free-text ``persona`` field. Shared by
    the summary (this module) and the full CRM-export serializer (issue
    #1079) so both callers agree on persona resolution.
    """
    if record.persona_ref_id is not None and record.persona_ref is not None:
        return record.persona_ref.display_label
    return (record.persona or '').strip()


def serialize_crm_record_summary(record):
    """Compact CRM-record dict (``id`` / ``status`` / ``persona``).

    ``record`` may be ``None`` (no ``CRMRecord``), in which case ``None``
    is returned. Kept record-based (not user-based) so the export path
    can serialize a pre-fetched record without re-querying.
    """
    if record is None:
        return None
    return {
        "id": record.pk,
        "status": record.status,
        "persona": resolve_crm_persona(record),
    }


def serialize_crm_record_for_operator(record):
    """CRM record payload with the Studio URL operators need next."""
    if record is None:
        return None
    path = reverse("studio_crm_detail", kwargs={"crm_id": record.pk})
    return {
        **serialize_crm_record_summary(record),
        "studio_url": path,
        "onboarding_url": f"{path}#onboarding",
    }


def _serialize_crm_record_summary(user):
    """Look up a user's ``CRMRecord`` and serialize the compact summary."""
    record = CRMRecord.objects.select_related('persona_ref').filter(
        user=user,
    ).first()
    return serialize_crm_record_summary(record)


def _actor_label(request):
    """Return a short label identifying the API caller.

    Prefers the operator-assigned token ``name``; falls back to
    ``key_prefix`` (the masked 8-char form Studio shows) when the token
    has no name. The label lands inside the ``details`` text of the
    audit-log row so operators can trace "who unsubscribed X" without
    needing a separate actor column.
    """
    token = getattr(request, "auth_token", None)
    if token is None:
        return "unknown"
    if token.name:
        return token.name
    return token.key_prefix


def _audit(user, action, details):
    """Insert one ``CommunityAuditLog`` row for the SUBJECT user.

    The audit-log convention is:

    - ``user`` = the SUBJECT (the user being mutated)
    - ``action`` = one of ``api_unsubscribe`` / ``api_verify`` / ``api_tag``
    - ``details`` = free-form text including ``actor_token=<label>``

    The actor identity rides in ``details`` rather than on a second FK
    so we can reuse the existing audit table (vs. adding a new column
    that every caller would have to know about).
    """
    CommunityAuditLog.objects.create(
        user=user,
        action=action,
        details=details,
    )


# ---- Constants for filters --------------------------------------------------

# Mirrors the ``EVENT_TYPE_CHOICES`` keys defined on ``SesEvent`` so the
# 422 ``allowed`` list stays accurate when new event kinds are added at
# the model layer. The model's choices are the source of truth.
VALID_SES_EVENT_TYPES = tuple(
    choice for choice, _label in SesEvent.EVENT_TYPE_CHOICES
)


# Allowed PATCH field set for the single-user write endpoint. Any other
# key in the body returns a 422 ``unknown_field``.
_PATCH_ALLOWED_FIELDS = {"unsubscribed", "email_verified"}


# ---- Read endpoints --------------------------------------------------------

_USER_EXAMPLE = {
    "email": "alice@example.com",
    "first_name": "Alice",
    "last_name": "Doe",
    "display_name": "Alice Doe",
    # ``tier`` is the EFFECTIVE tier (override applied), with ``source``
    # provenance (issue #965). This member pays for nothing (base Free) but
    # has an active Main override, so the effective tier is Main / "override".
    "tier": {"slug": "main", "level": 20, "source": "override"},
    "tier_override_active": True,
    # ``base_tier`` is the actually-paid tier — the old meaning of ``tier``.
    "base_tier": {"slug": "free", "level": 0},
    "tier_override": {
        "tier_slug": "main",
        "level": 20,
        "expires_at": "2036-05-06T00:00:00+00:00",
        "granted_by": "ops@aishippinglabs.com",
    },
    "unsubscribed": False,
    "soft_bounce_count": 0,
    "bounce_state": "none",
    "email_verified": True,
    "tags": ["sprint:may-2026"],
    "aliases": ["47-gentle.virtual@icloud.com"],
    "slack_member": True,
    "slack_user_id": "U01ABCDEF",
    "stripe_customer_id": "cus_xyz",
    "subscription_id": "sub_xyz",
    "date_joined": "2026-04-15T12:00:00+00:00",
    "last_login": "2026-05-19T08:30:00+00:00",
}


@token_required
@csrf_exempt
@require_methods("GET")
@openapi_spec(
    tag="Users",
    summary="List users (search/filter)",
    methods={
        "GET": {
            "summary": "List users",
            "description": (
                "Newest-first list of users with optional ``q`` search. "
                "Matches the Studio user-search surface: email, first/last "
                "name, ``stripe_customer_id``, ``slack_user_id``, and "
                "substring matches inside tags. Empty ``q`` returns the "
                "newest 50."
            ),
            "query": {
                "q": {
                    "type": "string",
                    "required": False,
                    "description": (
                        "Substring search across email/name/Stripe id/"
                        "Slack id/tags."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "required": False,
                    "description": "Default 50; clamped to 200.",
                },
                "since": {
                    "type": "string",
                    "required": False,
                    "description": (
                        "ISO-8601 datetime; only users joined at or after "
                        "this are returned."
                    ),
                },
            },
            "responses": {
                200: {
                    "description": "User list page.",
                    "example": {
                        "users": [
                            {k: v for k, v in _USER_EXAMPLE.items()
                             if k not in ("tags", "tier_override", "base_tier")},
                        ],
                        "count": 1,
                        "limit": 50,
                    },
                },
                422: {
                    "description": "Invalid ``limit`` or ``since``.",
                    "example": {
                        "error": "Invalid integer: 'abc'",
                        "code": "validation_error",
                        "details": {"field": "limit", "value": "abc"},
                    },
                },
            },
        },
    },
)
def users_collection(request):
    """``GET /api/users`` -- search / list."""
    limit, err = _parse_limit(request.GET.get("limit"))
    if err is not None:
        return err
    since, err = _parse_since(request.GET.get("since"))
    if err is not None:
        return err

    q = (request.GET.get("q") or "").strip()

    qs = User.objects.select_related("tier")

    if since is not None:
        qs = qs.filter(date_joined__gte=since)

    if q:
        scalar = (
            Q(email__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(stripe_customer_id__icontains=q)
            | Q(slack_user_id__icontains=q)
        )
        # Tag-substring match: ``User.tags`` is a JSONField list so we
        # filter in Python to stay portable across SQLite (tests) and
        # Postgres (prod). Mirrors the Studio listing's behaviour.
        normalized = normalize_tag(q)
        tag_user_ids = []
        if normalized:
            for user_id, tags in User.objects.values_list("pk", "tags").iterator():
                if not isinstance(tags, list):
                    continue
                for tag in tags:
                    if isinstance(tag, str) and normalized in tag.lower():
                        tag_user_ids.append(user_id)
                        break
        qs = qs.filter(scalar | Q(pk__in=tag_user_ids))

    qs = qs.order_by("-date_joined")[:limit]
    rows = [serialize_user_state(u, compact=True) for u in qs]
    return JsonResponse(
        {"users": rows, "count": len(rows), "limit": limit},
        status=200,
    )


@token_required
@csrf_exempt
@require_methods("GET", "PATCH")
@openapi_spec(
    tag="Users",
    summary="Get or update a single user",
    description=(
        "Read or update a single user's state. The PATCH surface is "
        "deliberately narrow: only ``unsubscribed`` and "
        "``email_verified`` are writable; tier / email rename / password "
        "are Studio-only by design."
    ),
    methods={
        "GET": {
            "summary": "Get user state",
            "responses": {
                200: {
                    "description": "User payload.",
                    "example": _USER_EXAMPLE,
                },
                404: {
                    "description": "Unknown email.",
                    "example": {
                        "error": "User not found",
                        "code": "user_not_found",
                    },
                },
            },
        },
        "PATCH": {
            "summary": "Update user state",
            "description": (
                "Accepts ``unsubscribed: bool`` and/or "
                "``email_verified: true`` only. Setting "
                "``email_verified: false`` returns 422 "
                "``verification_demote_forbidden``. Any other field "
                "returns 422 ``unknown_field``. Every PATCH appends a "
                "``CommunityAuditLog`` row (even no-ops)."
            ),
            "request_body": {
                "properties": {
                    "unsubscribed": {"type": "boolean"},
                    "email_verified": {
                        "type": "boolean",
                        "description": (
                            "Only ``true`` is accepted; demote-via-API "
                            "is forbidden."
                        ),
                    },
                },
                "example": {"unsubscribed": True},
            },
            "responses": {
                200: {
                    "description": "Updated user payload.",
                    "example": _USER_EXAMPLE,
                },
                404: {
                    "description": "Unknown email.",
                    "example": {
                        "error": "User not found",
                        "code": "user_not_found",
                    },
                },
                422: {
                    "description": "Body validation failed.",
                    "example": {
                        "error": "Unknown field: tier",
                        "code": "unknown_field",
                        "details": {"field": "tier"},
                    },
                },
            },
        },
    },
)
def user_detail(request, email):
    """``GET | PATCH /api/users/<email>``."""
    user = _find_user(email)
    if user is None:
        return _user_not_found_response()

    if request.method == "GET":
        return JsonResponse(serialize_user_state(user), status=200)

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

    # Reject unknown fields up front so callers immediately see the
    # right error rather than a silent no-op.
    for key in data:
        if key not in _PATCH_ALLOWED_FIELDS:
            return error_response(
                f"Unknown field: {key}",
                "unknown_field",
                status=422,
                details={"field": key},
            )

    # Demote-via-API guard: explicit false is forbidden so an operator
    # cannot accidentally erase verification state from a script.
    if "email_verified" in data and data["email_verified"] is not True:
        return error_response(
            "email_verified cannot be set to false via API",
            "verification_demote_forbidden",
            status=422,
            details={"field": "email_verified"},
        )

    actor = _actor_label(request)

    with transaction.atomic():
        # Re-fetch with row-level lock so the audit row reflects the
        # actual transition we wrote (not a stale read).
        locked = User.objects.select_for_update().get(pk=user.pk)

        if "unsubscribed" in data:
            new_value = bool(data["unsubscribed"])
            previous = bool(locked.unsubscribed)
            if previous != new_value:
                locked.unsubscribed = new_value
                locked.save(update_fields=["unsubscribed"])
                details = (
                    f"set unsubscribed={new_value}; "
                    f"actor_token={actor}; previous={previous}"
                )
            else:
                # No-op write -- the operator's attempted action is still
                # recorded so "who tried to unsubscribe X" answers cleanly
                # even when X was already unsubscribed.
                details = (
                    f"no-op unsubscribed={new_value}; "
                    f"actor_token={actor}; previous={previous}"
                )
            _audit(locked, "api_unsubscribe", details)

        if "email_verified" in data:
            previous = bool(locked.email_verified)
            update_fields = []
            if not previous:
                locked.email_verified = True
                update_fields.append("email_verified")
            # Clear the TTL so the purge task cannot reclaim a manually
            # verified row -- even when the verified flag was already
            # set, an explicit operator action should release the hold.
            if locked.verification_expires_at is not None:
                locked.verification_expires_at = None
                update_fields.append("verification_expires_at")
            if update_fields:
                locked.save(update_fields=update_fields)
                details = (
                    f"verified via API; actor_token={actor}; "
                    f"previous_verified={previous}"
                )
            else:
                details = (
                    f"no-op verified; actor_token={actor}; "
                    f"previous_verified={previous}"
                )
            _audit(locked, "api_verify", details)

        user = locked

    return JsonResponse(serialize_user_state(user), status=200)


@token_required
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Users",
    summary="Create or reuse a user's CRM record from submitted onboarding",
    methods={
        "POST": {
            "summary": "Ensure CRM record",
            "description": (
                "Staff-token repair/automation endpoint. Resolves the path "
                "email through primary email or email aliases, refuses users "
                "without submitted onboarding, then creates or reuses the "
                "single ``CRMRecord`` for the canonical user. This is meant "
                "for pre-existing onboarding submissions and account-merge "
                "repairs, not arbitrary CRM imports."
            ),
            "responses": {
                200: {
                    "description": "Existing CRM record reused.",
                    "example": {
                        "email": "alice@example.com",
                        "created": False,
                        "crm_record": {
                            "id": 42,
                            "status": "active",
                            "persona": "",
                            "studio_url": "/studio/crm/42/",
                            "onboarding_url": "/studio/crm/42/#onboarding",
                        },
                    },
                },
                201: {
                    "description": "CRM record created.",
                    "example": {
                        "email": "alice@example.com",
                        "created": True,
                        "crm_record": {
                            "id": 42,
                            "status": "active",
                            "persona": "",
                            "studio_url": "/studio/crm/42/",
                            "onboarding_url": "/studio/crm/42/#onboarding",
                        },
                    },
                },
                404: {
                    "description": "Unknown primary email or alias.",
                    "example": {
                        "error": "User not found",
                        "code": "user_not_found",
                    },
                },
                409: {
                    "description": "No submitted onboarding exists.",
                    "example": {
                        "error": "Submitted onboarding is required",
                        "code": "onboarding_not_submitted",
                    },
                },
            },
        },
    },
)
def user_crm_record(request, email):
    """``POST /api/users/<email>/crm-record``."""
    user = resolve_user_by_email(email)
    if user is None:
        return _user_not_found_response()
    onboarding_response = get_onboarding_response(user)
    if onboarding_response is None or onboarding_response.status != "submitted":
        return error_response(
            "Submitted onboarding is required",
            "onboarding_not_submitted",
            status=409,
            details={"email": user.email},
        )

    actor = getattr(request, "user", None)
    with transaction.atomic():
        locked = User.objects.select_for_update().get(pk=user.pk)
        record, created = CRMRecord.objects.get_or_create(
            user=locked,
            defaults={
                "status": "active",
                "created_by": actor if getattr(actor, "is_staff", False) else None,
            },
        )
        CommunityAuditLog.objects.create(
            user=locked,
            action="api_crm_record",
            details=(
                f"{'created' if created else 'reused'} CRM record "
                f"{record.pk}; actor_token={_actor_label(request)}; "
                f"source=submitted_onboarding"
            ),
        )

    return JsonResponse(
        {
            "email": user.email,
            "created": created,
            "crm_record": serialize_crm_record_for_operator(record),
        },
        status=201 if created else 200,
    )


_ACTIVITY_EXAMPLE = {
    "id": 123,
    "event_type": "event_register",
    "type_label": "Registered",
    "category": "events",
    "label": "Registered for event: Agents workshop",
    "occurred_at": "2026-05-19T08:30:00+00:00",
    "object_type": "event",
    "object_id": "agents-workshop",
    "target_url": "/events/agents-workshop",
    "is_upgrade_marker": False,
}


@token_required
@csrf_exempt
@require_methods("GET")
@openapi_spec(
    tag="Users",
    summary="List recent activity for a user",
    methods={
        "GET": {
            "summary": "List user activity context",
            "description": (
                "Staff-only read endpoint for CRM/operator automation. "
                "Returns curated ``analytics.UserActivity`` rows using the "
                "same safe public/member-facing link policy as Studio CRM. "
                "No write endpoint exists for arbitrary activity rows."
            ),
            "query": {
                "limit": {
                    "type": "integer",
                    "required": False,
                    "description": "Default 30; clamped to 100.",
                },
                "category": {
                    "type": "string",
                    "required": False,
                    "description": (
                        "One of all, learning, events, content, account, "
                        "comms. Default all."
                    ),
                },
                "since": {
                    "type": "string",
                    "required": False,
                    "description": "ISO-8601 datetime filter on occurred_at.",
                },
            },
            "responses": {
                200: {
                    "description": "Recent activity context.",
                    "example": {
                        "user": {
                            "email": "alice@example.com",
                            "display_name": "Alice Doe",
                            "tier": {
                                "slug": "main",
                                "level": 20,
                                "source": "subscription",
                            },
                        },
                        "crm_record": {
                            "id": 42,
                            "status": "active",
                            "persona": "Sam - Technical Professional",
                        },
                        "total_count": 12,
                        "limit": 5,
                        "has_more": True,
                        "category_counts": {
                            "all": 12,
                            "learning": 4,
                            "events": 3,
                            "content": 2,
                            "account": 1,
                            "comms": 2,
                        },
                        "activities": [_ACTIVITY_EXAMPLE],
                    },
                },
                404: {
                    "description": "Unknown email.",
                    "example": {
                        "error": "User not found",
                        "code": "user_not_found",
                    },
                },
                422: {
                    "description": "Invalid limit, category, or since.",
                    "example": {
                        "error": "Invalid activity category: 'unknown'",
                        "code": "validation_error",
                        "details": {
                            "field": "category",
                            "value": "unknown",
                            "allowed": [
                                "all",
                                "learning",
                                "events",
                                "content",
                                "account",
                                "comms",
                            ],
                        },
                    },
                },
            },
        },
    },
)
def user_activity(request, email):
    """``GET /api/users/<email>/activity``."""
    user = _find_user(email)
    if user is None:
        return _user_not_found_response()

    limit, err = _parse_activity_limit(request.GET.get("limit"))
    if err is not None:
        return err
    since, err = _parse_since(request.GET.get("since"))
    if err is not None:
        return err
    category, err = _parse_activity_category(request.GET.get("category"))
    if err is not None:
        return err

    context = build_activity_context(
        user,
        limit=limit,
        category=category,
        since=since,
        include_category_counts=True,
    )
    return JsonResponse(
        {
            "user": serialize_user_state(user, compact=True),
            "crm_record": _serialize_crm_record_summary(user),
            "total_count": context["activity_total"],
            "limit": context["activity_limit"],
            "has_more": context["activity_has_more"],
            "category_counts": context["activity_category_counts"],
            "activities": [
                serialize_activity_for_api(activity)
                for activity in context["activities"]
            ],
        },
        status=200,
    )


_SES_EVENT_EXAMPLE = {
    "message_id": "11111111-2222-3333-4444-555555555555",
    "event_type": "bounce_permanent",
    "received_at": "2026-05-19T08:30:00+00:00",
    "recipient_email": "alice@example.com",
    "bounce_type": "Permanent",
    "bounce_subtype": "General",
    "diagnostic_code": "smtp; 550 user unknown",
    "action_taken": "unsubscribed and tagged bounced",
    "email_log_id": 42,
}


@token_required
@csrf_exempt
@require_methods("GET")
@openapi_spec(
    tag="Users",
    summary="List SES events for a user",
    methods={
        "GET": {
            "summary": "List inbound SES events",
            "description": (
                "Newest-first list of ``SesEvent`` rows for the user. "
                "Filters on ``SesEvent.user_id`` (NOT ``recipient_email``) "
                "so the history survives email renames. ``raw_payload`` is "
                "deliberately excluded -- the Studio surface owns the "
                "deep-dive."
            ),
            "query": {
                "limit": {
                    "type": "integer",
                    "required": False,
                    "description": "Default 50; clamped to 200.",
                },
                "since": {
                    "type": "string",
                    "required": False,
                    "description": "ISO-8601 datetime filter on ``received_at``.",
                },
                "type": {
                    "type": "string",
                    "required": False,
                    "description": "Filter on ``event_type``.",
                },
            },
            "responses": {
                200: {
                    "description": "SES events page.",
                    "example": {
                        "ses_events": [_SES_EVENT_EXAMPLE],
                        "count": 1,
                        "limit": 50,
                    },
                },
                404: {
                    "description": "Unknown email.",
                    "example": {
                        "error": "User not found",
                        "code": "user_not_found",
                    },
                },
                422: {
                    "description": "Invalid filter values.",
                    "example": {
                        "error": "Invalid event type: 'invalid'",
                        "code": "validation_error",
                        "details": {
                            "field": "type",
                            "value": "invalid",
                            "allowed": list(VALID_SES_EVENT_TYPES),
                        },
                    },
                },
            },
        },
    },
)
def user_ses_events(request, email):
    """``GET /api/users/<email>/ses-events``."""
    user = _find_user(email)
    if user is None:
        return _user_not_found_response()

    limit, err = _parse_limit(request.GET.get("limit"))
    if err is not None:
        return err
    since, err = _parse_since(request.GET.get("since"))
    if err is not None:
        return err

    type_filter = request.GET.get("type") or ""
    if type_filter and type_filter not in VALID_SES_EVENT_TYPES:
        return error_response(
            f"Invalid event type: {type_filter!r}",
            "validation_error",
            status=422,
            details={
                "field": "type",
                "value": type_filter,
                "allowed": list(VALID_SES_EVENT_TYPES),
            },
        )

    qs = SesEvent.objects.filter(user=user)
    if since is not None:
        qs = qs.filter(received_at__gte=since)
    if type_filter:
        qs = qs.filter(event_type=type_filter)
    qs = qs.order_by("-received_at")[:limit]

    rows = [serialize_ses_event(e) for e in qs]
    return JsonResponse(
        {"ses_events": rows, "count": len(rows), "limit": limit},
        status=200,
    )


_EMAIL_LOG_EXAMPLE = {
    "id": 42,
    "email_type": "campaign",
    "sent_at": "2026-05-19T08:30:00+00:00",
    "ses_message_id": "0103018f-aaaa-bbbb-cccc-000000000000",
    "opened_at": "2026-05-19T09:00:00+00:00",
    "opens": 3,
    "clicked_at": None,
    "clicks": 0,
    "bounced_at": None,
    "bounce_type": "",
    "bounce_subtype": "",
    "complained_at": None,
    "campaign_id": 7,
    "disposition": "opened",
}


@token_required
@csrf_exempt
@require_methods("GET")
@openapi_spec(
    tag="Users",
    summary="List email-log rows for a user",
    methods={
        "GET": {
            "summary": "List outbound email-log rows",
            "description": (
                "Newest-first list of ``EmailLog`` rows for the user. "
                "Each row carries the raw timing fields plus a derived "
                "``disposition`` reflecting the strongest signal in the "
                "order ``sent < delivered < opened < clicked < bounced < "
                "complained``. ``kind`` filters on ``email_type`` exact "
                "match -- unknown values are valid (they simply return "
                "an empty page) since ``email_type`` is not a closed "
                "enum at the model layer."
            ),
            "query": {
                "limit": {
                    "type": "integer",
                    "required": False,
                    "description": "Default 50; clamped to 200.",
                },
                "since": {
                    "type": "string",
                    "required": False,
                    "description": "ISO-8601 datetime filter on ``sent_at``.",
                },
                "kind": {
                    "type": "string",
                    "required": False,
                    "description": (
                        "Exact ``email_type`` filter (e.g. ``campaign``, "
                        "``welcome``, ``verification``)."
                    ),
                },
            },
            "responses": {
                200: {
                    "description": "Email logs page.",
                    "example": {
                        "email_logs": [_EMAIL_LOG_EXAMPLE],
                        "count": 1,
                        "limit": 50,
                    },
                },
                404: {
                    "description": "Unknown email.",
                    "example": {
                        "error": "User not found",
                        "code": "user_not_found",
                    },
                },
                422: {
                    "description": "Invalid ``limit`` or ``since``.",
                },
            },
        },
    },
)
def user_email_log(request, email):
    """``GET /api/users/<email>/email-log``."""
    user = _find_user(email)
    if user is None:
        return _user_not_found_response()

    limit, err = _parse_limit(request.GET.get("limit"))
    if err is not None:
        return err
    since, err = _parse_since(request.GET.get("since"))
    if err is not None:
        return err

    qs = EmailLog.objects.filter(user=user)
    if since is not None:
        qs = qs.filter(sent_at__gte=since)
    kind_filter = request.GET.get("kind") or ""
    if kind_filter:
        qs = qs.filter(email_type=kind_filter)
    qs = qs.order_by("-sent_at")[:limit]

    rows = [serialize_email_log(log) for log in qs]
    return JsonResponse(
        {"email_logs": rows, "count": len(rows), "limit": limit},
        status=200,
    )


# ---- Tag writes ------------------------------------------------------------


@token_required
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Users",
    summary="Add a tag to a user (idempotent)",
    methods={
        "POST": {
            "summary": "Add a single tag",
            "description": (
                "Idempotent -- adding an existing tag is a no-op but "
                "still records an audit row. The input is normalized via "
                "``accounts.utils.tags.normalize_tag``; empty input after "
                "normalization returns 422 ``invalid_tag``."
            ),
            "request_body": {
                "required": ["tag"],
                "properties": {
                    "tag": {"type": "string"},
                },
                "example": {"tag": "early-adopter"},
            },
            "responses": {
                200: {
                    "description": "Current tag list including the new tag.",
                    "example": {
                        "email": "alice@example.com",
                        "tags": ["sprint:may-2026", "early-adopter"],
                    },
                },
                404: {
                    "description": "Unknown email.",
                    "example": {
                        "error": "User not found",
                        "code": "user_not_found",
                    },
                },
                422: {
                    "description": "Empty or invalid tag input.",
                    "example": {
                        "error": "Tag must be a non-empty slug",
                        "code": "invalid_tag",
                    },
                },
            },
        },
    },
)
def user_tags_add(request, email):
    """``POST /api/users/<email>/tags`` -- add a single tag (idempotent)."""
    user = _find_user(email)
    if user is None:
        return _user_not_found_response()

    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return error_response(
            "Body must be a JSON object",
            "invalid_type",
            details={"field": "body", "expected": "object"},
        )

    raw_tag = data.get("tag")
    if not isinstance(raw_tag, str):
        return error_response(
            "Tag must be a non-empty slug",
            "invalid_tag",
            status=422,
        )
    normalized = normalize_tag(raw_tag)
    if not normalized:
        return error_response(
            "Tag must be a non-empty slug",
            "invalid_tag",
            status=422,
        )

    actor = _actor_label(request)
    with transaction.atomic():
        locked = User.objects.select_for_update().get(pk=user.pk)
        had_tag = normalized in list(locked.tags or [])
        add_tag(locked, normalized)
        verb = "added"
        if had_tag:
            verb = "no-op add"
        _audit(
            locked,
            "api_tag",
            f"{verb} tag {normalized!r}; actor_token={actor}",
        )
        # Refetch so we report the canonical list ordering after the
        # write. ``add_tag`` already saved with update_fields=['tags'].
        locked.refresh_from_db(fields=["tags"])
        user = locked

    return JsonResponse(
        {"email": user.email, "tags": list(user.tags or [])},
        status=200,
    )


# ---- Mark-bounced write -----------------------------------------------------

# Body keys accepted by the mark-bounced endpoint. Any other key returns
# 422 ``unknown_field`` (mirrors PATCH's strict-keys behaviour).
_MARK_BOUNCED_ALLOWED_FIELDS = {"bounce_type", "diagnostic", "reason"}

# Allowed ``bounce_type`` values. Order matters: matches the spec's
# ``["permanent", "soft"]`` so 422 ``details.allowed`` is stable.
_MARK_BOUNCED_TYPES = ["permanent", "soft"]


@token_required
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Users",
    summary="Manually mark a user as bounced",
    methods={
        "POST": {
            "summary": "Mark a user as bounced (mirrors the SES webhook)",
            "description": (
                "Operator-triggered bounce mark for the rare cases where "
                "the SES -> SNS webhook is unavailable (e.g. SNS HTTPS "
                "subscription not yet wired) or where staff need to "
                "reproduce post-bounce state at will. Side-effects are "
                "shared with the webhook via "
                "``accounts.utils.bounce`` so the resulting state is "
                "indistinguishable from a real bounce. Idempotent: "
                "calling again with the same ``bounce_type`` on a user "
                "already in that state returns 200, writes no new "
                "``SesEvent`` row, but still records an audit row "
                "annotated ``no-op``."
            ),
            "request_body": {
                "required": ["bounce_type"],
                "properties": {
                    "bounce_type": {
                        "type": "string",
                        "enum": _MARK_BOUNCED_TYPES,
                    },
                    "diagnostic": {
                        "type": "string",
                        "description": (
                            "Optional SMTP diagnostic to persist on "
                            "``User.last_bounce_diagnostic``."
                        ),
                    },
                    "reason": {
                        "type": "string",
                        "description": (
                            "Optional free-form operator note recorded in "
                            "the audit log."
                        ),
                    },
                },
                "example": {
                    "bounce_type": "permanent",
                    "diagnostic": "smtp; 550 5.1.1 user unknown",
                    "reason": "real bounce arrived; SNS not wired yet",
                },
            },
            "responses": {
                200: {
                    "description": (
                        "User payload after the (possibly no-op) mark."
                    ),
                    "example": _USER_EXAMPLE,
                },
                404: {
                    "description": "Unknown email.",
                    "example": {
                        "error": "User not found",
                        "code": "user_not_found",
                    },
                },
                422: {
                    "description": "Body validation failed.",
                    "example": {
                        "error": "Invalid bounce_type",
                        "code": "validation_error",
                        "details": {
                            "field": "bounce_type",
                            "value": "hard",
                            "allowed": _MARK_BOUNCED_TYPES,
                        },
                    },
                },
            },
        },
    },
)
def user_mark_bounced(request, email):
    """``POST /api/users/<email>/mark-bounced`` -- operator mark-bounced."""
    user = _find_user(email)
    if user is None:
        return _user_not_found_response()

    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return error_response(
            "Body must be a JSON object",
            "invalid_type",
            details={"field": "body", "expected": "object"},
        )

    # Strict-keys: unknown body fields return 422 ``unknown_field`` so
    # callers immediately see the typo rather than silently dropping it.
    for key in data:
        if key not in _MARK_BOUNCED_ALLOWED_FIELDS:
            return error_response(
                f"Unknown field: {key}",
                "unknown_field",
                status=422,
                details={"field": key},
            )

    bounce_type = data.get("bounce_type")
    if bounce_type is None:
        return error_response(
            "bounce_type is required",
            "validation_error",
            status=422,
            details={
                "field": "bounce_type",
                "allowed": list(_MARK_BOUNCED_TYPES),
            },
        )
    if bounce_type not in _MARK_BOUNCED_TYPES:
        return error_response(
            f"Invalid bounce_type: {bounce_type!r}",
            "validation_error",
            status=422,
            details={
                "field": "bounce_type",
                "value": bounce_type,
                "allowed": list(_MARK_BOUNCED_TYPES),
            },
        )

    raw_diagnostic = data.get("diagnostic")
    if raw_diagnostic is None or raw_diagnostic == "":
        # Default diagnostic mirrors what an operator would write by
        # hand. The helper trims to ``MAX_BOUNCE_DIAGNOSTIC_LEN``.
        diagnostic = "manual operator mark via API"
    else:
        diagnostic = str(raw_diagnostic)

    reason = data.get("reason")
    if reason is not None:
        reason = str(reason)

    actor = _actor_label(request)
    body_bounce_type = bounce_type  # preserved for audit / raw_payload

    target_state = (
        User.BounceState.PERMANENT
        if bounce_type == "permanent"
        else User.BounceState.SOFT
    )
    synthetic_event_type = (
        SesEvent.EVENT_TYPE_BOUNCE_PERMANENT
        if bounce_type == "permanent"
        else SesEvent.EVENT_TYPE_BOUNCE_TRANSIENT
    )
    synthetic_bounce_type_field = (
        "Permanent" if bounce_type == "permanent" else "Transient"
    )

    with transaction.atomic():
        locked = User.objects.select_for_update().get(pk=user.pk)
        previous_state = locked.bounce_state

        # Idempotency check: if the user is already in the requested
        # state, skip helper + SesEvent insert. We still write an audit
        # row so the operator's attempt is captured.
        already_at_state = previous_state == target_state
        if already_at_state:
            details = (
                f"no-op: already {previous_state}; "
                f"mark bounce_type={body_bounce_type!r}; "
                f"actor_token={actor}; "
                f"current_state={previous_state!r}"
            )
            if reason is not None:
                details = f"{details}; reason={reason!r}"
            _audit(locked, "api_mark_bounced", details)
            user = locked
        else:
            if bounce_type == "permanent":
                mark_permanent_bounce(locked, diagnostic=diagnostic)
            else:
                record_soft_bounce(locked, diagnostic=diagnostic)

            # Synthetic SES event row. ``message_id`` uses millisecond
            # precision so back-to-back legitimate transitions on the
            # same user don't collide on the unique-message_id index.
            ms = int(timezone.now().timestamp() * 1000)
            message_id = f"manual-mark-bounced-{locked.id}-{ms}"
            action_taken = (
                f"manual mark via API; actor_token={actor}"
            )
            if reason is not None:
                action_taken = (
                    f"{action_taken}; reason={reason!r}"
                )
            action_taken = action_taken[:255]
            raw_payload = {
                "source": "api_mark_bounced",
                "actor_token": actor,
                "bounce_type": body_bounce_type,
                "diagnostic": diagnostic,
                "reason": reason,
                "ran_at": timezone.now().isoformat(),
            }
            SesEvent.objects.create(
                message_id=message_id,
                event_type=synthetic_event_type,
                raw_payload=raw_payload,
                recipient_email=locked.email,
                user=locked,
                action_taken=action_taken,
                email_log=None,
                bounce_type=synthetic_bounce_type_field,
                bounce_subtype="",
                diagnostic_code=diagnostic,
            )

            details = (
                f"marked bounce_type={body_bounce_type!r}; "
                f"actor_token={actor}; "
                f"previous_state={previous_state!r}; "
                f"diagnostic={diagnostic!r}"
            )
            if reason is not None:
                details = f"{details}; reason={reason!r}"
            _audit(locked, "api_mark_bounced", details)
            user = locked

    return JsonResponse(serialize_user_state(user), status=200)


@token_required
@csrf_exempt
@require_methods("DELETE")
@openapi_spec(
    tag="Users",
    summary="Remove a tag from a user (idempotent)",
    methods={
        "DELETE": {
            "summary": "Remove a single tag",
            "description": (
                "Idempotent -- removing a tag the user does not carry "
                "still returns 200 and writes an audit row. The ``<tag>`` "
                "path segment is re-normalized defensively."
            ),
            "responses": {
                200: {
                    "description": "Current tag list with the tag removed.",
                    "example": {
                        "email": "alice@example.com",
                        "tags": ["sprint:may-2026"],
                    },
                },
                404: {
                    "description": "Unknown email.",
                    "example": {
                        "error": "User not found",
                        "code": "user_not_found",
                    },
                },
                422: {
                    "description": "Empty or invalid tag input.",
                    "example": {
                        "error": "Tag must be a non-empty slug",
                        "code": "invalid_tag",
                    },
                },
            },
        },
    },
)
def user_tags_remove(request, email, tag):
    """``DELETE /api/users/<email>/tags/<tag>`` -- remove one tag (idempotent)."""
    user = _find_user(email)
    if user is None:
        return _user_not_found_response()

    normalized = normalize_tag(tag)
    if not normalized:
        return error_response(
            "Tag must be a non-empty slug",
            "invalid_tag",
            status=422,
        )

    actor = _actor_label(request)
    with transaction.atomic():
        locked = User.objects.select_for_update().get(pk=user.pk)
        had_tag = normalized in list(locked.tags or [])
        remove_tag(locked, normalized)
        verb = "removed"
        if not had_tag:
            verb = "no-op remove"
        _audit(
            locked,
            "api_tag",
            f"{verb} tag {normalized!r}; actor_token={actor}",
        )
        locked.refresh_from_db(fields=["tags"])
        user = locked

    return JsonResponse(
        {"email": user.email, "tags": list(user.tags or [])},
        status=200,
    )
