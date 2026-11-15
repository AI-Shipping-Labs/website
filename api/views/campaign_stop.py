"""Staff API endpoint to stop an active Relay campaign and reconcile deliveries."""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import require_methods
from api.views.campaign_scheduling import _load_campaign_or_error
from email_app.services.relay_campaign_control import (
    CampaignStopConflict,
    stop_campaign,
)


@token_required
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Campaigns",
    methods={
        "POST": {
            "summary": "Stop an active Relay campaign and reconcile deliveries",
            "description": (
                "Calls Relay's stop endpoint on any queued/sending waves for "
                "this campaign, syncs Relay's sent count, and reverts any "
                "unconfirmed deliveries back to pending."
            ),
            "responses": {
                200: {
                    "description": "Campaign stopped and reconciled.",
                    "example": {
                        "campaign_id": 18,
                        "stopped": True,
                        "stopped_keys": ["aishl-campaign-18-resume-4"],
                        "sent_count": 196,
                        "pending_count": 1186,
                        "status": "paused",
                    },
                },
                401: {"description": "Missing or invalid staff token."},
                404: {"description": "Campaign not found."},
                409: {"description": "Campaign has no Relay key to stop."},
            },
        },
    },
)
def campaign_stop(request, campaign_id):
    """POST ``/api/campaigns/<id>/stop``."""
    campaign, error = _load_campaign_or_error(campaign_id)
    if error is not None:
        return error
    try:
        result = stop_campaign(campaign.pk)
    except CampaignStopConflict as exc:
        return error_response(
            str(exc),
            exc.code,
            status=409,
            details={"campaign_id": campaign.pk, "status": campaign.status},
        )
    return JsonResponse({
        "campaign_id": campaign.pk,
        "stopped": True,
        "stopped_keys": list(result.stopped_keys),
        "sent_count": result.sent_count,
        "pending_count": result.pending_count,
        "status": result.campaign.status,
    }, status=200)
