"""Account-merge API endpoint (issue #841 / slice 840b).

``POST /api/users/merge`` consolidates a secondary (merged-in) ``User`` into a
canonical (surviving) one. Thin wrapper over
``accounts.services.account_merge.merge_accounts`` -- the service owns the
``transaction.atomic()`` and the whole algorithm; the view only resolves the two
emails, maps typed errors to status codes, and serializes the ``MergePlan``.

Irreversible data movement plus billing, so the endpoint is staff-token-only and
ships a mandatory ``dry_run`` plan: operators run dry_run first, eyeball the
plan, then run for real. ``dry_run: true`` returns the FULL plan and persists
NOTHING.

Conventions mirror ``api/views/users.py`` / ``api/views/aliases.py``:
``token_required`` (staff-only JSON 401), the ``error_response`` envelope,
``@openapi_spec`` coverage, strict body keys (unknown field -> 422), and one
``CommunityAuditLog`` row per real merge (written inside the service).
"""

from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from accounts.services.account_merge import (
    SelfMergeError,
    StaffMergeRefused,
    SubscriptionConflictError,
    merge_accounts,
)
from accounts.services.email_resolution import normalize_email
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import parse_json_body, require_methods

User = get_user_model()

_ALLOWED_FIELDS = {"canonical_email", "merge_email", "dry_run", "force"}


def _actor_label(request):
    """Short label identifying the API caller (mirrors ``api/views/users.py``)."""
    token = getattr(request, "auth_token", None)
    if token is None:
        return "unknown"
    if token.name:
        return token.name
    return token.key_prefix


def _find_user(email):
    if not email:
        return None
    return User.objects.filter(email__iexact=str(email).strip()).first()


@token_required
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Users",
    summary="Merge a secondary user account into a canonical one",
    methods={
        "POST": {
            "summary": "Merge two accounts (irreversible; dry_run first)",
            "description": (
                "Consolidates the ``merge_email`` account into the "
                "``canonical_email`` account: repoints every owned row, "
                "reconciles profile / entitlement fields by precedence (UNION "
                "tags, OR ``email_verified``, higher ``tier`` wins, surviving "
                "subscription dictates billing fields), records ``merge_email`` "
                "as an ``EmailAlias`` of canonical so future Stripe/relay events "
                "route correctly, and deactivates the secondary login. Runs in "
                "ONE transaction.\n\n"
                "IRREVERSIBLE: there is no automated un-merge. Always run "
                "``dry_run: true`` first to review the plan; a dry run computes "
                "the full plan against the real DB and persists NOTHING.\n\n"
                "Errors: 400 ``self_merge`` (same normalized email); 404 "
                "``user_not_found`` (unknown ``canonical_email``/``merge_email``, "
                "``details.field`` says which); 409 ``subscription_conflict`` "
                "(both sides have a distinct live subscription -- refuses unless "
                "``force: true``, never silently drops a paid sub); 409 "
                "``staff_merge_refused`` (either side is staff -- requires "
                "``force: true``); 422 ``unknown_field`` / ``validation_error`` "
                "(strict body)."
            ),
            "request_body": {
                "required": ["canonical_email", "merge_email"],
                "properties": {
                    "canonical_email": {"type": "string"},
                    "merge_email": {"type": "string"},
                    "dry_run": {"type": "boolean"},
                    "force": {"type": "boolean"},
                },
                "example": {
                    "canonical_email": "stefanonoventa@gmail.com",
                    "merge_email": "47-gentle.virtual@icloud.com",
                    "dry_run": True,
                },
            },
            "responses": {
                200: {
                    "description": "The merge plan (or already-merged no-op).",
                    "example": {
                        "canonical_email": "stefanonoventa@gmail.com",
                        "merge_email": "47-gentle.virtual@icloud.com",
                        "dry_run": True,
                        "already_merged": False,
                        "moved": [
                            {
                                "model": "events.EventRegistration",
                                "field": "user",
                                "moved": 1,
                                "dropped": 1,
                            }
                        ],
                        "reconciled": {"email_verified": {"to": True}},
                        "tier_overrides": {"deactivated": [], "kept_active": None},
                        "stripe": {},
                        "conflicts": [],
                        "alias_created": "47-gentle.virtual@icloud.com",
                        "secondary_deactivated": False,
                    },
                },
                400: {
                    "description": "Self-merge (same normalized email).",
                    "example": {"error": "Cannot merge an account into itself", "code": "self_merge"},
                },
                404: {
                    "description": "Unknown canonical or merge email.",
                    "example": {
                        "error": "User not found",
                        "code": "user_not_found",
                        "details": {"field": "merge_email"},
                    },
                },
                409: {
                    "description": (
                        "Unresolved conflict: ``subscription_conflict`` (dual "
                        "live subs) or ``staff_merge_refused``. Retry with "
                        "``force: true`` to proceed."
                    ),
                    "example": {
                        "error": "Both accounts have a live subscription",
                        "code": "subscription_conflict",
                        "details": {
                            "canonical_subscription_id": "sub_A",
                            "merge_subscription_id": "sub_B",
                        },
                    },
                },
                422: {
                    "description": "Strict-body violation.",
                    "example": {
                        "error": "Unknown field: foo",
                        "code": "unknown_field",
                        "details": {"field": "foo"},
                    },
                },
            },
        },
    },
)
def merge_users(request):
    """``POST /api/users/merge`` -- merge ``merge_email`` into ``canonical_email``."""
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return error_response(
            "Body must be a JSON object",
            "validation_error",
            status=422,
            details={"field": "body", "expected": "object"},
        )

    for key in data:
        if key not in _ALLOWED_FIELDS:
            return error_response(
                f"Unknown field: {key}",
                "unknown_field",
                status=422,
                details={"field": key},
            )

    canonical_email = data.get("canonical_email")
    merge_email = data.get("merge_email")
    if not canonical_email or not isinstance(canonical_email, str):
        return error_response(
            "canonical_email is required",
            "validation_error",
            status=422,
            details={"field": "canonical_email"},
        )
    if not merge_email or not isinstance(merge_email, str):
        return error_response(
            "merge_email is required",
            "validation_error",
            status=422,
            details={"field": "merge_email"},
        )

    dry_run = bool(data.get("dry_run", False))
    force = bool(data.get("force", False))

    # Self-merge guard (normalized) BEFORE any user lookup so a typo'd same-email
    # request fails fast and identically whether or not the user exists.
    if normalize_email(canonical_email) == normalize_email(merge_email):
        return error_response(
            "Cannot merge an account into itself",
            "self_merge",
            status=400,
        )

    canonical = _find_user(canonical_email)
    if canonical is None:
        return error_response(
            "User not found",
            "user_not_found",
            status=404,
            details={"field": "canonical_email"},
        )
    secondary = _find_user(merge_email)
    if secondary is None:
        return error_response(
            "User not found",
            "user_not_found",
            status=404,
            details={"field": "merge_email"},
        )

    actor_label = _actor_label(request)
    try:
        plan = merge_accounts(
            canonical,
            secondary,
            actor_label=actor_label,
            actor=request.user,
            dry_run=dry_run,
            force=force,
        )
    except SelfMergeError:
        return error_response(
            "Cannot merge an account into itself",
            "self_merge",
            status=400,
        )
    except SubscriptionConflictError as exc:
        return error_response(
            "Both accounts have a live subscription; pass force to keep "
            "canonical's and drop the other.",
            "subscription_conflict",
            status=409,
            details={
                "canonical_subscription_id": exc.canonical_sub,
                "merge_subscription_id": exc.secondary_sub,
            },
        )
    except StaffMergeRefused:
        return error_response(
            "Refusing to merge a staff account without force.",
            "staff_merge_refused",
            status=409,
        )

    return JsonResponse(plan.to_dict(), status=200)
