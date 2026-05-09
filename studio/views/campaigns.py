"""Studio views for email campaign management."""

import logging
import re

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.utils.tags import normalize_tags
from email_app.models import EmailCampaign
from email_app.services.email_classification import EMAIL_KIND_PROMOTIONAL
from email_app.services.email_service import EmailService, EmailServiceError
from studio.decorators import staff_required

logger = logging.getLogger(__name__)

TEST_RECIPIENT_SPLIT_RE = re.compile(r"[\s,;]+")
# Operators may type tags comma-, space-, semicolon-, or newline-separated
# in the include/exclude inputs (issue #357). The form also submits HTML
# multi-select values as a list, so we accept both shapes.
TAG_INPUT_SPLIT_RE = re.compile(r"[\s,;]+")
TEST_EMAIL_FOOTER_NOTE = (
    "Test send only. This address is not linked to a subscriber record, so no unsubscribe link is included."
)
User = get_user_model()


def _build_campaign_detail_context(campaign, *, test_recipients=""):
    """Build the shared context for the campaign detail page."""
    engagement = campaign.email_logs.aggregate(
        sent=Count("id"),
        opened=Count("id", filter=Q(opened_at__isnull=False)),
        clicked=Count("id", filter=Q(clicked_at__isnull=False)),
    )
    sent = engagement["sent"] or 0
    opened = engagement["opened"] or 0
    clicked = engagement["clicked"] or 0
    opened_rate = (opened / sent * 100) if sent else 0
    clicked_rate = (clicked / sent * 100) if sent else 0

    return {
        "campaign": campaign,
        "recipient_count": campaign.get_recipient_count(),
        "engagement": {
            "sent": sent,
            "opened": opened,
            "clicked": clicked,
            "opened_rate": opened_rate,
            "clicked_rate": clicked_rate,
        },
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


def _parse_campaign_tags(request, field_name):
    """Parse a campaign tag input (include/exclude) into a normalized list.

    Accepts both ``getlist`` (multi-select, repeated inputs) and a single
    free-form string with comma/space/semicolon/newline separators -- the
    typeahead input ships values as a single text field and the multi-
    select form ships them as repeated POST keys, so support both.

    De-duplicates while preserving the operator's original order so the
    detail-page summary reads in a predictable shape.
    """
    pieces = []
    raw_list = request.POST.getlist(field_name)
    for raw in raw_list:
        if raw is None:
            continue
        # A single field may itself contain a comma-separated list of tags
        # (free-form typing), so split each entry too.
        pieces.extend(TAG_INPUT_SPLIT_RE.split(raw))

    seen = set()
    ordered = []
    for tag in normalize_tags(pieces):
        if tag and tag not in seen:
            seen.add(tag)
            ordered.append(tag)
    return ordered


def _all_known_contact_tags():
    """Sorted union of every existing contact tag across users.

    Powers the typeahead ``<datalist>`` on the campaign form so operators
    pick from tags already in use. Mirrors the helper in
    ``studio/views/users.py`` (issue #354) -- if you change one, change
    both.
    """
    seen = set()
    for tag_list in User.objects.values_list('tags', flat=True):
        if not tag_list:
            continue
        for tag in normalize_tags(tag_list):
            seen.add(tag)
    return sorted(seen)


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
        target_tags_any = _parse_campaign_tags(request, "target_tags_any")
        target_tags_none = _parse_campaign_tags(request, "target_tags_none")
        slack_filter = _normalize_slack_filter(
            request.POST.get("slack_filter", "")
        )

        campaign = EmailCampaign.objects.create(
            subject=subject,
            body=body,
            target_min_level=target_min_level,
            target_tags_any=target_tags_any,
            target_tags_none=target_tags_none,
            slack_filter=slack_filter,
            status="draft",
        )
        messages.success(
            request,
            f'Draft campaign "{campaign.subject}" created.',
        )
        return redirect("studio_campaign_detail", campaign_id=campaign.pk)

    # Brand-new campaign defaults to ``target_min_level=0`` (Everyone),
    # so the helper next to the audience selector can preview the size.
    recipient_count = _recipient_count_for_level(0)
    return render(
        request,
        "studio/campaigns/form.html",
        {
            "campaign": None,
            "form_action": "create",
            "recipient_count": recipient_count,
            "known_tags": _all_known_contact_tags(),
        },
    )


def _normalize_slack_filter(value):
    """Map raw form value to a valid EmailCampaign slack_filter choice."""
    valid = {choice[0] for choice in EmailCampaign.SLACK_FILTER_CHOICES}
    if value in valid:
        return value
    return EmailCampaign.SLACK_FILTER_ANY


@staff_required
def campaign_edit(request, campaign_id):
    """Edit a draft campaign.

    Non-draft campaigns cannot be edited: attempts redirect to the detail
    page with a flash error so the operator sees the campaign's current
    state instead of a blank 404.
    """
    campaign = get_object_or_404(EmailCampaign, pk=campaign_id)

    if campaign.status != "draft":
        messages.error(
            request,
            f"This campaign is already {campaign.get_status_display().lower()} "
            f"and cannot be edited.",
        )
        return redirect("studio_campaign_detail", campaign_id=campaign.pk)

    if request.method == "POST":
        subject = request.POST.get("subject", "").strip()
        body = request.POST.get("body", "")
        target_min_level = int(request.POST.get("target_min_level", 0))
        target_tags_any = _parse_campaign_tags(request, "target_tags_any")
        target_tags_none = _parse_campaign_tags(request, "target_tags_none")
        slack_filter = _normalize_slack_filter(
            request.POST.get("slack_filter", "")
        )

        campaign.subject = subject
        campaign.body = body
        campaign.target_min_level = target_min_level
        campaign.target_tags_any = target_tags_any
        campaign.target_tags_none = target_tags_none
        campaign.slack_filter = slack_filter
        campaign.save(update_fields=[
            "subject",
            "body",
            "target_min_level",
            "target_tags_any",
            "target_tags_none",
            "slack_filter",
        ])

        messages.success(
            request,
            f'Campaign "{campaign.subject}" updated.',
        )
        return redirect("studio_campaign_detail", campaign_id=campaign.pk)

    recipient_count = campaign.get_recipient_count()
    return render(
        request,
        "studio/campaigns/form.html",
        {
            "campaign": campaign,
            "form_action": "edit",
            "recipient_count": recipient_count,
            "known_tags": _all_known_contact_tags(),
        },
    )


@staff_required
@require_POST
def campaign_delete(request, campaign_id):
    """Delete a draft campaign.

    Only draft campaigns can be deleted. ``sending`` and ``sent`` are
    refused so historic sends remain auditable.
    """
    campaign = get_object_or_404(EmailCampaign, pk=campaign_id)

    if campaign.status != "draft":
        messages.error(request, "Only draft campaigns can be deleted.")
        return redirect("studio_campaign_detail", campaign_id=campaign.pk)

    subject = campaign.subject
    campaign.delete()
    messages.success(request, f'Deleted draft campaign "{subject}".')
    return redirect("studio_campaign_list")


@staff_required
def campaign_detail(request, campaign_id):
    """View campaign details with preview and send controls."""
    campaign = get_object_or_404(EmailCampaign, pk=campaign_id)

    # Render the full email HTML via the shared email pipeline so what
    # the operator sees is exactly what the recipient would get (minus
    # the personalized unsubscribe link).
    service = EmailService()
    preview_html = service.render_markdown_email(
        campaign.subject,
        campaign.body,
        unsubscribe_url=None,
        footer_note=(
            "Studio preview — the unsubscribe link will be personalized "
            "per recipient when the campaign is sent."
        ),
    )

    context = _build_campaign_detail_context(campaign)
    context["preview_html"] = preview_html
    return render(request, "studio/campaigns/detail.html", context)


def _recipient_count_for_level(target_min_level):
    """Count users eligible for a given ``target_min_level``.

    Mirrors ``EmailCampaign.get_eligible_recipients`` without needing an
    existing campaign row — used by the Create form to show the default
    audience size before the draft is saved.
    """
    return User.objects.filter(
        tier__level__gte=target_min_level,
        unsubscribed=False,
        email_verified=True,
    ).count()


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
            service._send_ses(
                recipient,
                subject,
                full_html,
                email_kind=EMAIL_KIND_PROMOTIONAL,
                unsubscribe_url=unsubscribe_url,
            )
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
        target_tags_any=list(campaign.target_tags_any or []),
        target_tags_none=list(campaign.target_tags_none or []),
        slack_filter=campaign.slack_filter,
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
