"""Studio views for email campaign management."""

import logging
import re

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from email_app.models import EmailCampaign
from email_app.services.email_service import EmailService, EmailServiceError
from studio.decorators import staff_required

logger = logging.getLogger(__name__)

TEST_RECIPIENT_SPLIT_RE = re.compile(r"[\s,;]+")
TEST_EMAIL_FOOTER_NOTE = (
    "Test send only. This address is not linked to a subscriber record, so no unsubscribe link is included."
)
User = get_user_model()


def _build_campaign_detail_context(campaign, *, test_recipients=""):
    """Build the shared context for the campaign detail page."""
    return {
        "campaign": campaign,
        "recipient_count": campaign.get_recipient_count(),
        "test_recipients": test_recipients,
    }


def _parse_test_recipients(raw_addresses):
    """Split, validate, and de-duplicate a free-form recipient list."""
    recipients = []
    invalid = []
    seen = set()

    for candidate in TEST_RECIPIENT_SPLIT_RE.split(raw_addresses):
        candidate = candidate.strip()
        if not candidate:
            continue

        try:
            validate_email(candidate)
        except ValidationError:
            invalid.append(candidate)
            continue

        normalized = candidate.casefold()
        if normalized in seen:
            continue

        seen.add(normalized)
        recipients.append(candidate)

    return recipients, invalid


def _summarize_recipients(recipients, *, limit=3):
    """Return a compact human-readable recipient summary."""
    if len(recipients) <= limit:
        return ", ".join(recipients)
    return f"{', '.join(recipients[:limit])} (+{len(recipients) - limit} more)"


@staff_required
def campaign_list(request):
    """List all email campaigns with stats."""
    search = request.GET.get("q", "")
    status_filter = request.GET.get("status", "")

    campaigns = EmailCampaign.objects.all()
    if search:
        campaigns = campaigns.filter(subject__icontains=search)
    if status_filter:
        campaigns = campaigns.filter(status=status_filter)

    return render(
        request,
        "studio/campaigns/list.html",
        {
            "campaigns": campaigns,
            "search": search,
            "status_filter": status_filter,
        },
    )


@staff_required
def campaign_create(request):
    """Create a new email campaign."""
    if request.method == "POST":
        subject = request.POST.get("subject", "").strip()
        body = request.POST.get("body", "")
        target_min_level = int(request.POST.get("target_min_level", 0))

        campaign = EmailCampaign.objects.create(
            subject=subject,
            body=body,
            target_min_level=target_min_level,
            status="draft",
        )
        return redirect("studio_campaign_detail", campaign_id=campaign.pk)

    return render(
        request,
        "studio/campaigns/form.html",
        {
            "campaign": None,
            "form_action": "create",
        },
    )


@staff_required
def campaign_detail(request, campaign_id):
    """View campaign details with preview and send controls."""
    campaign = get_object_or_404(EmailCampaign, pk=campaign_id)
    return render(
        request,
        "studio/campaigns/detail.html",
        _build_campaign_detail_context(campaign),
    )


@staff_required
@require_POST
def campaign_test_send(request, campaign_id):
    """Send a campaign test email to one or more explicit addresses."""
    campaign = get_object_or_404(EmailCampaign, pk=campaign_id)
    raw_test_recipients = request.POST.get("test_recipients", "").strip()

    if not raw_test_recipients:
        messages.error(request, "Provide at least one email address for the test send.")
        return render(
            request,
            "studio/campaigns/detail.html",
            _build_campaign_detail_context(
                campaign,
                test_recipients=raw_test_recipients,
            ),
        )

    recipients, invalid = _parse_test_recipients(raw_test_recipients)
    if invalid:
        messages.error(
            request,
            f"Invalid email address(es): {', '.join(invalid)}.",
        )
        return render(
            request,
            "studio/campaigns/detail.html",
            _build_campaign_detail_context(
                campaign,
                test_recipients=raw_test_recipients,
            ),
        )

    if not recipients:
        messages.error(request, "Provide at least one valid email address for the test send.")
        return render(
            request,
            "studio/campaigns/detail.html",
            _build_campaign_detail_context(
                campaign,
                test_recipients=raw_test_recipients,
            ),
        )

    service = EmailService()
    subject = f"[TEST] {campaign.subject}"
    sent = []
    failed = {}

    for recipient in recipients:
        user = User.objects.filter(email__iexact=recipient).first()
        unsubscribe_url = None
        footer_note = TEST_EMAIL_FOOTER_NOTE

        if user is not None:
            unsubscribe_url = service._build_unsubscribe_url(user)
            footer_note = None

        full_html = service.render_markdown_email(
            subject,
            campaign.body,
            unsubscribe_url=unsubscribe_url,
            footer_note=footer_note,
        )

        try:
            service._send_ses(recipient, subject, full_html)
        except EmailServiceError as exc:
            failed[recipient] = str(exc)
            logger.warning(
                "Failed to send campaign test email %s to %s",
                campaign.pk,
                recipient,
                exc_info=True,
            )
        else:
            sent.append(recipient)

    if failed and sent:
        messages.warning(
            request,
            "Sent test email to "
            f"{len(sent)} of {len(recipients)} address(es): "
            f"{_summarize_recipients(sent)}. "
            f"Failed: {_summarize_recipients(list(failed))}.",
        )
        return redirect("studio_campaign_detail", campaign_id=campaign.pk)

    if failed:
        first_error = next(iter(failed.values()))
        messages.error(
            request,
            f"Failed to send test email to {_summarize_recipients(list(failed))}. {first_error}",
        )
        return render(
            request,
            "studio/campaigns/detail.html",
            _build_campaign_detail_context(
                campaign,
                test_recipients=raw_test_recipients,
            ),
        )

    messages.success(
        request,
        f"Test email sent to {len(sent)} address(es): {_summarize_recipients(sent)}.",
    )
    return redirect("studio_campaign_detail", campaign_id=campaign.pk)


@staff_required
@require_POST
def campaign_duplicate(request, campaign_id):
    """Create a new draft copy of an existing campaign."""
    campaign = get_object_or_404(EmailCampaign, pk=campaign_id)

    duplicate = EmailCampaign.objects.create(
        subject=f"{campaign.subject} (Copy)",
        body=campaign.body,
        target_min_level=campaign.target_min_level,
        status="draft",
    )
    messages.success(
        request,
        f'Created draft copy "{duplicate.subject}".',
    )
    return redirect("studio_campaign_detail", campaign_id=duplicate.pk)


@staff_required
@require_POST
def campaign_send(request, campaign_id):
    """Enqueue a campaign for background sending from Studio."""
    campaign = get_object_or_404(EmailCampaign, pk=campaign_id)

    if campaign.status != "draft":
        messages.error(
            request,
            f'Campaign "{campaign.subject}" is already {campaign.status}.',
        )
        return redirect("studio_campaign_detail", campaign_id=campaign.pk)

    from jobs.tasks import async_task

    task_id = async_task(
        "email_app.tasks.send_campaign.send_campaign",
        campaign_id=campaign.pk,
    )
    logger.info(
        "Enqueued campaign %s for sending from Studio (task_id=%s)",
        campaign.pk,
        task_id,
    )
    messages.success(
        request,
        f'Campaign "{campaign.subject}" queued for sending — watching it here.',
    )
    return redirect("studio_worker")
