"""Staff API for the guarded internal-only Relay bulk proof.

The guards live in ``email_app.services.relay_campaigns``: recipients come only
from ``CAMPAIGN_TEST_RECIPIENTS``, the audience resolver is never called, and
no ``CampaignDelivery`` row is created. These views parse input and map the
service outcome to a response; recipient addresses never leave the service.
"""

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
from email_app.models import EmailCampaign
from email_app.services.relay_campaigns import (
    RelayCampaignError,
    RelayProofKeyUsed,
    RelayProofRejected,
    get_native_campaign_stats,
    native_test_campaign_key_match,
    preview_native_test_campaign,
    queue_native_test_campaign,
)

STATS_FIELDS = (
    "status", "recipient_count", "skipped_count", "sent_count",
    "delivered_count", "unique_open_count", "open_count",
    "unique_click_count", "click_count", "unsubscribe_count", "bounce_count",
    "complaint_count", "created_at", "updated_at", "scheduled_at", "sent_at",
    "send_halted", "send_halted_at", "send_halt_reason",
)
AUTH_RESPONSE = {401: {"description": "Missing or invalid staff token."}}
NOT_FOUND_RESPONSE = {404: {"description": "Campaign not found."}}
REJECTED_RESPONSE = {422: {"description": "A proof guard refused the request."}}


def _unknown_campaign():
    return error_response("Campaign not found", "unknown_campaign", status=404)


def _rejected(message):
    return error_response(message, "relay_proof_rejected", status=422)


def _proof_summary(plan):
    return {
        "campaign_key": plan["campaign_key"],
        "recipient_count": plan["recipient_count"],
        "domain_counts": plan["domain_counts"],
    }


@token_required
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Campaigns",
    methods={
        "POST": {
            "summary": "Validate the Relay bulk proof list and issue a signed key",
            "description": (
                "Checks ``CAMPAIGN_TEST_RECIPIENTS`` (200-500 unique addresses "
                "on the internal allowlist) without calling Relay. Returns only "
                "the signed key, the count, and per-domain counts."
            ),
            "responses": {
                200: {
                    "description": "The reviewed proof plan.",
                    "example": {
                        "campaign_key": "aishl-test-17-<nonce>-<signature>",
                        "recipient_count": 200,
                        "domain_counts": {"dtcdev.click": 200},
                    },
                },
                **AUTH_RESPONSE,
                **NOT_FOUND_RESPONSE,
                **REJECTED_RESPONSE,
            },
        },
    },
)
def campaign_relay_proof_dry_run(request, campaign_id):
    """POST ``/api/campaigns/<id>/relay-proof/dry-run``."""
    campaign = EmailCampaign.objects.filter(pk=campaign_id).first()
    if campaign is None:
        return _unknown_campaign()
    try:
        plan = preview_native_test_campaign(campaign)
    except RelayProofRejected as exc:
        return _rejected(str(exc))
    return JsonResponse(_proof_summary(plan))


@token_required
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Campaigns",
    methods={
        "POST": {
            "summary": "Queue the Relay bulk proof with a reviewed dry-run key",
            "description": (
                "Queues the proof to the configured internal list only. The "
                "key must come from dry-run and the list must be unchanged."
            ),
            "request_body": {
                "type": "object",
                "properties": {"campaign_key": {"type": "string"}},
                "example": {"campaign_key": "aishl-test-17-<nonce>-<signature>"},
            },
            "responses": {
                200: {
                    "description": "The proof was queued.",
                    "example": {
                        "campaign_key": "aishl-test-17-<nonce>-<signature>",
                        "recipient_count": 200,
                        "domain_counts": {"dtcdev.click": 200},
                        "queued": True,
                    },
                },
                **AUTH_RESPONSE,
                **NOT_FOUND_RESPONSE,
                409: {"description": "This key was already used at Relay."},
                422: {"description": "Missing key, or a proof guard refused it."},
                502: {"description": "Relay failed while queueing."},
            },
        },
    },
)
def campaign_relay_proof_confirm(request, campaign_id):
    """POST ``/api/campaigns/<id>/relay-proof/confirm``."""
    campaign = EmailCampaign.objects.filter(pk=campaign_id).first()
    if campaign is None:
        return _unknown_campaign()
    data, parse_error = parse_json_body(request)
    if parse_error:
        return parse_error
    if not isinstance(data, dict):
        return body_must_be_object_response()
    campaign_key = data.get("campaign_key")
    if not isinstance(campaign_key, str) or not campaign_key:
        return validation_response({"campaign_key": "Must be the key returned by dry-run."})
    try:
        result = queue_native_test_campaign(campaign, campaign_key=campaign_key)
    except RelayProofRejected as exc:
        return _rejected(str(exc))
    except RelayProofKeyUsed as exc:
        return error_response(str(exc), "relay_proof_key_used", status=409)
    except RelayCampaignError as exc:
        return error_response(
            "Relay could not queue the proof campaign.",
            "relay_proof_queue_failed",
            status=502,
            details={"error": str(exc)},
        )
    return JsonResponse({**_proof_summary(result), "queued": result["queued"]})


@token_required
@csrf_exempt
@require_methods("GET")
@openapi_spec(
    tag="Campaigns",
    methods={
        "GET": {
            "summary": "Read Relay delivery stats for a bulk proof key",
            "description": (
                "Accepts only an ``aishl-test-<id>-...`` key for this campaign "
                "and returns an explicit field allowlist. Relay has no start "
                "timestamp, so compute msg/s from ``sent_count``, "
                "``created_at``, ``updated_at``, and ``sent_at``."
            ),
            "responses": {
                200: {
                    "description": "Allowlisted Relay campaign fields.",
                    "example": {
                        "campaign_key": "aishl-test-17-<nonce>-<signature>",
                        "status": "sending",
                        "recipient_count": 200,
                        "sent_count": 120,
                        "sent_at": None,
                    },
                },
                **AUTH_RESPONSE,
                404: {"description": "Unknown campaign, or no such Relay campaign."},
                422: {"description": "Key is not a proof key for this campaign."},
                502: {"description": "Relay failed while reading stats."},
            },
        },
    },
)
def campaign_relay_proof_stats(request, campaign_id, campaign_key):
    """GET ``/api/campaigns/<id>/relay-proof/<campaign_key>``."""
    if not EmailCampaign.objects.filter(pk=campaign_id).exists():
        return _unknown_campaign()
    # The signature depends on the current list, so it is deliberately not
    # rechecked here: a later list change must not make a sent proof unreadable.
    if native_test_campaign_key_match(campaign_id, campaign_key) is None:
        return _rejected("Key is not a Relay proof key for this campaign.")
    try:
        native = get_native_campaign_stats(campaign_key, allow_not_found=True)
    except RelayCampaignError as exc:
        return error_response(
            "Relay campaign stats are unavailable.",
            "relay_campaign_stats_unavailable",
            status=502,
            details={"error": str(exc)},
        )
    if native is None:
        return error_response(
            "Relay has no campaign for this key.", "relay_proof_not_found", status=404,
        )
    return JsonResponse({
        "campaign_key": campaign_key,
        **{field: native.get(field) for field in STATS_FIELDS},
    })
