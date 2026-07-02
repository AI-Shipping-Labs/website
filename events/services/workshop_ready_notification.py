"""Event-audience workshop-ready broadcast service (issue #1118)."""

import logging
from dataclasses import dataclass
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.utils.text import Truncator

from accounts.services.email_resolution import normalize_email, resolve_user_by_email
from email_app.services.email_service import EmailService
from integrations.config import site_base_url
from notifications.models import EventReminderLog, Notification

logger = logging.getLogger(__name__)
User = get_user_model()

EMAIL_TYPE = 'event_workshop_ready'
INTERVAL_WORKSHOP_READY = 'workshop_ready'


class WorkshopReadyNotReady(ValueError):
    """Raised when an event cannot send the workshop-ready broadcast yet."""


@dataclass(frozen=True)
class WorkshopReadyRecipient:
    email: str
    source: str
    user: object | None = None

    @property
    def key(self):
        if self.user is not None:
            return f'user:{self.user.pk}'
        return f'email:{normalize_email(self.email)}'

    @property
    def email_only(self):
        return self.user is None


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


def resolve_workshop_ready_recipients(event):
    """Resolve active registrants plus event hosts, deduped by user/email."""
    recipients = []
    seen = set()

    def add(email, source, user=None):
        email = (email or '').strip()
        if not email and user is not None:
            email = (user.email or '').strip()
        if not email:
            return
        if user is None:
            user = resolve_user_by_email(email)
        if user is not None:
            if not user.is_active:
                return
            email = user.email
        elif _inactive_primary_user_exists(email):
            return
        elif not _is_deliverable_email(email):
            return

        recipient = WorkshopReadyRecipient(email=email, source=source, user=user)
        if recipient.key in seen:
            return
        seen.add(recipient.key)
        recipients.append(recipient)

    registrations = (
        event.registrations
        .select_related('user')
        .filter(user__is_active=True)
        .order_by('registered_at')
    )
    for registration in registrations:
        add(registration.user.email, 'registration', registration.user)

    add(getattr(event, 'host_email', ''), 'event.host_email')

    host_links = (
        event.event_host_links.select_related('host')
        .filter(host__is_active=True)
        .order_by('position')
    )
    for link in host_links:
        add(link.host.email, 'event.hosts')

    return recipients


def notify_workshop_ready(event):
    """Send the workshop-ready broadcast to the event audience.

    The service is synchronous because both Studio and the staff API need an
    immediate per-recipient report. Individual recipient failures are captured
    and do not stop the rest of the fan-out.
    """
    workshop = assert_workshop_ready(event)
    recipients = resolve_workshop_ready_recipients(event)

    results = []
    for recipient in recipients:
        try:
            result = _send_one(event, workshop, recipient)
        except Exception as exc:
            logger.exception(
                'Failed to send %s for event %s to %s',
                EMAIL_TYPE, event.pk, recipient.email,
            )
            result = _recipient_result(
                recipient,
                'failed',
                reason=exc.__class__.__name__,
            )
        results.append(result)

    return _build_summary(event, workshop, results)


def _send_one(event, workshop, recipient):
    if _already_sent(event, recipient):
        return _recipient_result(recipient, 'already_sent')

    email_service = EmailService()
    subject, body_html = email_service._render_template(
        EMAIL_TYPE,
        _render_user(recipient),
        _build_context(event, workshop),
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

    with transaction.atomic():
        # Re-check after SES in case another operator sent while this request
        # was rendering. If so, do not create duplicate local rows.
        if _already_sent(event, recipient, for_update=True):
            return _recipient_result(recipient, 'already_sent')

        from email_app.models import EmailLog

        email_log = EmailLog.objects.create(
            event=event,
            user=recipient.user,
            recipient_email='' if recipient.user else recipient.email,
            email_type=EMAIL_TYPE,
            ses_message_id=ses_message_id,
        )
        notification = None
        if recipient.user is not None:
            EventReminderLog.objects.create(
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


def _render_user(recipient):
    if recipient.user is not None:
        return recipient.user
    return SimpleNamespace(
        email=recipient.email,
        first_name='',
        last_name='',
        email_verified=True,
    )


def _is_deliverable_email(email):
    try:
        validate_email(email)
    except ValidationError:
        return False
    return True


def _inactive_primary_user_exists(email):
    normalized = normalize_email(email)
    if not normalized:
        return False
    return User.objects.filter(email__iexact=normalized, is_active=False).exists()


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
