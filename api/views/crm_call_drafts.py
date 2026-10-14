"""Read-only listing of attendee summary email drafts (issue #1951).

``GET /api/crm/call-email-drafts`` (staff token) lists the draft
summary emails the booking-call capture pipeline stores on CRM calls,
so operator tooling can check what is pending review in Studio. Write,
send, and delete are deliberately out of scope for the API: the only
sender is the explicit Studio staff action, and the scheduled pipeline
never sends email in any configuration.
"""

from django.db.models import Q
from django.http import JsonResponse

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.serializers.datetime import isoformat_or_none
from api.utils import require_methods
from crm.models.call import Call


def _draft_status(call):
    if call.email_sent_at:
        return "sent"
    if call.email_draft_dismissed_at:
        return "dismissed"
    return "draft"


@token_required
@require_methods("GET")
@openapi_spec(
    tag="CRM",
    summary="List attendee email drafts",
    methods={
        "GET": {
            "summary": "List CRM attendee summary email drafts",
            "description": (
                "Read-only staff listing of the attendee summary email "
                "drafts stored on CRM calls (booking-call capture). "
                "Each row carries the call id, attendee email, draft "
                "subject, status (``draft`` / ``sent`` / "
                "``dismissed``), and the draft-ready / sent "
                "timestamps. Nothing here sends email: the only send "
                "path is the explicit Studio staff action. "
                "Staff-token only."
            ),
            "responses": {
                200: {"description": "Draft rows, most recent call first."},
                401: {
                    "description": "Missing, invalid, or non-staff token.",
                },
                405: {"description": "Only GET is supported."},
            },
        },
    },
)
def crm_call_drafts(request):
    """``GET /api/crm/call-email-drafts`` -- list stored drafts."""
    calls = (
        Call.objects.filter(
            Q(email_draft_ready_at__isnull=False)
            | Q(email_sent_at__isnull=False)
            | Q(email_draft_dismissed_at__isnull=False),
        )
        .select_related("contact__user", "email_sent_by")
        .order_by("-occurred_at", "-id")
    )
    rows = [
        {
            "call_id": call.pk,
            "email": call.email,
            "contact_id": call.contact_id,
            "subject": call.email_draft_subject,
            "status": _draft_status(call),
            "draft_ready_at": isoformat_or_none(call.email_draft_ready_at),
            "sent_at": isoformat_or_none(call.email_sent_at),
            "dismissed_at": isoformat_or_none(call.email_draft_dismissed_at),
            "occurred_at": isoformat_or_none(call.occurred_at),
        }
        for call in calls
    ]
    return JsonResponse({"drafts": rows, "count": len(rows)})
