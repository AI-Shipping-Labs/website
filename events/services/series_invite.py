"""Series subscriber calendar-invite sends (issue #869).

A series subscriber should receive the WHOLE series in their calendar
when they register, and that calendar should stay in sync as sessions
change. We model this as a multi-VEVENT ``.ics`` (one VEVENT per
occurrence), reusing the per-event ``build_vevent`` machinery so the
UID/SEQUENCE rules match the per-event invites and the platform feed.

Three send entry points, all best-effort and per-recipient isolated so
one failing recipient never blocks the rest, and all gated on the
``SES_ENABLED`` kill-switch (returning a synthetic id when disabled):

- ``send_series_registration_invite(user, series, events)`` — the
  confirmation a subscriber gets on registration, attaching a
  ``METHOD:REQUEST`` invite covering the occurrences they were enrolled
  in. Called from the series-register endpoint.
- ``send_series_update_to_subscribers(event)`` — when an occurrence's
  time changes or a new occurrence is added, every subscriber of that
  occurrence's series receives a refreshed ``METHOD:REQUEST`` invite
  covering the upcoming occurrences they are registered for (the
  changed/added occurrence carries its bumped SEQUENCE). One canonical
  email per subscriber avoids the conflicting single + series entries
  the intake warned about.
- ``send_series_cancellation_to_subscribers(event)`` — when an
  occurrence linked to a series flips to ``cancelled``, every subscriber
  registered for it receives a single-VEVENT ``METHOD:CANCEL`` for that
  occurrence (bumped SEQUENCE) so the entry leaves their calendar.

De-dup rule (documented contract): the canonical series update for a
time change is THIS series invite. The per-event reschedule notice
(``events.tasks.notify_reschedule``) and this series update share the
occurrence's UID, so a calendar merges them by UID rather than
double-booking — but to avoid spamming a series subscriber with two
emails for the same change, the Studio reschedule path sends the
per-event reschedule notice to one-off registrants only and the series
update to series subscribers (see ``studio.views.events``).
"""

import logging

from django.template.loader import render_to_string

from accounts.services.timezones import format_user_datetime
from content.access import (
    _resolve_required_level,
    can_access,
    get_required_tier_name,
)
from email_app.services.email_service import EmailService
from events.models import EventRegistration
from events.services.calendar_invite import generate_ics, generate_series_ics
from events.services.registration_email import _send_raw_email
from events.services.series_registration import _eligible_occurrences
from integrations.config import site_base_url

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


def _partial_access_note(user, series, accessible_events, site_url):
    """Build the plain-text upsell note for sessions ``user`` cannot access.

    The gated set is ``_eligible_occurrences(series)`` (upcoming, non-draft,
    non-cancelled) minus the accessible occurrences the invite already
    covers, compared by event id, kept where ``can_access`` is False. When
    there are none, returns ``''`` so the template's ``{% if partial_note %}``
    guard renders nothing.

    Names a single concrete upgrade target — the tier that unlocks the
    highest-gated session — so the CTA reads cleanly even when gated
    sessions span multiple tiers. Plain text only: the templates render
    this through Django autoescaping, so the ``/pricing`` link is a bare
    absolute URL.
    """
    accessible_ids = {event.id for event in accessible_events}
    gated = [
        event
        for event in _eligible_occurrences(series)
        if event.id not in accessible_ids and not can_access(user, event)
    ]
    if not gated:
        return ''

    count = len(gated)
    plural = '' if count == 1 else 's'
    is_are = 'is' if count == 1 else 'are'
    isnt_arent = "isn't" if count == 1 else "aren't"
    it_them = 'it' if count == 1 else 'them'

    highest_level = max(_resolve_required_level(event) for event in gated)
    tier_name = get_required_tier_name(highest_level)
    pricing_url = f'{site_url}/pricing'

    return (
        f'Heads up: {count} more session{plural} in this series '
        f'{is_are} available on the {tier_name} tier and {isnt_arent} '
        f'included above. Upgrade any time to add {it_them} to your '
        f'calendar: {pricing_url}'
    )


def _render_series_email(template_name, user, series, events, email_type):
    """Render the shared series email body for ``template_name``.

    Returns ``(subject, full_html)`` ready for ``_send_raw_email``.
    """
    site_url = site_base_url()
    series_url = f'{site_url}{series.get_absolute_url()}'
    partial_note = _partial_access_note(user, series, events, site_url)

    ordered = sorted(events, key=lambda e: e.start_datetime)
    lines = [
        f'- {event.title} — {format_user_datetime(event.start_datetime, user)}'
        for event in ordered
    ]
    occurrences_list = '\n'.join(lines)
    registered_count = len(ordered)

    email_service = EmailService()
    subject, body_html = email_service._render_template(
        template_name,
        user,
        {
            'series_name': series.name,
            'series_url': series_url,
            'registered_count': registered_count,
            'registered_count_plural': '' if registered_count == 1 else 's',
            'occurrences_list': occurrences_list,
            'partial_note': partial_note,
        },
    )
    full_html = render_to_string('email_app/base_email.html', {
        'subject': subject,
        'body_html': body_html,
    })
    return subject, full_html


def _log_send(user, email_type, ses_message_id):
    from email_app.models import EmailLog
    return EmailLog.objects.create(
        user=user,
        email_type=email_type,
        ses_message_id=ses_message_id,
    )


def send_series_registration_invite(user, series, events):
    """Send the registration confirmation with a multi-event ``.ics``.

    ``events`` is the list of occurrences the user was just enrolled in.
    Attaches a ``METHOD:REQUEST`` series invite covering exactly those
    occurrences so the whole series lands in their calendar from this one
    email. Sending nothing (empty ``events``) is the caller's job — this
    helper assumes at least one occurrence.

    Returns the ``EmailLog`` instance.
    """
    subject, full_html = _render_series_email(
        'series_registration', user, series, events, 'series_registration',
    )
    ics_content = generate_series_ics(events, method='REQUEST')
    ses_message_id = _send_raw_email(
        to_email=user.email,
        subject=subject,
        html_body=full_html,
        ics_content=ics_content,
        method='REQUEST',
    )
    email_log = _log_send(user, 'series_registration', ses_message_id)
    logger.info(
        'Sent series registration invite to %s for series "%s" '
        '(%d occurrences, SES: %s)',
        user.email, series.name, len(events), ses_message_id,
    )
    return email_log


def send_series_update_to_subscribers(event, user_ids=None):
    """Fan an updated series invite out to subscribers of ``event``'s series.

    Called when an occurrence's time changes (all subscribers) or a new
    occurrence is added and existing subscribers were auto-enrolled (only
    the enrolled users, passed via ``user_ids``). Each recipient receives
    a refreshed ``METHOD:REQUEST`` invite covering the upcoming occurrences
    they are registered for; the changed/added occurrence carries its
    current (already-bumped where relevant) ``ics_sequence`` so calendar
    clients UPDATE rather than duplicate by UID.

    Args:
        event: the changed/added occurrence (used to resolve the series).
        user_ids: optional iterable of subscriber user ids to target. When
            ``None``, every subscriber of the series is targeted.

    Best-effort and per-recipient isolated. Returns the number of
    subscribers a send was attempted for.
    """
    series = getattr(event, 'event_series', None)
    if series is None:
        return 0

    subscriber_user_ids = set(
        series.series_registrations.values_list('user_id', flat=True)
    )
    if user_ids is not None:
        subscriber_user_ids &= set(user_ids)
    if not subscriber_user_ids:
        return 0

    from accounts.models import User

    sent = 0
    for user in User.objects.filter(id__in=subscriber_user_ids):
        try:
            if user.unsubscribed:
                continue
            events = _subscriber_upcoming_events(user, series)
            if not events:
                continue
            subject, full_html = _render_series_email(
                'series_update', user, series, events, 'series_update',
            )
            ics_content = generate_series_ics(events, method='REQUEST')
            ses_message_id = _send_raw_email(
                to_email=user.email,
                subject=subject,
                html_body=full_html,
                ics_content=ics_content,
                method='REQUEST',
            )
            _log_send(user, 'series_update', ses_message_id)
            sent += 1
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


def send_series_cancellation_to_subscribers(event):
    """Send a ``METHOD:CANCEL`` for ``event`` to series subscribers.

    Called when an occurrence linked to a series flips to ``cancelled``
    via Studio. Each subscriber who holds an ``EventRegistration`` for
    the occurrence receives a single-VEVENT ``METHOD:CANCEL`` ``.ics``
    (bumped SEQUENCE) so the entry disappears from their calendar.

    Best-effort and per-recipient isolated. Returns the number of
    subscribers a cancel was attempted for.
    """
    series = getattr(event, 'event_series', None)
    if series is None:
        return 0

    subscriber_user_ids = set(
        series.series_registrations.values_list('user_id', flat=True)
    )
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

    ics_content = generate_ics(event, method='CANCEL')
    sent = 0
    for user in User.objects.filter(id__in=registered_user_ids):
        try:
            if user.unsubscribed:
                continue
            subject, full_html = _render_series_email(
                'series_cancellation', user, series, [event],
                'series_cancellation',
            )
            ses_message_id = _send_raw_email(
                to_email=user.email,
                subject=subject,
                html_body=full_html,
                ics_content=ics_content,
                method='CANCEL',
            )
            _log_send(user, 'series_cancellation', ses_message_id)
            sent += 1
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
