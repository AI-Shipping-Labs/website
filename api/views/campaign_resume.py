"""Staff API endpoint to safely resume pending deliveries of a partial campaign."""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import require_methods
from api.views.campaign_scheduling import _load_campaign_or_error
from email_app.services.campaign_resume import (
    CampaignResumeConflict,
    resume_pending_campaign,
)


@token_required
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Campaigns",
    methods={
        "POST": {
            "summary": "Resume pending deliveries for a partial campaign",
            "description": (
                "Reconciles against prior Relay campaign keys and queues only "
                "deliveries still in ``pending`` state under a fresh Relay key. "
                "Already-sent deliveries are never touched."
            ),
            "responses": {
                200: {
                    "description": "Pending deliveries enqueued for resume.",
                    "example": {
                        "campaign_id": 18,
                        "resumed": True,
                        "pending_count": 1200,
                        "external_key": "aishl-campaign-18-resume-1",
                        "status": "sending",
                        "task_id": "task-uuid",
                    },
                },
                401: {"description": "Missing or invalid staff token."},
                404: {"description": "Campaign not found."},
                409: {"description": "Campaign cannot be resumed in its current state."},
            },
        },
    },
)
def campaign_resume(request, campaign_id):
    """POST ``/api/campaigns/<id>/resume``."""
    campaign, error = _load_campaign_or_error(campaign_id)
    if error is not None:
        return error
    try:
        result = resume_pending_campaign(campaign.pk, source="API campaign resume")
    except CampaignResumeConflict as exc:
        return error_response(
            str(exc),
            exc.code,
            status=409,
            details={"campaign_id": campaign.pk, "status": campaign.status},
        )
    return JsonResponse({
        "campaign_id": campaign.pk,
        "resumed": True,
        "pending_count": result.pending_count,
        "external_key": result.external_key,
        "status": result.campaign.status,
        "task_id": result.task_id,
    }, status=200)
