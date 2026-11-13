"""Event-audience workshop-ready broadcast service (issue #1118)."""

import logging

from django.db import transaction
from django.utils.html import strip_tags
from django.utils.text import Truncator

from email_app.relay_transactional import recipient_template_context
from email_app.services.relay_campaigns import submit_transient_list
from email_app.services.relay_templates import relay_template_subject
from events.services.workshop_ready_recipients import (
    relay_recipient_identity as _relay_recipient_identity,
)
from events.services.workshop_ready_recipients import (
    render_recipient_user as _render_user,
)
from events.services.workshop_ready_recipients import (
    resolve_workshop_ready_recipients,
)
from integrations.config import site_base_url
from notifications.models import EventReminderLog, Notification

logger = logging.getLogger(__name__)

EMAIL_TYPE = 'event_workshop_ready'
INTERVAL_WORKSHOP_READY = 'workshop_ready'


class WorkshopReadyNotReady(ValueError):
    """Raised when an event cannot send the workshop-ready broadcast yet."""


def assert_workshop_ready(event):
    """Return the linked published workshop or raise ``WorkshopReadyNotReady``."""
    workshop = getattr(event, 'workshop', None)
    if workshop is None:
        raise WorkshopReadyNotReady('A linked published workshop is required.')
    if workshop.status != 'published':
        raise WorkshopReadyNotReady('The linked workshop must be published first.')
    if not workshop.get_absolute_url():
        raise WorkshopReadyNotReady('The linked workshop needs a public URL.')
    return workshop


def notify_workshop_ready(event):
    """Send the workshop-ready broadcast to the event audience in one Relay request."""
    workshop = assert_workshop_ready(event)
    recipients = resolve_workshop_ready_recipients(event)

    results = []
    pending = []
    for recipient in recipients:
        if _already_sent(event, recipient):
            results.append(_recipient_result(recipient, 'already_sent'))
        else:
            pending.append(recipient)

    if not pending:
        return _build_summary(event, workshop, results)

    base_context = _build_context(event, workshop)
    members = []
    pending_meta = []
    for recipient in pending:
        user = _render_user(recipient)
        member_ctx = recipient_template_context(user, base_context)
        identity = _relay_recipient_identity(recipient)
        members.append({
            'email': recipient.email,
            'source_object_key': identity,
            'active': True,
            'metadata': {'user_name': member_ctx['user_name']},
        })
        pending_meta.append((
            recipient,
            identity,
            relay_template_subject(EMAIL_TYPE, member_ctx),
        ))

    try:
        response = submit_transient_list(
            list_key=f'{EMAIL_TYPE}:{event.pk}',
            name=f'Workshop ready: {event.title}',
            template_key=EMAIL_TYPE,
            idempotency_key=f'{EMAIL_TYPE}:{event.pk}',
            context=base_context,
            members=members,
            category=EMAIL_TYPE,
        )
    except Exception as exc:
        logger.exception(
            'Failed to submit %s batch for event %s',
            EMAIL_TYPE, event.pk,
        )
        for recipient in pending:
            results.append(
                _recipient_result(
                    recipient,
                    'failed',
                    reason=exc.__class__.__name__,
                )
            )
        return _build_summary(event, workshop, results)

    queued_ids = list((response or {}).get('queued_message_ids') or [])
    for idx, (recipient, identity, subject) in enumerate(pending_meta):
        msg_id = str(queued_ids[idx]) if idx < len(queued_ids) else f'{EMAIL_TYPE}:{event.pk}:{identity}'
        results.append(
            _record_sent(event, workshop, recipient, identity, subject, msg_id)
        )

    return _build_summary(event, workshop, results)


def _record_sent(event, workshop, recipient, identity, subject, message_id):
    dedupe_key = f'{EMAIL_TYPE}:{event.pk}:{identity}'
    with transaction.atomic():
        if _already_sent(event, recipient, for_update=True):
            return _recipient_result(recipient, 'already_sent')

        from email_app.models import EmailLog

        email_log, created = EmailLog.objects.get_or_create(
            dedupe_key=dedupe_key,
            defaults={
                'event': event,
                'user': recipient.user,
                'recipient_email': recipient.email,
                'email_type': EMAIL_TYPE,
                'subject': subject,
                'ses_message_id': message_id,
            },
        )
        if not created:
            return _recipient_result(recipient, 'already_sent')
        notification = None
        if recipient.user is not None:
            EventReminderLog.objects.get_or_create(
                event=event,
                user=recipient.user,
                interval=INTERVAL_WORKSHOP_READY,
            )
            notification = Notification.objects.create(
                user=recipient.user,
                title=f'Workshop ready: {workshop.title}',
                body=f'The workshop write-up for {event.title} is ready.',
                url=workshop.get_absolute_url(),
                notification_type='announcement',
            )

    return _recipient_result(
        recipient,
        'sent',
        email_log_id=email_log.pk,
        notification_id=notification.pk if notification else None,
    )


def _already_sent(event, recipient, *, for_update=False):
    if recipient.user is not None:
        queryset = EventReminderLog.objects.filter(
            event=event,
            user=recipient.user,
            interval=INTERVAL_WORKSHOP_READY,
        )
        if for_update:
            queryset = queryset.select_for_update()
        return queryset.exists()

    from email_app.models import EmailLog

    queryset = EmailLog.objects.filter(
        event=event,
        email_type=EMAIL_TYPE,
        recipient_email__iexact=recipient.email,
    )
    if for_update:
        queryset = queryset.select_for_update()
    return queryset.exists()


def _build_context(event, workshop):
    base_url = site_base_url()
    return {
        'event_title': event.title,
        'workshop_title': workshop.title,
        'workshop_url': f'{base_url}{workshop.get_absolute_url()}',
        'event_url': f'{base_url}{event.get_absolute_url()}',
        'workshop_description': _workshop_excerpt(workshop),
    }


def _workshop_excerpt(workshop):
    source = workshop.description_html or workshop.description or ''
    text = strip_tags(source).strip()
    return Truncator(text).chars(320) if text else ''


def _recipient_result(
    recipient,
    status,
    *,
    reason='',
    email_log_id=None,
    notification_id=None,
):
    result = {
        'email': recipient.email,
        'source': recipient.source,
        'status': status,
        'email_only': recipient.email_only,
    }
    if recipient.user is not None:
        result['user_id'] = recipient.user.pk
    if reason:
        result['reason'] = reason
    if email_log_id:
        result['email_log_id'] = email_log_id
    if notification_id:
        result['notification_id'] = notification_id
    return result


def _build_summary(event, workshop, results):
    emailed = sum(1 for item in results if item['status'] == 'sent')
    notified = sum(
        1 for item in results
        if item['status'] == 'sent' and not item['email_only']
    )
    already_sent = sum(1 for item in results if item['status'] == 'already_sent')
    failed = sum(1 for item in results if item['status'] == 'failed')
    return {
        'event': {
            'id': event.pk,
            'slug': event.slug,
            'title': event.title,
        },
        'workshop': {
            'id': workshop.pk,
            'slug': workshop.slug,
            'title': workshop.title,
            'url': workshop.get_absolute_url(),
        },
        'emailed': emailed,
        'notified': notified,
        'already_sent': already_sent,
        'failed': failed,
        'results': results,
    }
