"""Studio campaign test-send responses for staff sessions and tokens."""

import logging
from functools import wraps

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from api.safety import error_response
from api.utils import parse_json_body, staff_token_or_session_required
from email_app.models import EmailCampaign
from email_app.services.campaign_test_send import (
    CampaignTestRecipientError,
    send_campaign_test_recipient,
)
from studio.decorators import staff_required

logger = logging.getLogger(__name__)


def campaign_test_access(view):
    authenticated_view = staff_token_or_session_required(view)
    session_view = staff_required(authenticated_view)

    @wraps(authenticated_view)
    def wrapper(request, *args, **kwargs):
        if request.headers.get("Authorization"):
            return authenticated_view(request, *args, **kwargs)
        return session_view(request, *args, **kwargs)

    return wrapper


def _detail_response(request, campaign, raw):
    from studio.views import campaigns

    return render(request, "studio/campaigns/detail.html", campaigns._build_campaign_detail_context(
        campaign, test_recipients=raw,
        test_recipient_suggestions=campaigns._test_recipient_suggestions(request),
    ))


def _parse_recipients(raw, *, token_request):
    from studio.views.campaigns import _parse_test_recipients

    if not raw:
        return [], "Provide at least one email address for the test send."
    recipients, invalid = _parse_test_recipients(raw)
    if invalid:
        return [], f"Invalid email address(es): {', '.join(invalid)}."
    if not recipients:
        return [], "Provide at least one valid email address for the test send."
    if token_request and len(recipients) != 1:
        return [], "Token test sends require exactly one explicit recipient."
    return recipients, ""


def _recipient_input(request, *, token_request):
    if not token_request or request.content_type != "application/json":
        return request.POST.get("test_recipients", "").strip()
    data, error = parse_json_body(request)
    if error is not None or not isinstance(data, dict):
        return ""
    raw = data.get("test_recipients", "")
    if not isinstance(raw, str):
        return ""
    return raw.strip()


def _send_recipients(campaign, recipients, *, service, send):
    sent = {}
    failed = {}
    for recipient in recipients:
        try:
            message_id = send_campaign_test_recipient(campaign, recipient, service=service, send=send)
        except CampaignTestRecipientError as exc:
            failed[recipient] = str(exc)
        except Exception:
            # External rendering/transport boundary: preserve the UI's partial
            # success behavior without returning raw provider payloads to callers.
            logger.exception("Failed campaign test send for campaign %s", campaign.pk)
            failed[recipient] = "Test delivery failed. Inspect the server logs."
        else:
            sent[recipient] = message_id
    return sent, failed


def _token_response(campaign, sent, failed):
    if failed:
        return error_response("Campaign test send failed.", "campaign_test_send_failed", status=502)
    return JsonResponse({
        "campaign_id": campaign.pk,
        "relay_message_id": next(iter(sent.values())),
        "status": "accepted",
    })


def _session_result(request, campaign, raw, recipients, sent, failed):
    from studio.views.campaigns import _remember_recent_test_recipients, _summarize_recipients

    if sent:
        _remember_recent_test_recipients(request, list(sent))
    if failed and sent:
        messages.warning(request, "Sent test email to "
            f"{len(sent)} of {len(recipients)} address(es): "
            f"{_summarize_recipients(list(sent))}. "
            f"Failed: {_summarize_recipients(list(failed))}.")
    elif failed:
        messages.error(request,
            f"Failed to send test email to {_summarize_recipients(list(failed))}. "
            f"{next(iter(failed.values()))}")
        return _detail_response(request, campaign, raw)
    else:
        messages.success(request,
            f"Test email sent to {len(sent)} address(es): {_summarize_recipients(list(sent))}.")
    return redirect("studio_campaign_detail", campaign_id=campaign.pk)


def campaign_test_send_response(request, campaign_id, *, service_factory, send):
    campaign = get_object_or_404(EmailCampaign, pk=campaign_id)
    token_request = hasattr(request, "auth_token")
    raw = _recipient_input(request, token_request=token_request)
    recipients, error = _parse_recipients(raw, token_request=token_request)
    if error:
        if token_request:
            return error_response("Provide exactly one valid test recipient.", "validation_error", status=422)
        messages.error(request, error)
        return _detail_response(request, campaign, raw)
    sent, failed = _send_recipients(campaign, recipients, service=service_factory(), send=send)
    if token_request:
        return _token_response(campaign, sent, failed)
    return _session_result(request, campaign, raw, recipients, sent, failed)
