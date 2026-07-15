"""Send registration confirmation emails with .ics calendar attachments."""

import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import boto3
from django.conf import settings
from django.template.loader import render_to_string

from accounts.services.timezones import (
    build_timezone_account_url,
    build_timezone_email_line,
    format_user_datetime,
)
from email_app.services.email_classification import (
    EMAIL_KIND_TRANSACTIONAL,
    get_sender_for_kind,
)
from email_app.services.email_service import EmailService
from events.services.calendar_invite import (
    AUDIENCE_ATTENDEE,
    AUDIENCE_HOST,
    generate_ics,
)
from events.services.calendar_links import build_calendar_links
from events.services.cancel_token import generate_cancel_token
from events.services.host_registration import (
    build_host_management_links,
    is_host_registration,
)
from integrations.config import get_config, site_base_url

logger = logging.getLogger(__name__)


def send_registration_confirmation(registration):
    """Send a registration confirmation email with .ics attachment.

    Args:
        registration: EventRegistration model instance.

    Returns:
        EmailLog instance for the sent registration email.
    """
    user = registration.user
    event = registration.event
    is_host = is_host_registration(registration)
    if is_host:
        # Host delivery belongs to the current assignment/access generation,
        # even when the underlying attendee registration already existed.
        dedupe_key = (
            f'event-host-registration:{event.pk}:{user.pk}:'
            f'{event.host_access_version}'
        )
    else:
        # An attendee may cancel and later register for the same event again.
        # Scope idempotency to that concrete registration lifecycle so repeat
        # processing is safe without suppressing the new calendar REQUEST.
        dedupe_key = (
            f'event-registration:{event.pk}:{user.pk}:{registration.pk}'
        )

    from email_app.models import EmailLog
    existing_log = EmailLog.objects.filter(dedupe_key=dedupe_key).first()
    if existing_log is not None:
        return existing_log

    site_url = site_base_url()
    # Issue #1082: id-canonical join URL via ``Event.get_join_url``.
    join_url = f'{site_url}{event.get_join_url()}'
    cancel_token = generate_cancel_token(registration)
    cancel_url = (
        f'{site_url}/events/{event.slug}/cancel-registration?token={cancel_token}'
    )

    calendar_links = build_calendar_links(event)
    host_links = build_host_management_links(event) if is_host else {}

    # Render the email template
    email_service = EmailService()
    subject, body_html = email_service._render_template(
        'event_registration',
        user,
        {
            'event_title': event.title,
            # Issue #666: render in the recipient's preferred timezone (with
            # IANA name appended), falling back to literal UTC when no
            # valid preference is set. Replaces ``event.formatted_start()``
            # which hardcoded UTC and forced the recipient to convert.
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
            **host_links,
        },
    )

    # Wrap in base HTML email template
    full_html = render_to_string('email_app/base_email.html', {
        'subject': subject,
        'body_html': body_html,
    })

    # Generate .ics. Host auto-registration emails are host-only surfaces
    # with Studio management links, so they explicitly opt out of the
    # attendee /join URL as their primary calendar location/details link.
    ics_content = generate_ics(
        event,
        audience=AUDIENCE_HOST if is_host else AUDIENCE_ATTENDEE,
        attendee_email=user.email,
    )

    # Send raw email with attachment
    ses_message_id = _send_raw_email(
        to_email=user.email,
        subject=subject,
        html_body=full_html,
        ics_content=ics_content,
    )

    # Log the send
    email_log = EmailLog.objects.create(
        user=user,
        event=event,
        email_type='event_registration',
        ses_message_id=ses_message_id,
        dedupe_key=dedupe_key,
    )

    logger.info(
        'Sent registration confirmation to %s for event "%s" (SES: %s)',
        user.email, event.title, ses_message_id,
    )

    return email_log


def build_calendar_email_message(
    to_email,
    subject,
    html_body,
    ics_content,
    *,
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

    Container shape: ``multipart/alternative[ text/html, text/calendar;
    method=<METHOD> ]``. The HTML is listed first and the calendar last so
    non-calendar clients render the HTML body while calendar-aware clients
    pick the richer (last-listed) calendar alternative — a known
    Gmail-compatibility ordering nuance.
    """
    from_email = get_sender_for_kind(EMAIL_KIND_TRANSACTIONAL)
    normalized_method = method.upper()

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = from_email
    msg['To'] = to_email

    # HTML body first: the human-readable representation.
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
    subject,
    html_body,
    ics_content,
    method='REQUEST',
    filename='event.ics',
):
    """Send a raw email via SES with the .ics calendar as a body alternative.

    Issue #1088: the calendar is delivered as a ``multipart/alternative``
    sibling of the HTML body (``text/calendar; method=...``), not as a
    ``Content-Disposition: attachment`` part.

    Args:
        to_email: Recipient email address.
        subject: Email subject line.
        html_body: Full HTML email body.
        ics_content: .ics file content as bytes.
        method: iCalendar method (REQUEST or CANCEL).

    Returns:
        str: SES message ID.
    """
    # Issue #509: kill-switch for tests / local dev. Mirrors the gate in
    # EmailService._send_ses so neither boto3 client construction site can
    # reach a real SES account when SES_ENABLED is False. Returns a
    # recognisable synthetic message id so the caller's EmailLog row still
    # records the attempt.
    if not getattr(settings, 'SES_ENABLED', False):
        logger.info(
            'SES disabled - skipping registration email to %s (subject=%s)',
            to_email,
            subject,
        )
        return 'ses-disabled-noop'

    from_email = get_sender_for_kind(EMAIL_KIND_TRANSACTIONAL)
    msg = build_calendar_email_message(
        to_email,
        subject,
        html_body,
        ics_content,
        method=method,
        filename=filename,
    )

    client = boto3.client(
        'sesv2',
        region_name=get_config('AWS_SES_REGION', 'us-east-1'),
        aws_access_key_id=get_config('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=get_config('AWS_SECRET_ACCESS_KEY'),
    )
    response = client.send_email(
        FromEmailAddress=from_email,
        Destination={
            'ToAddresses': [to_email],
        },
        Content={
            'Raw': {
                'Data': msg.as_string(),
            },
        },
    )
    return response.get('MessageId', '')
