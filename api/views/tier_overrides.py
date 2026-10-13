"""Tier-override list, grant and revoke API endpoints (issue #833).

``GET /api/tier-overrides`` (issue #1936) lists active overrides with a
``raises_access`` flag computed by ``content.access.annotate_override_raises_access``
(the same definition as the Studio "Not raising access" filter).

``POST /api/tier-overrides/revoke`` deactivates active overrides through
``payments.services.tier_override_revoke``, the same service the Studio revoke
button uses. ``ineffective_only`` narrows it to grants that raise nothing.

``POST /api/tier-overrides`` grants the same long-lived ``TierOverride`` the
Studio bulk contact-import gives non-paying members: a 10-year ``main`` override
that deactivates any existing active override and records who granted it. Works
for a single member (a one-element ``emails`` list) or a whole cohort batch.

Why a dedicated endpoint instead of a flag on ``POST /api/contacts/import``:
the contacts-import endpoint's OpenAPI spec publicly promises overrides are NOT
available there (a deliberate footgun-avoidance decision). A separate,
explicitly-named override endpoint makes the privileged action discoverable and
impossible to trigger by accident from the contacts surface.

The 10-year override semantics are NOT duplicated here -- the grant reuses
``studio.services.contacts_import.import_contact_rows(...,
tier_assignment_mode="override")``, which routes through ``_apply_tier_override``.
This module adds the parts the shared importer does not provide:

- Idempotency (approach (a) from the issue): we pre-filter emails that already
  carry an identical active override (same ``override_tier``, ``expires_at`` in
  the future) and report them ``skipped_idempotent`` WITHOUT passing them to the
  importer, so no fresh ``TierOverride`` history row is stacked. This keeps
  ``studio/services/contacts_import.py`` completely untouched.
- A per-email ``results`` array with override-focused statuses
  (``granted`` / ``skipped_idempotent`` / ``malformed``).
- One ``CommunityAuditLog`` row per ``granted`` email attributing the grant to
  the calling token (mirrors the User Management API audit convention).
"""

import datetime

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Count, F, Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from accounts.services.email_resolution import resolve_user_by_email
from api.openapi import openapi_spec
from api.request_parsing import parse_limit
from api.safety import error_response
from api.utils import parse_json_body, require_methods
from community.models import CommunityAuditLog
from content.access import annotate_override_raises_access
from payments.models import Tier, TierOverride
from payments.services.tier_override_revoke import (
    active_overrides_for,
    revoke_active_overrides,
)
from studio.services.contacts_import import import_contact_rows

User = get_user_model()


# Batch cap. Matches the import's practical batch size; an over-cap request
# grants NOTHING and returns ``400 batch_too_large``.
BATCH_CAP = 1000

# Default override tier slug when the caller omits ``tier``. The motivating use
# is the ``main`` cohort grant.
DEFAULT_TIER_SLUG = "main"


def _actor_label(request):
    """Return a short label identifying the API caller.

    Mirrors ``api/views/users.py::_actor_label``: prefers the operator-assigned
    token ``name``; falls back to ``key_prefix`` (the masked 8-char form Studio
    shows) when the token has no name. The label lands inside the ``details``
    text of the audit-log row so operators can trace who granted the override.
    """
    token = getattr(request, "auth_token", None)
    if token is None:
        return "unknown"
    if token.name:
        return token.name
    return token.key_prefix


def _is_valid_email(value):
    """Per-email validity: non-empty string, contains ``@``, passes validator."""
    if not isinstance(value, str):
        return False
    value = value.strip()
    if not value or "@" not in value:
        return False
    try:
        validate_email(value)
    except ValidationError:
        return False
    return True


# Issue #1936: ``GET /api/tier-overrides`` status filter values.
LIST_STATUS_VALUES = ("all", "effective", "ineffective")

# ``GET /api/tier-overrides`` page size: default 200, clamped to the batch cap.
LIST_LIMIT_DEFAULT = 200

_LIST_ROW_EXAMPLE = {
    "id": 42,
    "email": "leftover@example.com",
    "tier": "basic",
    "stored_tier": "main",
    "tier_at_grant": "free",
    "raises_access": False,
    "source": "manual",
    "created_at": "2026-09-01T10:00:00+00:00",
    "expires_at": "2026-12-01T10:00:00+00:00",
    "granted_by": "staff@example.com",
}


@token_required
@csrf_exempt
@require_methods("GET", "POST")
@openapi_spec(
    tag="Tier overrides",
    summary="List or grant tier overrides",
    methods={
        "GET": {
            "summary": "List active tier overrides",
            "description": (
                "Lists every active, non-expired ``TierOverride`` ordered by "
                "``expires_at`` ascending (the Studio tier overrides list "
                "order). A grant raises access when its tier level is above "
                "the member's current stored tier (``free`` when unset); "
                "otherwise it is not raising access (a leftover grant). A "
                "weaker grant shadowed by a stronger one still raises access "
                "when it is above the stored tier. Status is computed at "
                "request time. ``total``, ``effective_count`` and "
                "``ineffective_count`` are always the unfiltered totals; "
                "``count`` is the number of rows returned."
            ),
            "query": {
                "status": {
                    "type": "string",
                    "enum": list(LIST_STATUS_VALUES),
                    "required": False,
                    "description": (
                        "``all`` (default), ``effective`` (raising access) "
                        "or ``ineffective`` (not raising access)."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "required": False,
                    "description": (
                        f"Maximum rows, default {LIST_LIMIT_DEFAULT}, "
                        f"clamped to {BATCH_CAP}."
                    ),
                },
            },
            "responses": {
                200: {
                    "description": "Active overrides plus status counts.",
                    "example": {
                        "count": 1,
                        "total": 5,
                        "effective_count": 2,
                        "ineffective_count": 3,
                        "overrides": [_LIST_ROW_EXAMPLE],
                    },
                },
                401: {"description": "Missing, invalid or non-staff token."},
                422: {
                    "description": (
                        "Invalid ``status`` (code ``invalid_status``) or "
                        "``limit`` (code ``validation_error``)."
                    ),
                    "example": {
                        "error": "Invalid status: 'nope'",
                        "code": "invalid_status",
                        "details": {
                            "field": "status",
                            "allowed": list(LIST_STATUS_VALUES),
                        },
                    },
                },
            },
        },
        "POST": {
            "summary": "Grant a contact-import-style tier override",
            "description": (
                "Grants the same long-lived ``TierOverride`` the Studio bulk "
                "contact-import gives non-paying members: a 10-year override "
                "(default tier ``main``) that deactivates any existing active "
                "override and records who granted it. A single grant is a "
                "one-element ``emails`` list. Missing users are created "
                "(``signup_source=imported``, ``email_verified=false``). "
                "Re-running an identical active grant is idempotent "
                "(``skipped_idempotent``, no new row). ``tier`` is validated "
                "against ``Tier`` -- unknown slug -> ``unknown_tier``; "
                "``free`` / ``level==0`` -> ``invalid_tier``. Batch cap "
                f"{BATCH_CAP}."
            ),
            "request_body": {
                "required": ["emails"],
                "properties": {
                    "emails": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "tier": {"type": "string"},
                    "expires_at": {
                        "type": "string",
                        "format": "date",
                        "description": "Optional UTC expiry date, today or later.",
                    },
                },
                "example": {
                    "emails": [
                        "vinayak@example.com",
                        "newcohort@example.com",
                    ],
                    "tier": "main",
                },
            },
            "responses": {
                200: {
                    "description": "Per-email grant results plus counts.",
                    "example": {
                        "tier": "main",
                        "granted": 2,
                        "skipped": 1,
                        "malformed": 0,
                        "results": [
                            {
                                "email": "a@x.com",
                                "status": "granted",
                                "created_user": True,
                            },
                            {
                                "email": "b@x.com",
                                "status": "granted",
                                "created_user": False,
                            },
                            {
                                "email": "c@x.com",
                                "status": "skipped_idempotent",
                                "created_user": False,
                                "expires_at": "2036-05-06T00:00:00+00:00",
                            },
                        ],
                    },
                },
                400: {
                    "description": (
                        "Malformed body, unknown/invalid tier, or over-cap "
                        "batch. Codes: ``missing_emails``, ``unknown_tier``, "
                        "``invalid_tier``, ``batch_too_large``."
                    ),
                    "example": {
                        "error": "Unknown tier: nope",
                        "code": "unknown_tier",
                    },
                },
            },
        },
    },
)
def tier_overrides_collection(request):
    """``GET`` lists active overrides; ``POST`` grants overrides."""
    if request.method == "GET":
        return _list_overrides(request)
    return _grant_overrides(request)


def _serialize_list_row(override):
    return {
        "id": override.pk,
        "email": override.user.email,
        "tier": override.override_tier.slug,
        "stored_tier": override.stored_tier_slug or "free",
        "tier_at_grant": (
            override.original_tier.slug if override.original_tier else None
        ),
        "raises_access": override.raises_access,
        "source": override.source or "manual",
        "created_at": override.created_at.isoformat(),
        "expires_at": override.expires_at.isoformat(),
        "granted_by": (
            override.granted_by.email if override.granted_by else None
        ),
    }


def _list_overrides(request):
    """Return active overrides with raises-access status (issue #1936).

    Two queries regardless of row count: one aggregate for the counts and
    one select (stored tier, grant-time tier and granter joined) for rows.
    """
    status = (request.GET.get("status") or "all").strip().lower()
    if status not in LIST_STATUS_VALUES:
        return error_response(
            f"Invalid status: {request.GET.get('status')!r}",
            "invalid_status",
            status=422,
            details={"field": "status", "allowed": list(LIST_STATUS_VALUES)},
        )
    limit, err = parse_limit(
        request.GET.get("limit"),
        default=LIST_LIMIT_DEFAULT,
        maximum=BATCH_CAP,
    )
    if err is not None:
        return err

    base = annotate_override_raises_access(
        TierOverride.objects.filter(
            is_active=True, expires_at__gt=timezone.now(),
        )
        .select_related("user", "override_tier", "original_tier", "granted_by")
        .annotate(stored_tier_slug=F("user__membership__tier__slug"))
        .order_by("expires_at", "pk")
    )
    counts = base.aggregate(
        total=Count("pk"),
        effective=Count("pk", filter=Q(raises_access=True)),
    )
    rows = base
    if status == "effective":
        rows = rows.filter(raises_access=True)
    elif status == "ineffective":
        rows = rows.filter(raises_access=False)
    overrides = [_serialize_list_row(o) for o in rows[:limit]]
    return JsonResponse(
        {
            "count": len(overrides),
            "total": counts["total"],
            "effective_count": counts["effective"],
            "ineffective_count": counts["total"] - counts["effective"],
            "overrides": overrides,
        },
        status=200,
    )


def _grant_overrides(request):
    """Grant a 10-year tier override to one or many members.

    Request body::

        {"emails": ["x@y.com", ...], "tier": "main"}

    ``tier`` is optional and defaults to ``main``. Returns
    ``{tier, granted, skipped, malformed, results}`` where ``results`` is a
    per-email array with status ``granted`` / ``skipped_idempotent`` /
    ``malformed``.
    """
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error

    emails = data.get("emails") if isinstance(data, dict) else None
    if not isinstance(emails, list):
        return error_response(
            "emails must be a list",
            "missing_emails",
        )

    # Validate tier BEFORE any write so an invalid slug grants nothing.
    tier_slug = (data.get("tier") if isinstance(data, dict) else None) or DEFAULT_TIER_SLUG
    tier = Tier.objects.filter(slug=tier_slug).first()
    if tier is None:
        return error_response(
            f"Unknown tier: {tier_slug}",
            "unknown_tier",
        )
    if tier.level == 0:
        return error_response(
            f"Tier '{tier_slug}' cannot be granted as an override (overrides only upgrade)",
            "invalid_tier",
        )

    override_expires_at = None
    raw_expires_at = data.get("expires_at")
    if raw_expires_at is not None:
        try:
            expiry_date = datetime.datetime.strptime(
                raw_expires_at, "%Y-%m-%d",
            ).date()
            today_utc = timezone.now().astimezone(datetime.timezone.utc).date()
            if expiry_date < today_utc:
                raise ValueError
            override_expires_at = datetime.datetime.combine(
                expiry_date,
                datetime.time(23, 59, 59, tzinfo=datetime.timezone.utc),
            )
        except (TypeError, ValueError):
            return error_response(
                "expires_at must be today or later in YYYY-MM-DD format",
                "validation_error",
                status=422,
                details={"field": "expires_at", "value": raw_expires_at},
            )

    # Batch cap check BEFORE any write so an over-cap batch grants nothing.
    if len(emails) > BATCH_CAP:
        return error_response(
            f"Batch too large: {len(emails)} exceeds cap of {BATCH_CAP}",
            "batch_too_large",
        )

    actor_label = _actor_label(request)

    # Whole batch in one atomic block (matches the import). Idempotency pre-filter
    # + the importer call + audit rows all commit or roll back together.
    with transaction.atomic():
        results = _grant_batch(
            emails, tier, request.user, actor_label,
            expires_at=override_expires_at,
        )

    granted = sum(1 for r in results if r["status"] == "granted")
    skipped = sum(1 for r in results if r["status"] == "skipped_idempotent")
    malformed = sum(1 for r in results if r["status"] == "malformed")

    return JsonResponse(
        {
            "tier": tier.slug,
            "expires_at": override_expires_at.isoformat() if override_expires_at else None,
            "granted": granted,
            "skipped": skipped,
            "malformed": malformed,
            "results": results,
        },
        status=200,
    )


def _dedupe_emails(emails):
    """Drop duplicate emails in first-seen order, preserving the original value.

    The dedup key is the normalized form the rest of the pipeline compares on
    (``normalize_email(stripped).lower()``) so two copies of the same address --
    including the same address in differing case or with surrounding whitespace
    -- collapse to a single entry. The kept entry is the FIRST raw value seen,
    so the ``results`` array still echoes the caller's input. Values that aren't
    well-formed emails can't be normalized, so they are passed through untouched
    (each keeps its own ``malformed`` result downstream).
    """
    seen = set()
    deduped = []
    for raw in emails:
        if _is_valid_email(raw):
            key = User.objects.normalize_email(raw.strip()).lower()
            if key in seen:
                continue
            seen.add(key)
        deduped.append(raw)
    return deduped


def _grant_batch(emails, tier, granted_by, actor_label, *, expires_at=None):
    """Grant ``tier`` overrides to ``emails``; return the per-email results list.

    Idempotency approach (a): for each well-formed email, check for an existing
    active override on the same ``override_tier`` with a future ``expires_at``.
    If present, mark ``skipped_idempotent`` and do NOT pass it to the importer
    (so no new history row is stacked). The remaining emails are handed to
    ``import_contact_rows(..., tier_assignment_mode="override")``, which upserts
    users and routes the grant through ``_apply_tier_override`` (deactivate any
    other active override + create a fresh 10-year row). ``created_user`` is
    derived from whether the user existed before the importer ran.
    """
    now = timezone.now()

    # De-duplicate the incoming list in first-seen order BEFORE processing. Two
    # copies of the same email (or the same email in differing case) collapse to
    # a single ``TierOverride`` inside ``import_contact_rows``, but the
    # idempotency pre-filter below sees no committed override yet for either
    # copy, so without this guard each duplicate would be appended to
    # ``to_grant`` -- double-counting ``granted`` and writing a second audit row.
    # The dedup key is the SAME normalized form the pre-filter and the importer
    # compare on (``normalize_email(...).lower()``), so the collapse is
    # consistent with downstream. Malformed values can't be normalized, so they
    # are never deduped against -- each one keeps its own ``malformed`` entry.
    emails = _dedupe_emails(emails)

    # ``results`` is built in input order: each entry is filled in-place so the
    # per-email array mirrors the request regardless of processing order. The
    # ``to_grant`` list holds the indices we still hand to the importer.
    results = [None] * len(emails)
    to_grant = []  # list of (index, raw, normalized, existed_before)

    for index, raw in enumerate(emails):
        if not _is_valid_email(raw):
            results[index] = {"email": raw, "status": "malformed"}
            continue

        normalized = User.objects.normalize_email(raw.strip()).lower()
        existing_user = User.objects.filter(email__iexact=normalized).first()

        existing_override = (
            TierOverride.objects.filter(
                user=existing_user,
                override_tier=tier,
                is_active=True,
                expires_at__gt=now,
            ).first()
            if existing_user is not None
            else None
        )
        if (
            existing_override is not None
            and (expires_at is None or existing_override.expires_at == expires_at)
        ):
            # Identical active override already in place -> no new row.
            # Surface the existing expiry so a re-grant attempt answers
            # "until when?" without a second read.
            results[index] = {
                "email": raw,
                "status": "skipped_idempotent",
                "created_user": False,
                "expires_at": existing_override.expires_at.isoformat(),
            }
            continue

        to_grant.append((index, raw, normalized, existing_user is not None))

    if to_grant:
        rows = [{"email": normalized} for (_i, _raw, normalized, _existed) in to_grant]
        import_contact_rows(
            rows,
            default_tier=tier,
            granted_by=granted_by,
            tier_assignment_mode="override",
            override_expires_at=expires_at,
        )

        for index, raw, normalized, existed in to_grant:
            user = User.objects.filter(email__iexact=normalized).first()
            CommunityAuditLog.objects.create(
                user=user,
                action="api_tier_override",
                details=f"actor_token={actor_label} tier={tier.slug}",
            )
            results[index] = {
                "email": raw,
                "status": "granted",
                "created_user": not existed,
            }

    return results


@token_required
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Tier overrides",
    summary="Revoke active tier overrides",
    methods={
        "POST": {
            "summary": "Revoke every active tier override for each email",
            "description": (
                "Deactivates each user's active ``TierOverride`` rows (manual "
                "and source-specific grants such as Maven) through the same "
                "service as the Studio revoke button, and writes one "
                "``tier_override_revoked`` audit row per revoked override. "
                "Rows stay as history. Emails resolve by primary email, then "
                "alias. Per-email ``status``: ``revoked``, "
                "``no_active_override``, ``user_not_found``, or ``malformed``. "
                "Re-running is idempotent (``no_active_override``). "
                "``dry_run: true`` reports ``would_revoke`` and writes "
                "nothing. ``ineffective_only: true`` revokes (or reports) "
                "only the member's active overrides that do not raise access "
                "(tier at or below the current stored tier) and leaves grants "
                "that raise access untouched; a member whose active overrides "
                "all raise access reports ``no_active_override``. "
                f"Batch cap {BATCH_CAP}."
            ),
            "request_body": {
                "required": ["emails"],
                "properties": {
                    "emails": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "dry_run": {"type": "boolean"},
                    "ineffective_only": {
                        "type": "boolean",
                        "description": (
                            "Only revoke grants that do not raise access. "
                            "Default false (revoke every active override)."
                        ),
                    },
                },
                "example": {"emails": ["refunded@example.com"]},
            },
            "responses": {
                200: {
                    "description": "Per-email revoke results plus counts.",
                    "example": {
                        "dry_run": False,
                        "revoked": 1,
                        "no_active_override": 1,
                        "user_not_found": 1,
                        "malformed": 0,
                        "results": [
                            {
                                "email": "refunded@example.com",
                                "status": "revoked",
                                "overrides": [
                                    {
                                        "id": 42,
                                        "tier": "main",
                                        "expires_at": "2036-05-06T00:00:00+00:00",
                                        "source": "maven:abc123",
                                    },
                                ],
                            },
                            {
                                "email": "free@example.com",
                                "status": "no_active_override",
                                "overrides": [],
                            },
                            {
                                "email": "ghost@example.com",
                                "status": "user_not_found",
                            },
                        ],
                    },
                },
                400: {
                    "description": (
                        "Malformed body or over-cap batch. Codes: "
                        "``missing_emails``, ``batch_too_large``."
                    ),
                    "example": {
                        "error": "emails must be a list",
                        "code": "missing_emails",
                    },
                },
            },
        },
    },
)
def tier_overrides_revoke(request):
    """Revoke the active tier overrides of one or many members by email."""
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error

    emails = data.get("emails") if isinstance(data, dict) else None
    if not isinstance(emails, list):
        return error_response("emails must be a list", "missing_emails")
    if len(emails) > BATCH_CAP:
        return error_response(
            f"Batch too large: {len(emails)} exceeds cap of {BATCH_CAP}",
            "batch_too_large",
        )
    dry_run = data.get("dry_run") is True
    ineffective_only = data.get("ineffective_only") is True

    actor = f"actor_token={_actor_label(request)}"
    results = []
    with transaction.atomic():
        for raw in _dedupe_emails(emails):
            results.append(_revoke_one(
                raw, actor=actor, dry_run=dry_run,
                ineffective_only=ineffective_only,
            ))

    counts = {
        status: sum(1 for r in results if r["status"] == status)
        for status in ("no_active_override", "user_not_found", "malformed")
    }
    revoked = sum(
        1 for r in results if r["status"] in ("revoked", "would_revoke")
    )
    return JsonResponse(
        {"dry_run": dry_run, "revoked": revoked, **counts, "results": results},
        status=200,
    )


def _serialize_override(override):
    return {
        "id": override.pk,
        "tier": override.override_tier.slug,
        "expires_at": override.expires_at.isoformat(),
        "source": override.source,
    }


def _revoke_one(raw, *, actor, dry_run, ineffective_only=False):
    if not _is_valid_email(raw):
        return {"email": raw, "status": "malformed"}
    user = resolve_user_by_email(raw)
    if user is None:
        return {"email": raw, "status": "user_not_found"}
    if dry_run:
        overrides = active_overrides_for(
            user, ineffective_only=ineffective_only,
        )
        status = "would_revoke" if overrides else "no_active_override"
    else:
        overrides = revoke_active_overrides(
            user, actor=actor, ineffective_only=ineffective_only,
        )
        status = "revoked" if overrides else "no_active_override"
    return {
        "email": raw,
        "status": status,
        "overrides": [_serialize_override(o) for o in overrides],
    }
