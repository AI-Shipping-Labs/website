"""Tier-override grant API endpoint (issue #833).

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

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from accounts.models import TierOverride
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import parse_json_body, require_methods
from community.models import CommunityAuditLog
from payments.models import Tier
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


@token_required
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Tier overrides",
    summary="Grant a 10-year tier override",
    methods={
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
def tier_overrides_grant(request):
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
        results = _grant_batch(emails, tier, request.user, actor_label)

    granted = sum(1 for r in results if r["status"] == "granted")
    skipped = sum(1 for r in results if r["status"] == "skipped_idempotent")
    malformed = sum(1 for r in results if r["status"] == "malformed")

    return JsonResponse(
        {
            "tier": tier.slug,
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


def _grant_batch(emails, tier, granted_by, actor_label):
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

        if existing_user is not None and TierOverride.objects.filter(
            user=existing_user,
            override_tier=tier,
            is_active=True,
            expires_at__gt=now,
        ).exists():
            # Identical active override already in place -> no new row.
            results[index] = {
                "email": raw,
                "status": "skipped_idempotent",
                "created_user": False,
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
