"""Campaign dispatch API: schedule, unschedule, send, and test-send.

Draft authoring lives in ``api/views/campaigns.py`` (which must never touch
the send path — a sentinel test enforces that). This module is the one
sanctioned API surface for crossing the send boundary:

- ``POST /api/campaigns/<id>/schedule``   — set/replace the send time.
- ``POST /api/campaigns/<id>/unschedule`` — clear it.
- ``POST /api/campaigns/<id>/send``       — claim and enqueue immediately.
- ``POST /api/campaigns/<id>/test-send``  — one explicit probe recipient.

Immediate and scheduled sends both go through
``email_app.services.campaign_dispatch.claim_and_enqueue_campaign``, the
same exactly-once claim Studio uses. Test sends never change campaign
status, mirroring Studio.
"""

import logging

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import (
    body_must_be_object_response,
    parse_json_body,
    require_methods,
    validation_response,
)
from api.views.campaigns import _serialize_campaign
from email_app.models import EmailCampaign
from email_app.services.campaign_dispatch import claim_and_enqueue_campaign
from email_app.services.campaign_scheduling import (
    CampaignScheduleConflict,
    CampaignScheduleValidationError,
    schedule_campaign,
    unschedule_campaign,
)
from email_app.services.campaign_test_send import (
    CampaignTestRecipientError,
    send_campaign_test_recipient,
    send_test_campaign_delivery,
)

logger = logging.getLogger(__name__)

_SCHEDULE_REQUEST_EXAMPLE = {
    "scheduled_at": "2026-11-05T10:00:00",
    "scheduled_timezone": "Europe/Berlin",
}

_SCHEDULE_ACTION_RESPONSES = {
    200: {
        "description": "Campaign schedule state.",
        "example": {
            **_SCHEDULE_REQUEST_EXAMPLE,
            "id": 1,
            "status": "draft",
            "scheduled_at": "2026-11-05T09:00:00+00:00",
        },
    },
    401: {"description": "Missing or invalid staff token."},
    404: {
        "description": "Campaign not found.",
        "example": {"error": "Campaign not found", "code": "unknown_campaign"},
    },
    409: {
        "description": "Campaign is not a draft (schedule actions apply to drafts only).",
        "example": {
            "error": "Campaign is sent; only draft campaigns can be scheduled.",
            "code": "campaign_not_draft",
        },
    },
    422: {
        "description": "Validation error (bad datetime, unknown timezone, past time).",
        "example": {
            "error": "Validation error",
            "code": "validation_error",
            "details": {"scheduled_at": "Must be in the future."},
        },
    },
}


def _load_campaign_or_error(campaign_id):
    campaign = EmailCampaign.objects.filter(pk=campaign_id).first()
    if campaign is None:
        return None, error_response(
            "Campaign not found", "unknown_campaign", status=404,
        )
    return campaign, None


def _schedule_conflict_response(exc):
    return error_response(str(exc), "campaign_not_draft", status=409)


def _schedule_validation_response(exc):
    return validation_response(exc.errors)


@token_required
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Campaigns",
    methods={
        "POST": {
            "summary": "Schedule a draft campaign for sending",
            "description": (
                "Sets the dispatch time on a draft campaign. The server "
                "claims and sends the campaign at that time via the "
                "every-minute scheduled dispatcher; the schedule is cleared "
                "when the send is claimed (by the dispatcher or a manual "
                "Studio send). ``scheduled_at`` accepts an ISO 8601 "
                "datetime; a naive value is interpreted in "
                "``scheduled_timezone`` (required then), an aware value may "
                "carry ``scheduled_timezone`` for display when the offset "
                "matches. Rescheduling an already-scheduled draft simply "
                "overwrites the fields."
            ),
            "request_body": {
                "required": ["scheduled_at"],
                "properties": {
                    "scheduled_at": {
                        "type": "string",
                        "description": (
                            "ISO 8601 datetime, naive or offset-aware; "
                            "must be in the future."
                        ),
                        "example": "2026-11-05T10:00:00",
                    },
                    "scheduled_timezone": {
                        "type": "string",
                        "description": (
                            "IANA timezone name for naive datetimes and "
                            "display (e.g. \"Europe/Berlin\")."
                        ),
                        "example": "Europe/Berlin",
                    },
                },
                "example": _SCHEDULE_REQUEST_EXAMPLE,
            },
            "responses": _SCHEDULE_ACTION_RESPONSES,
        },
    },
)
def campaign_schedule(request, campaign_id):
    """POST ``/api/campaigns/<id>/schedule``."""
    campaign, error = _load_campaign_or_error(campaign_id)
    if error is not None:
        return error
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return body_must_be_object_response()
    try:
        campaign = schedule_campaign(
            campaign.pk,
            scheduled_at=data.get("scheduled_at"),
            scheduled_timezone=data.get("scheduled_timezone"),
        )
    except CampaignScheduleValidationError as exc:
        return _schedule_validation_response(exc)
    except CampaignScheduleConflict as exc:
        return _schedule_conflict_response(exc)
    return JsonResponse(_serialize_campaign(campaign), status=200)


@token_required
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Campaigns",
    methods={
        "POST": {
            "summary": "Cancel a scheduled campaign send",
            "description": (
                "Clears the schedule on a draft campaign so the dispatcher "
                "never claims it. Refuses with 409 when the campaign is not "
                "scheduled or is no longer a draft (already sending/sent)."
            ),
            "request_body": {
                "body_required": False,
                "properties": {},
            },
            "responses": _SCHEDULE_ACTION_RESPONSES,
        },
    },
)
def campaign_unschedule(request, campaign_id):
    """POST ``/api/campaigns/<id>/unschedule``."""
    campaign, error = _load_campaign_or_error(campaign_id)
    if error is not None:
        return error
    try:
        campaign = unschedule_campaign(campaign.pk)
    except CampaignScheduleConflict as exc:
        return _schedule_conflict_response(exc)
    return JsonResponse(_serialize_campaign(campaign), status=200)


_SEND_RESPONSE_EXAMPLE = {
    "campaign_id": 1,
    "claimed": True,
    "status": "sending",
    "task_id": "purple-otter-delivers",
}


@token_required
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Campaigns",
    methods={
        "POST": {
            "summary": "Send a draft campaign now",
            "description": (
                "Claims the draft (exactly-once, same path as Studio's send "
                "button and the scheduled dispatcher) and enqueues the "
                "fan-out. Any pending schedule is consumed. A campaign that "
                "is not a draft answers 409 ``campaign_not_draft`` with its "
                "current status; clients should require explicit operator "
                "confirmation (``asl campaigns send`` gates on ``--yes``)."
            ),
            "request_body": {"body_required": False, "properties": {}},
            "responses": {
                200: {
                    "description": "Campaign claimed and queued for sending.",
                    "example": _SEND_RESPONSE_EXAMPLE,
                },
                401: {"description": "Missing or invalid staff token."},
                404: {
                    "description": "Campaign not found.",
                    "example": {
                        "error": "Campaign not found",
                        "code": "unknown_campaign",
                    },
                },
                409: {
                    "description": "Campaign is not a draft; no second send was queued.",
                    "example": {
                        "error": "Campaign is sent; no second send was queued.",
                        "code": "campaign_not_draft",
                    },
                },
            },
        },
    },
)
def campaign_send(request, campaign_id):
    """POST ``/api/campaigns/<id>/send``."""
    campaign, error = _load_campaign_or_error(campaign_id)
    if error is not None:
        return error
    result = claim_and_enqueue_campaign(
        campaign.pk,
        source="API campaign send",
        # Studio attributes monitored-wave releases to the operator; the API
        # mirrors that with the token's user.
        actor=(
            request.user
            if campaign.audience_verification
            == EmailCampaign.AUDIENCE_VERIFICATION_UNVERIFIED_ONLY
            else None
        ),
    )
    if not result.claimed:
        return error_response(
            (
                f"Campaign is {result.campaign.status}; "
                "no second send was queued."
            ),
            "campaign_not_draft",
            status=409,
            details={"campaign_id": campaign.pk, "status": result.campaign.status},
        )
    return JsonResponse({
        "campaign_id": campaign.pk,
        "claimed": True,
        "status": result.campaign.status,
        "task_id": result.task_id,
    }, status=200)


_TEST_SEND_RESPONSE_EXAMPLE = {
    "campaign_id": 1,
    "relay_message_id": "ses-message-id",
    "status": "accepted",
}


@token_required
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Campaigns",
    methods={
        "POST": {
            "summary": "Send a campaign test email",
            "description": (
                "Delivers one explicit probe recipient without changing the "
                "campaign draft, status, or audience. Mirrors Studio's "
                "test send, including the re-permission policy for "
                "``unverified_only`` campaigns. Unknown or invalid "
                "addresses answer 422; policy or transport failures answer "
                "502 ``campaign_test_send_failed``."
            ),
            "request_body": {
                "required": ["email"],
                "properties": {
                    "email": {
                        "type": "string",
                        "description": "Exactly one recipient address.",
                        "example": "alice@example.com",
                    },
                },
                "example": {"email": "alice@example.com"},
            },
            "responses": {
                200: {
                    "description": "Test email accepted by Relay.",
                    "example": _TEST_SEND_RESPONSE_EXAMPLE,
                },
                401: {"description": "Missing or invalid staff token."},
                404: {
                    "description": "Campaign not found.",
                    "example": {
                        "error": "Campaign not found",
                        "code": "unknown_campaign",
                    },
                },
                422: {
                    "description": "Missing or invalid recipient email.",
                    "example": {
                        "error": "Validation error",
                        "code": "validation_error",
                        "details": {"email": "Must be a valid email address."},
                    },
                },
                502: {
                    "description": (
                        "Test delivery failed (policy or transport boundary)."
                    ),
                    "example": {
                        "error": "Campaign test send failed.",
                        "code": "campaign_test_send_failed",
                    },
                },
            },
        },
    },
)
def campaign_test_send(request, campaign_id):
    """POST ``/api/campaigns/<id>/test-send``."""
    campaign, error = _load_campaign_or_error(campaign_id)
    if error is not None:
        return error
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return body_must_be_object_response()
    email = data.get("email")
    if not isinstance(email, str) or not email.strip():
        return validation_response({"email": "Provide a recipient email."})
    email = email.strip()
    try:
        validate_email(email)
    except ValidationError:
        return validation_response({"email": "Must be a valid email address."})
    try:
        message_id = send_campaign_test_recipient(
            campaign,
            email,
            send=send_test_campaign_delivery,
        )
    except CampaignTestRecipientError as exc:
        return error_response(
            str(exc), "campaign_test_send_failed", status=502,
        )
    except Exception:
        # External rendering/transport boundary: never leak provider payloads.
        logger.exception("Campaign test delivery failed for campaign %s", campaign.pk)
        return error_response(
            "Test delivery failed. Inspect the server logs.",
            "campaign_test_send_failed",
            status=502,
        )
    return JsonResponse({
        "campaign_id": campaign.pk,
        "relay_message_id": message_id,
        "status": "accepted",
    }, status=200)
