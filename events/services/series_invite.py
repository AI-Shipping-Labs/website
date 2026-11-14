"""Relay calendar invites for series registration, update, and cancellation."""

import logging
from hashlib import sha256

from content.access import can_access
from email_app.relay_transactional import (
    calendar_alternative,
    recipient_template_context,
)
from email_app.services.email_classification import (
    get_calendar_sender,
    relay_sender_id,
)
from events.models import EventRegistration
from events.services.calendar_invite import generate_ics, generate_series_ics
from events.services.calendar_lifecycle import user_has_permanent_bounce
from events.services.registration_email import _send_calendar_email
from events.services.relay_event_notice import (
    build_relay_recipient,
    submit_event_notice_campaign,
)
from events.services.series_email_context import (
    build_series_subject_context,
)

logger = logging.getLogger(__name__)


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
    """Send one multi-event registration invite for a series lifecycle.

    By design this is a transactional single-recipient send, not a fan-out:
    it fires once for the one user who just registered, through Relay's
    transactional API with an idempotency key (Relay owns delivery and
    retries). Update/cancellation fan-outs use one Relay campaign instead.
    """
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
    message_id = _send_calendar_email(
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
    """Submit one Relay campaign for all eligible series-update subscribers."""
    series = getattr(event, 'event_series', None)
    if series is None:
        return 0

    old_start = _parse_old_start(old_start_iso)
    subscriber_user_ids = _subscriber_ids(series, user_ids)
    if not subscriber_user_ids:
        return 0

    from accounts.models import User

    prepared = []
    for user in User.objects.filter(id__in=subscriber_user_ids):
        item = _prepare_series_update_item(event, user, series, old_start)
        if item is not None:
            prepared.append(item)

    if not prepared:
        return 0

    external_key = _series_update_key(series, event, user_ids)
    submit_event_notice_campaign(
        external_key=external_key,
        subject=prepared[0]['subject'],
        template_key='series_update',
        recipients=[item['member'] for item in prepared],
        sender_id=relay_sender_id(get_calendar_sender()),
        metadata={
            'series_id': series.pk,
            'event_id': event.pk,
            'email_type': 'series_update',
        },
    )
    for item in prepared:
        _log_send(item['user'], 'series_update', item['subject'], external_key, item['dedupe_key'])

    logger.info(
        'Series update campaign %s for series "%s" occurrence "%s": %d subscribers',
        external_key, series.slug, event.slug, len(prepared),
    )
    return len(prepared)


def _series_update_key(series, event, user_ids):
    base = f'series-update-{series.pk}-event-{event.pk}-seq-{event.ics_sequence}'
    if user_ids is None:
        return base
    raw = ','.join(str(uid) for uid in sorted(set(user_ids)))
    digest = sha256(raw.encode('utf-8')).hexdigest()[:8]
    return f'{base}-u{digest}'


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


def _prepare_series_update_item(event, user, series, old_start):
    if user_has_permanent_bounce(user):
        return None
    events = _subscriber_upcoming_events(user, series)
    if not events:
        return None
    dedupe_key = (
        f'series-update:{series.pk}:{user.pk}:{event.pk}:{event.ics_sequence}'
    )
    return _build_series_item(
        user,
        series,
        events,
        template_key='series_update',
        method='REQUEST',
        dedupe_key=dedupe_key,
        changed_event=event,
        old_start=old_start,
    )


def _build_series_item(
    user,
    series,
    events,
    *,
    template_key,
    method,
    dedupe_key,
    changed_event=None,
    old_start=None,
):
    subject, context = build_series_subject_context(
        template_key,
        user,
        series,
        events,
        changed_event=changed_event,
        old_start=old_start,
    )
    full_ctx = recipient_template_context(user, context)
    ics_content = _series_ics(events, user.email, method)
    calendar = calendar_alternative(ics_content, method=method, name='event.ics')
    member = build_relay_recipient(user.email, f'user-{user.pk}', full_ctx, calendar=calendar)
    return {'user': user, 'subject': subject, 'dedupe_key': dedupe_key, 'member': member}


def send_series_cancellation_to_subscribers(event):
    """Submit one Relay campaign for registered series-cancellation subscribers."""
    series = getattr(event, 'event_series', None)
    if series is None:
        return 0

    subscriber_user_ids = _subscriber_ids(series, None)
    if not subscriber_user_ids:
        return 0

    registered_user_ids = set(
        EventRegistration.objects.filter(
            event=event, user_id__in=subscriber_user_ids,
        ).values_list('user_id', flat=True)
    )
    if not registered_user_ids:
        return 0

    from accounts.models import User

    prepared = []
    for user in User.objects.filter(id__in=registered_user_ids):
        if user_has_permanent_bounce(user):
            continue
        dedupe_key = f'series-cancellation:{series.pk}:{user.pk}:{event.pk}:{event.ics_sequence}'
        prepared.append(_build_series_item(
            user, series, [event], template_key='series_cancellation', method='CANCEL', dedupe_key=dedupe_key,
        ))

    if not prepared:
        return 0

    external_key = f'series-cancellation-{series.pk}-event-{event.pk}-seq-{event.ics_sequence}'
    submit_event_notice_campaign(
        external_key=external_key,
        subject=prepared[0]['subject'],
        template_key='series_cancellation',
        recipients=[item['member'] for item in prepared],
        sender_id=relay_sender_id(get_calendar_sender()),
        metadata={
            'series_id': series.pk,
            'event_id': event.pk,
            'email_type': 'series_cancellation',
        },
    )
    for item in prepared:
        _log_send(item['user'], 'series_cancellation', item['subject'], external_key, item['dedupe_key'])

    logger.info(
        'Series cancellation campaign %s for occurrence "%s": %d subscribers',
        external_key, event.slug, len(prepared),
    )
    return len(prepared)
