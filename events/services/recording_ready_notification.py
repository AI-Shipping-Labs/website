"""Host/operator notification for recordings uploaded from Zoom to S3."""

import logging
from dataclasses import dataclass
from types import SimpleNamespace

from django.db import transaction
from django.db.models import Q
from django.template.loader import render_to_string

from accounts.services.email_resolution import resolve_user_by_email
from email_app.services.email_service import EmailService
from events.services.display_time import format_event_time_range
from integrations.config import get_config, site_base_url

logger = logging.getLogger(__name__)

EMAIL_TYPE = 'event_recording_ready'


@dataclass(frozen=True)
class RecordingReadyRecipient:
    email: str
    source: str
    user: object | None = None


def resolve_recording_ready_recipients(event):
    """Return host/operator recipients in the issue-defined order."""
    recipients = []
    seen = set()

    def add(raw_email, source):
        email = (raw_email or '').strip()
        if not email:
            return
        key = email.lower()
        if key in seen:
            return
        seen.add(key)
        recipients.append(
            RecordingReadyRecipient(
                email=email,
                source=source,
                user=resolve_user_by_email(email),
            )
        )

    add(getattr(event, 'host_email', ''), 'event.host_email')

    host_links = (
        event.event_host_links.select_related('host')
        .filter(host__is_active=True)
        .order_by('position')
    )
    for link in host_links:
        add(link.host.email, 'event.hosts')

    if recipients:
        return recipients

    fallback = (get_config('STAFF_SIGNUP_NOTIFY_EMAIL', '') or '').strip()
    add(fallback, 'staff_fallback')
    return recipients


def notify_recording_ready(event):
    """Send the recording-ready notification once per event/recipient.

    The notification is best-effort: per-recipient failures are reported in
    the returned structure but do not raise to the recording upload task.
    """
    if not (getattr(event, 'recording_s3_url', '') or '').strip():
        logger.warning(
            'Skipping %s for event %s: recording_s3_url is empty',
            EMAIL_TYPE,
            event.pk,
        )
        return _skipped_result('no_recording_s3_url')

    recipients = resolve_recording_ready_recipients(event)
    if not recipients:
        logger.warning(
            'Skipping %s for event %s: no host or staff fallback recipient',
            EMAIL_TYPE,
            event.pk,
        )
        return _skipped_result('no_recipient')

    results = []
    email_log_ids = []

    for recipient in recipients:
        try:
            outcome = _send_one(event, recipient)
        except Exception as exc:
            logger.exception(
                'Failed to send %s for event %s to %s',
                EMAIL_TYPE,
                event.pk,
                recipient.email,
            )
            outcome = {
                'email': recipient.email,
                'source': recipient.source,
                'status': 'error',
                'reason': exc.__class__.__name__,
            }

        results.append(outcome)
        if outcome.get('email_log_id'):
            email_log_ids.append(outcome['email_log_id'])

    sent_count = sum(1 for item in results if item['status'] == 'sent')
    error_count = sum(1 for item in results if item['status'] == 'error')
    skipped_count = sum(1 for item in results if item['status'] == 'skipped')

    if sent_count and error_count:
        status = 'partial'
    elif sent_count:
        status = 'sent'
    elif error_count:
        status = 'error'
    else:
        status = 'skipped'

    skipped_reason = ''
    if status == 'skipped' and skipped_count == len(results):
        reasons = {item.get('reason', '') for item in results}
        skipped_reason = reasons.pop() if len(reasons) == 1 else 'all_skipped'

    return {
        'status': status,
        'recipient_count': sent_count,
        'attempted_recipient_count': len(recipients),
        'skipped_reason': skipped_reason,
        'email_log_ids': email_log_ids,
        'results': results,
    }


def _send_one(event, recipient):
    from email_app.models import EmailLog

    with transaction.atomic():
        existing = _existing_log(event, recipient)
        if existing is not None:
            return {
                'email': recipient.email,
                'source': recipient.source,
                'status': 'skipped',
                'reason': 'already_sent',
                'email_log_id': existing.pk,
            }

        email_service = EmailService()
        subject, body_html = email_service._render_template(
            EMAIL_TYPE,
            _render_user(recipient),
            _build_context(event),
        )
        full_html = render_to_string('email_app/base_email.html', {
            'subject': subject,
            'body_html': body_html,
        })
        ses_message_id = email_service._send_ses(
            recipient.email,
            subject,
            full_html,
            email_type=EMAIL_TYPE,
        )
        email_log = EmailLog.objects.create(
            event=event,
            user=recipient.user,
            recipient_email='' if recipient.user else recipient.email,
            email_type=EMAIL_TYPE,
            ses_message_id=ses_message_id,
        )

    logger.info(
        'Sent %s to %s for event "%s" (EmailLog %s, SES %s)',
        EMAIL_TYPE,
        recipient.email,
        event.title,
        email_log.pk,
        ses_message_id,
    )
    return {
        'email': recipient.email,
        'source': recipient.source,
        'status': 'sent',
        'email_log_id': email_log.pk,
    }


def _existing_log(event, recipient):
    from email_app.models import EmailLog

    query = Q(recipient_email__iexact=recipient.email)
    if recipient.user is not None:
        query |= Q(user=recipient.user)
    return (
        EmailLog.objects.select_for_update()
        .filter(event=event, email_type=EMAIL_TYPE)
        .filter(query)
        .first()
    )


def _render_user(recipient):
    if recipient.user is not None:
        return recipient.user
    return SimpleNamespace(
        email=recipient.email,
        first_name='',
        last_name='',
        email_verified=True,
    )


def _build_context(event):
    studio_event_url = f'{site_base_url()}{event.get_studio_edit_url()}'
    event_datetime = format_event_time_range(
        event.start_datetime,
        event.end_datetime,
        event.timezone or 'Europe/Berlin',
    )
    if event.published:
        publish_state = 'Uploaded and currently published'
        publish_copy = (
            'The event is currently published. Review the uploaded recording '
            'in Studio and confirm the member-facing page is still correct.'
        )
    else:
        publish_state = 'Ready for review/publishing'
        publish_copy = (
            'The event is not public yet. Review the upload, then publish '
            'and send the attendee follow-up when ready.'
        )

    return {
        'event_title': event.title,
        'event_datetime': event_datetime,
        'publish_state': publish_state,
        'publish_copy': publish_copy,
        'studio_event_url': studio_event_url,
        'zoom_recording_url': (event.recording_url or '').strip(),
    }


def _skipped_result(reason):
    return {
        'status': 'skipped',
        'recipient_count': 0,
        'attempted_recipient_count': 0,
        'skipped_reason': reason,
        'email_log_ids': [],
        'results': [],
    }
