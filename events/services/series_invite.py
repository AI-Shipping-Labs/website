"""Relay calendar invites for series registration, update, and cancellation."""

import logging

from content.access import can_access
from email_app.relay_transactional import recipient_template_context
from events.models import EventRegistration
from events.services.calendar_invite import generate_ics, generate_series_ics
from events.services.calendar_lifecycle import user_has_permanent_bounce
from events.services.registration_email import _send_raw_email
from events.services.series_email_context import (
    build_series_subject_context,
    render_series_email,
    render_series_email_parts,
)

logger = logging.getLogger(__name__)
_render_series_email = render_series_email
_render_series_email_parts = render_series_email_parts


def _subscriber_upcoming_events(user, series):
    """Return the upcoming series occurrences ``user`` is registered for.

    The invite must reflect what the subscriber actually has on their
    calendar: the upcoming, non-cancelled occurrences of ``series`` that
    they hold an ``EventRegistration`` for and can still access. Ordered
    chronologically so the rendered list reads in order.
    """
    registered_event_ids = set(
        EventRegistration.objects.filter(
            user=user, event__event_series=series,
        ).values_list('event_id', flat=True)
    )
    if not registered_event_ids:
        return []

    candidates = series.events.filter(id__in=registered_event_ids).exclude(
        status__in=('draft', 'cancelled'),
    )
    events = [
        event
        for event in candidates
        if event.is_upcoming and can_access(user, event)
    ]
    events.sort(key=lambda e: e.start_datetime)
    return events


def _log_send(user, email_type, subject, ses_message_id, dedupe_key):
    from email_app.models import EmailLog
    email_log, _ = EmailLog.objects.get_or_create(
        dedupe_key=dedupe_key,
        defaults={
            'user': user,
            'recipient_email': user.email,
            'email_type': email_type,
            'subject': subject,
            'ses_message_id': ses_message_id,
        },
    )
    return email_log


def send_series_registration_invite(user, series, events):
    """Send one multi-event registration invite for a series lifecycle."""
    registration = series.series_registrations.only('pk').get(user=user)
    dedupe_key = (
        f'series-registration:{series.pk}:{user.pk}:{registration.pk}'
    )
    email_log = _send_series_calendar(
        user,
        series,
        events,
        template_key='series_registration',
        email_type='series_registration',
        method='REQUEST',
        dedupe_key=dedupe_key,
    )
    logger.info(
        'Sent series registration invite to %s for series "%s" '
        '(%d occurrences, Relay: %s)',
        user.email, series.name, len(events), email_log.ses_message_id,
    )
    return email_log


def _send_series_calendar(
    user,
    series,
    events,
    *,
    template_key,
    email_type,
    method,
    dedupe_key,
    changed_event=None, old_start=None,
):
    subject, context = build_series_subject_context(
        template_key,
        user,
        series,
        events,
        changed_event=changed_event,
        old_start=old_start,
    )
    ics_content = _series_ics(events, user.email, method)
    message_id = _send_raw_email(
        to_email=user.email,
        template_key=template_key,
        context=recipient_template_context(user, context),
        ics_content=ics_content,
        idempotency_key=dedupe_key,
        safe_correlation=f'series={series.pk} user={user.pk}',
        method=method,
    )
    return _log_send(user, email_type, subject, message_id, dedupe_key)


def _series_ics(events, attendee_email, method):
    if len(events) == 1 and method == 'CANCEL':
        return generate_ics(
            events[0],
            method=method,
            attendee_email=attendee_email,
        )
    return generate_series_ics(
        events,
        method=method,
        attendee_email=attendee_email,
    )


def send_series_update_to_subscribers(event, user_ids=None, old_start_iso=None):
    """Fan one stable-key REQUEST invite to each eligible subscriber."""
    series = getattr(event, 'event_series', None)
    if series is None:
        return 0

    old_start = _parse_old_start(old_start_iso)
    subscriber_user_ids = _subscriber_ids(series, user_ids)
    if not subscriber_user_ids:
        return 0

    from accounts.models import User

    sent = 0
    for user in User.objects.filter(id__in=subscriber_user_ids):
        try:
            sent += _send_series_update_one(event, user, series, old_start)
        except Exception:
            logger.exception(
                'Failed to send series update to user %s for series "%s"',
                getattr(user, 'email', user.pk), series.slug,
            )
    logger.info(
        'Series update fan-out for series "%s" occurrence "%s": '
        '%d subscribers notified',
        series.slug, event.slug, sent,
    )
    return sent


def _parse_old_start(old_start_iso):
    if old_start_iso is None:
        return None
    from datetime import UTC, datetime

    old_start = datetime.fromisoformat(old_start_iso)
    if old_start.tzinfo is None:
        old_start = old_start.replace(tzinfo=UTC)
    return old_start


def _subscriber_ids(series, user_ids):
    subscriber_ids = set(
        series.series_registrations.values_list('user_id', flat=True),
    )
    if user_ids is not None:
        subscriber_ids &= set(user_ids)
    return subscriber_ids


def _send_series_update_one(event, user, series, old_start):
    if user_has_permanent_bounce(user):
        return 0
    events = _subscriber_upcoming_events(user, series)
    if not events:
        return 0
    dedupe_key = (
        f'series-update:{series.pk}:{user.pk}:{event.pk}:{event.ics_sequence}'
    )
    _send_series_calendar(
        user,
        series,
        events,
        template_key='series_update',
        email_type='series_update',
        method='REQUEST',
        dedupe_key=dedupe_key,
        changed_event=event,
        old_start=old_start,
    )
    return 1


def send_series_cancellation_to_subscribers(event):
    """Fan one stable-key CANCEL invite to registered series subscribers."""
    series = getattr(event, 'event_series', None)
    if series is None:
        return 0

    subscriber_user_ids = _subscriber_ids(series, None)
    if not subscriber_user_ids:
        return 0

    # Only subscribers who actually had this occurrence on their calendar
    # (held an EventRegistration) need the cancel.
    registered_user_ids = set(
        EventRegistration.objects.filter(
            event=event, user_id__in=subscriber_user_ids,
        ).values_list('user_id', flat=True)
    )
    if not registered_user_ids:
        return 0

    from accounts.models import User

    sent = 0
    for user in User.objects.filter(id__in=registered_user_ids):
        try:
            sent += _send_series_cancellation_one(event, user, series)
        except Exception:
            logger.exception(
                'Failed to send series cancellation to user %s for '
                'occurrence "%s"',
                getattr(user, 'email', user.pk), event.slug,
            )
    logger.info(
        'Series cancellation fan-out for occurrence "%s": '
        '%d subscribers notified',
        event.slug, sent,
    )
    return sent


def _send_series_cancellation_one(event, user, series):
    if user_has_permanent_bounce(user):
        return 0
    dedupe_key = (
        f'series-cancellation:{series.pk}:{user.pk}:'
        f'{event.pk}:{event.ics_sequence}'
    )
    _send_series_calendar(
        user,
        series,
        [event],
        template_key='series_cancellation',
        email_type='series_cancellation',
        method='CANCEL',
        dedupe_key=dedupe_key,
    )
    return 1
