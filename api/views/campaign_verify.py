"""Verify-or-delete campaign operations: audit, guarded purge, backup export.

Sending stays a Studio action (see ``api/views/campaigns.py``); this module
only reads audit data and runs the deadline purge, which is re-checked per
account and refuses to run before a delivery's deadline.
"""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from accounts.models import DeletedUserArchive
from accounts.services.verify_deadline_purge import (
    DEFAULT_LIMIT,
    VerifyDeadlineError,
    purge_unverified_after_deadline,
    unverified_audit,
)
from api.openapi import openapi_spec
from api.safety import error_response
from api.serializers.datetime import isoformat_or_none
from api.utils import (
    body_must_be_object_response,
    parse_json_body,
    require_methods,
    validation_response,
)
from email_app.models import EmailCampaign

MAX_PURGE_LIMIT = 1000
MAX_ARCHIVE_PAGE = 500


@token_required
@csrf_exempt
@require_methods("GET")
@openapi_spec(
    tag="Campaigns",
    methods={
        "GET": {
            "summary": "Audit unverified accounts by signup age",
            "description": (
                "Read-only counts of accounts with an unverified email, "
                "bucketed by signup age, with the segments that decide who "
                "can be emailed and who is protected from deletion."
            ),
            "responses": {
                200: {
                    "description": "Counts per age bucket and segment.",
                    "example": {
                        "generated_at": "2026-10-31T17:00:00+00:00",
                        "unverified": {"total": 3800, "emailable": 3790},
                        "verified_total": 1200,
                        "by_signup_age": {"under_30_days": {"total": 10}},
                    },
                },
                401: {"description": "Missing or invalid staff token."},
            },
        },
    },
)
def unverified_audit_view(request):
    """GET ``/api/campaigns/unverified-audit``."""
    return JsonResponse(unverified_audit())


@token_required
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Campaigns",
    methods={
        "POST": {
            "summary": "Delete accounts that ignored a verify-or-delete campaign",
            "description": (
                "Only for an ``unverified_only`` campaign with "
                "``verify_deadline_days``. Considers recipients whose email "
                "was confirmed sent at least that many days ago, re-checks "
                "each account, archives it, then deletes it. ``dry_run`` "
                "(default true) counts without writing. A real run needs "
                "``confirm: \"delete\"`` and is bounded per call: repeat "
                "with ``after`` set to the returned ``last_delivery_id`` "
                "until ``complete`` is true."
            ),
            "request_body": {
                "type": "object",
                "properties": {
                    "dry_run": {"type": "boolean"},
                    "confirm": {"type": "string", "enum": ["delete"]},
                    "limit": {"type": "integer"},
                    "after": {"type": "integer"},
                },
                "example": {"dry_run": True, "limit": 500, "after": 0},
            },
            "responses": {
                200: {
                    "description": "Counts for this call.",
                    "example": {
                        "campaign_id": 17,
                        "dry_run": True,
                        "deadline_days": 7,
                        "due_deliveries": 3700,
                        "processed": 500,
                        "deleted": 0,
                        "reasons": {"deletable": 480, "verified": 20},
                        "last_delivery_id": 9000,
                        "complete": False,
                    },
                },
                401: {"description": "Missing or invalid staff token."},
                404: {"description": "Campaign not found."},
                409: {"description": "Not a verify-or-delete campaign."},
                422: {"description": "Invalid body or missing confirmation."},
            },
        },
    },
)
def campaign_unverified_deletion(request, campaign_id):
    """POST ``/api/campaigns/<id>/unverified-deletion``."""
    data, parse_error = parse_json_body(request)
    if parse_error:
        return parse_error
    if not isinstance(data, dict):
        return body_must_be_object_response()
    unknown = sorted(set(data) - {"dry_run", "confirm", "limit", "after"})
    errors = {field: "Unknown field." for field in unknown}
    dry_run = data.get("dry_run", True)
    if not isinstance(dry_run, bool):
        errors["dry_run"] = "Must be a boolean."
    for field, default, ceiling in (
        ("limit", DEFAULT_LIMIT, MAX_PURGE_LIMIT), ("after", 0, None),
    ):
        value = data.get(field, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or (
            field == "limit" and value < 1
        ) or (ceiling and value > ceiling):
            errors[field] = "Must be a positive integer within range."
    if dry_run is False and data.get("confirm") != "delete":
        errors["confirm"] = 'Required for a real run: "delete".'
    if errors:
        return validation_response(errors)
    if not EmailCampaign.objects.filter(pk=campaign_id).exists():
        return error_response("Campaign not found", "unknown_campaign", status=404)
    try:
        summary = purge_unverified_after_deadline(
            campaign_id,
            dry_run=dry_run,
            limit=data.get("limit", DEFAULT_LIMIT),
            after=data.get("after", 0),
        )
    except VerifyDeadlineError as exc:
        return error_response(str(exc), "not_verify_deadline_campaign", status=409)
    return JsonResponse(summary)


@token_required
@csrf_exempt
@require_methods("GET")
@openapi_spec(
    tag="Campaigns",
    methods={
        "GET": {
            "summary": "Export the backup of accounts deleted for a campaign",
            "description": (
                "Pages through ``DeletedUserArchive`` rows written before "
                "each deletion. Pass the last returned ``id`` as ``after``."
            ),
            "query": {
                "after": {"type": "integer", "required": False},
                "limit": {"type": "integer", "required": False},
            },
            "responses": {
                200: {
                    "description": "Archive rows plus the total.",
                    "example": {"total": 1, "results": [{"id": 1, "email": "a@example.com"}]},
                },
                401: {"description": "Missing or invalid staff token."},
            },
        },
    },
)
def campaign_deleted_users(request, campaign_id):
    """GET ``/api/campaigns/<id>/deleted-users``."""
    try:
        after = int(request.GET.get("after", 0))
        limit = min(int(request.GET.get("limit", MAX_ARCHIVE_PAGE)), MAX_ARCHIVE_PAGE)
    except ValueError:
        return validation_response({"after": "after and limit must be integers."})
    rows = DeletedUserArchive.objects.filter(campaign_id=campaign_id)
    page = rows.filter(pk__gt=after).order_by("pk")[: max(limit, 1)]
    return JsonResponse({
        "total": rows.count(),
        "results": [
            {
                "id": row.pk,
                "original_user_id": row.original_user_id,
                "email": row.email,
                "deleted_at": isoformat_or_none(row.deleted_at),
                "snapshot": row.snapshot,
            }
            for row in page
        ],
    })
