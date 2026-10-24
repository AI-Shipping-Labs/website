"""Send registration confirmation emails with .ics calendar attachments."""

import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from accounts.services.timezones import (
    build_timezone_account_url,
    build_timezone_email_line,
    format_user_datetime,
)
from email_app.relay_transactional import (
    calendar_alternative,
    recipient_template_context,
    send_relay_transactional,
)
from email_app.services.email_classification import (
    EMAIL_KIND_TRANSACTIONAL,
    get_sender_for_kind,
)
from email_app.services.email_rendering import (
    render_template_parts,
)
from events.services.calendar_invite import (
    AUDIENCE_ATTENDEE,
    generate_ics,
)
from events.services.calendar_links import build_calendar_links
from events.services.cancel_token import generate_cancel_token
from events.services.host_registration import is_host_registration
from integrations.config import site_base_url

logger = logging.getLogger(__name__)


def send_registration_confirmation(registration):
    """Send one stable-key registration confirmation through Relay."""
    user = registration.user
    is_host = is_host_registration(registration)
    dedupe_key = _registration_dedupe_key(registration, is_host)
    from email_app.models import EmailLog

    existing_log = EmailLog.objects.filter(dedupe_key=dedupe_key).first()
    if existing_log is not None:
        return existing_log
    template_context = _registration_context(registration, is_host)
    subject, _body, _html, _footer = render_template_parts(
        'event_registration', user, template_context,
    )
    message_id = _send_registration_calendar(
        registration, template_context, dedupe_key,
    )
    return _create_registration_log(registration, subject, message_id, dedupe_key)


def _registration_dedupe_key(registration, is_host):
    event = registration.event
    user = registration.user
    if is_host:
        return (
            f'event-host-registration:{event.pk}:{user.pk}:'
            f'{event.host_access_version}'
        )
    return f'event-registration:{event.pk}:{user.pk}:{registration.pk}'


def _registration_context(registration, is_host):
    user = registration.user
    event = registration.event
    site_url = site_base_url()
    join_url = f'{site_url}{event.get_join_url()}'
    cancel_token = generate_cancel_token(registration)
    cancel_url = (
        f'{site_url}/events/{event.slug}/cancel-registration?token={cancel_token}'
    )
    calendar_links = build_calendar_links(event)
    return {
        'event_title': event.title,
        'event_datetime': format_user_datetime(event.start_datetime, user),
        'timezone_help': build_timezone_email_line(
            user, build_timezone_account_url(site_url),
        ),
        'join_url': join_url,
        'cancel_url': cancel_url,
        'google_calendar_url': calendar_links['google'],
        'outlook_calendar_url': calendar_links['outlook'],
        'office365_calendar_url': calendar_links['office365'],
        'is_host_registration': is_host,
    }


def _send_registration_calendar(registration, template_context, dedupe_key):
    event = registration.event
    user = registration.user
    ics_content = generate_ics(
        event,
        audience=AUDIENCE_ATTENDEE,
        attendee_email=user.email,
    )
    return _send_raw_email(
        to_email=user.email,
        template_key='event_registration',
        context=recipient_template_context(user, template_context),
        ics_content=ics_content,
        idempotency_key=dedupe_key,
        safe_correlation=f'event={event.pk} user={user.pk}',
    )


def _create_registration_log(registration, subject, message_id, dedupe_key):
    from email_app.models import EmailLog

    user = registration.user
    event = registration.event
    email_log, _ = EmailLog.objects.get_or_create(
        dedupe_key=dedupe_key,
        defaults={
            'user': user,
            'event': event,
            'recipient_email': user.email,
            'email_type': 'event_registration',
            'subject': subject,
            'ses_message_id': message_id,
        },
    )
    logger.info(
        'Sent registration confirmation to %s for event "%s" (Relay: %s)',
        registration.user.email,
        registration.event.title,
        message_id,
    )
    return email_log


def build_calendar_email_message(
    to_email,
    subject,
    html_body,
    ics_content,
    *,
    text_body,
    method='REQUEST',
    filename='event.ics',
):
    """Build the raw MIME message used for calendar lifecycle emails.

    Issue #1088: the ``text/calendar`` part is delivered as a
    ``multipart/alternative`` SIBLING of the HTML body rather than a
    ``Content-Disposition: attachment`` part. Gmail/Google Calendar only
    fire their heuristic "this email updates your calendar / merge by UID"
    handling when the invite arrives as an alternative body representation
    of an ``METHOD:REQUEST``/``CANCEL`` message, not as a named attachment.
    This is also the RFC-correct delivery for itip calendar messages.

    Container shape: ``multipart/alternative[ text/plain, text/html,
    text/calendar; method=<METHOD> ]``. The calendar remains last so
    calendar-aware clients pick the richest alternative.
    """
    from_email = get_sender_for_kind(EMAIL_KIND_TRANSACTIONAL)
    normalized_method = method.upper()

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = from_email
    msg['To'] = to_email

    # Human-readable alternatives first, with plain text as the universal
    # fallback and HTML as the richer representation.
    msg.attach(MIMEText(text_body, 'plain', 'utf-8'))
    body = MIMEText(html_body, 'html', 'utf-8')
    msg.attach(body)

    ics_str = (
        ics_content.decode('utf-8')
        if isinstance(ics_content, bytes)
        else ics_content
    )
    # Calendar last: the "richest" alternative. Carries the itip ``method``
    # param on its Content-Type (preserved unchanged). Deliberately NO
    # ``Content-Disposition: attachment`` so Gmail treats it as a calendar
    # update to merge by UID, not a downloadable file. We keep a ``name``
    # hint on the Content-Type for clients that still offer a download.
    cal_part = MIMEText(ics_str, 'calendar', 'utf-8')
    cal_part.set_param('method', normalized_method, header='Content-Type')
    cal_part.set_param('name', filename, header='Content-Type')
    msg.attach(cal_part)
    return msg


def _send_raw_email(
    to_email,
    template_key,
    context,
    ics_content,
    *,
    idempotency_key,
    safe_correlation,
    method='REQUEST',
    filename='event.ics',
):
    """Send a Relay template with a calendar body alternative."""
    calendar = calendar_alternative(
        ics_content,
        method=method,
        name=filename,
    )
    result = send_relay_transactional(
        to_email,
        template_key,
        context,
        idempotency_key=idempotency_key,
        safe_correlation=safe_correlation,
        calendar=calendar,
    )
    return result.message_id
