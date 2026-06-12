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
from email_app.services.email_service import EmailService, EmailServiceError
from integrations.config import get_config
from studio.decorators import staff_required

logger = logging.getLogger(__name__)

TEST_RECIPIENT_SPLIT_RE = re.compile(r"[\s,;]+")
# Session key + cap for remembering the operator's recent test-send
# addresses so they resurface as one-click chips (issue #921). No DB
# model — test sends don't write EmailLog rows, so the session is the
# lightest correct place to persist this per-operator convenience list.
RECENT_TEST_RECIPIENTS_SESSION_KEY = "recent_test_recipients"
RECENT_TEST_RECIPIENTS_CAP = 5
# Cap the merged You/Recent/Common suggestion row so it stays compact.
TEST_RECIPIENT_SUGGESTIONS_CAP = 8
# Operators may type tags comma-, space-, semicolon-, or newline-separated
# in the include/exclude inputs (issue #357). The form also submits HTML
# multi-select values as a list, so we accept both shapes.
TAG_INPUT_SPLIT_RE = re.compile(r"[\s,;]+")
TEST_EMAIL_FOOTER_NOTE = (
    "Test send only. This address is not linked to a subscriber record, so no unsubscribe link is included."
)
User = get_user_model()


def _valid_email_or_none(candidate):
    """Return the trimmed address if it validates, else None."""
    candidate = (candidate or "").strip()
    if not candidate:
        return None
    try:
        validate_email(candidate)
    except ValidationError:
        return None
    return candidate


def _split_recipient_config(raw):
    """Split a free-form CAMPAIGN_TEST_RECIPIENTS string into addresses."""
    if not raw:
        return []
    return [piece for piece in TEST_RECIPIENT_SPLIT_RE.split(raw) if piece.strip()]


def _test_recipient_suggestions(request):
    """Build the ordered, de-duplicated, validated suggestion list.

    Sources, in display order (issue #921):

    1. ``You`` — the operator's own ``request.user.email``, always first.
    2. ``Recent`` — addresses the operator most recently test-sent to,
       persisted in ``request.session`` (most-recent-first).
    3. ``Common`` — the configurable ``CAMPAIGN_TEST_RECIPIENTS`` list,
       read via ``get_config`` (Studio-editable, no redeploy).

    Invalid addresses are silently dropped. De-duplication is
    case-insensitive (first occurrence + its source ordering wins) and
    the merged list is capped at ``TEST_RECIPIENT_SUGGESTIONS_CAP`` so
    the chip row stays compact.
    """
    suggestions = []
    seen = set()

    def _add(raw_email, label):
        email = _valid_email_or_none(raw_email)
        if email is None:
            return
        normalized = email.casefold()
        if normalized in seen:
            return
        seen.add(normalized)
        suggestions.append({"email": email, "label": label})

    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        _add(getattr(user, "email", ""), "You")

    session = getattr(request, "session", None)
    if session is not None:
        for email in session.get(RECENT_TEST_RECIPIENTS_SESSION_KEY, []) or []:
            _add(email, "Recent")

    for email in _split_recipient_config(get_config("CAMPAIGN_TEST_RECIPIENTS", "")):
        _add(email, "Common")

    return suggestions[:TEST_RECIPIENT_SUGGESTIONS_CAP]


def _remember_recent_test_recipients(request, recipients):
    """Prepend just-sent addresses to the session recent list (cap 5).

    Most-recent-first, de-duplicated case-insensitively, so the next
    visit resurfaces them as ``Recent`` chips.
    """
    session = getattr(request, "session", None)
    if session is None or not recipients:
        return

    existing = session.get(RECENT_TEST_RECIPIENTS_SESSION_KEY, []) or []
    merged = list(recipients) + list(existing)

    ordered = []
    seen = set()
    for email in merged:
        email = (email or "").strip()
        if not email:
            continue
        normalized = email.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(email)

    session[RECENT_TEST_RECIPIENTS_SESSION_KEY] = ordered[:RECENT_TEST_RECIPIENTS_CAP]
    session.modified = True


def _build_campaign_detail_context(campaign, *, test_recipients="", test_recipient_suggestions=None):
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
        "test_recipient_suggestions": test_recipient_suggestions or [],
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
        audience_verification = _normalize_audience_verification(
            request.POST.get("audience_verification", "")
        )

        campaign = EmailCampaign.objects.create(
            subject=subject,
            body=body,
            target_min_level=target_min_level,
            target_tags_any=target_tags_any,
            target_tags_none=target_tags_none,
            slack_filter=slack_filter,
            audience_verification=audience_verification,
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


def _normalize_audience_verification(value):
    """Map raw form value to a valid audience_verification choice (issue #692).

    Unknown values fall back to the safe default (``verified_only``) so a
    typo in the POST body cannot relax the historical recipient filter.
    """
    valid = {choice[0] for choice in EmailCampaign.AUDIENCE_VERIFICATION_CHOICES}
    if value in valid:
        return value
    return EmailCampaign.AUDIENCE_VERIFICATION_VERIFIED_ONLY


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
        audience_verification = _normalize_audience_verification(
            request.POST.get("audience_verification", "")
        )

        campaign.subject = subject
        campaign.body = body
        campaign.target_min_level = target_min_level
        campaign.target_tags_any = target_tags_any
        campaign.target_tags_none = target_tags_none
        campaign.slack_filter = slack_filter
        campaign.audience_verification = audience_verification
        campaign.save(update_fields=[
            "subject",
            "body",
            "target_min_level",
            "target_tags_any",
            "target_tags_none",
            "slack_filter",
            "audience_verification",
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

    context = _build_campaign_detail_context(
        campaign,
        test_recipient_suggestions=_test_recipient_suggestions(request),
    )
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
                test_recipient_suggestions=_test_recipient_suggestions(request),
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
                test_recipient_suggestions=_test_recipient_suggestions(request),
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
                test_recipient_suggestions=_test_recipient_suggestions(request),
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
                email_type='campaign',
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
        _remember_recent_test_recipients(request, sent)
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
                test_recipient_suggestions=_test_recipient_suggestions(request),
            ),
        )

    _remember_recent_test_recipients(request, sent)
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
        audience_verification=campaign.audience_verification,
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

    from jobs.tasks import async_task, build_task_name

    task_id = async_task(
        "email_app.tasks.send_campaign.send_campaign",
        campaign_id=campaign.pk,
        task_name=build_task_name(
            "Send campaign",
            f"#{campaign.pk} {campaign.subject}",
            "Studio campaign detail",
        ),
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
