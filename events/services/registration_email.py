"""Send registration confirmation emails with .ics calendar attachments."""

import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import boto3
from django.conf import settings
from django.template.loader import render_to_string

from accounts.services.timezones import format_user_datetime
from email_app.services.email_classification import (
    EMAIL_KIND_TRANSACTIONAL,
    get_sender_for_kind,
)
from email_app.services.email_service import EmailService
from events.services.calendar_invite import generate_ics
from events.services.calendar_links import build_calendar_links
from events.services.cancel_token import generate_cancel_token
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

    site_url = site_base_url()
    join_url = f'{site_url}/events/{event.slug}/join'
    cancel_token = generate_cancel_token(registration)
    cancel_url = (
        f'{site_url}/events/{event.slug}/cancel-registration?token={cancel_token}'
    )

    calendar_links = build_calendar_links(event)

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
            'join_url': join_url,
            'cancel_url': cancel_url,
            'google_calendar_url': calendar_links['google'],
            'outlook_calendar_url': calendar_links['outlook'],
            'office365_calendar_url': calendar_links['office365'],
        },
    )

    # Wrap in base HTML email template
    full_html = render_to_string('email_app/base_email.html', {
        'subject': subject,
        'body_html': body_html,
    })

    # Generate .ics
    ics_content = generate_ics(event)

    # Send raw email with attachment
    ses_message_id = _send_raw_email(
        to_email=user.email,
        subject=subject,
        html_body=full_html,
        ics_content=ics_content,
    )

    # Log the send
    from email_app.models import EmailLog
    email_log = EmailLog.objects.create(
        user=user,
        email_type='event_registration',
        ses_message_id=ses_message_id,
    )

    logger.info(
        'Sent registration confirmation to %s for event "%s" (SES: %s)',
        user.email, event.title, ses_message_id,
    )

    return email_log


def send_series_registration_confirmation(user, series, registered_events):
    """Send ONE summary email for a whole-series registration (issue #857).

    Rather than sending N per-event confirmations (one per fanned-out
    occurrence), we send a single email summarising the occurrences the
    user was enrolled in. ``registered_events`` is the list of ``Event``
    rows that newly got an ``EventRegistration`` from the fan-out.

    A series registration that enrolled the user in zero new occurrences
    (e.g. they were already registered for everything, or every remaining
    occurrence requires a higher tier) does not send an email.

    Returns the ``EmailLog`` instance, or ``None`` when nothing was sent.
    """
    if not registered_events:
        return None

    site_url = site_base_url()
    series_url = f'{site_url}{series.get_absolute_url()}'

    # Order the summary by start time so the email reads chronologically.
    ordered = sorted(registered_events, key=lambda e: e.start_datetime)
    lines = []
    for event in ordered:
        when = format_user_datetime(event.start_datetime, user)
        lines.append(f'- {event.title} — {when}')
    occurrences_list = '\n'.join(lines)

    registered_count = len(ordered)

    email_service = EmailService()
    subject, body_html = email_service._render_template(
        'series_registration',
        user,
        {
            'series_name': series.name,
            'series_url': series_url,
            'registered_count': registered_count,
            'registered_count_plural': '' if registered_count == 1 else 's',
            'occurrences_list': occurrences_list,
            # Partial-enrollment note is surfaced on the series page; the
            # email keeps it blank so a clean full-series enrollment reads
            # without a dangling note.
            'partial_note': '',
        },
    )

    full_html = render_to_string('email_app/base_email.html', {
        'subject': subject,
        'body_html': body_html,
    })

    ses_message_id = _send_raw_email_no_ics(
        to_email=user.email,
        subject=subject,
        html_body=full_html,
    )

    from email_app.models import EmailLog
    email_log = EmailLog.objects.create(
        user=user,
        email_type='series_registration',
        ses_message_id=ses_message_id,
    )

    logger.info(
        'Sent series registration confirmation to %s for series "%s" '
        '(%d occurrences, SES: %s)',
        user.email, series.name, registered_count, ses_message_id,
    )
    return email_log


def _send_raw_email_no_ics(to_email, subject, html_body):
    """Send a plain HTML email via SES (no calendar attachment).

    The series summary email carries no single ``.ics`` — each fanned-out
    occurrence is its own calendar entry reachable from the dashboard and
    the per-event ``.ics`` download. Mirrors the SES kill-switch in
    ``_send_raw_email``.
    """
    if not getattr(settings, 'SES_ENABLED', False):
        logger.info(
            'SES disabled - skipping series registration email to %s '
            '(subject=%s)',
            to_email,
            subject,
        )
        return 'ses-disabled-noop'

    from_email = get_sender_for_kind(EMAIL_KIND_TRANSACTIONAL)

    msg = MIMEMultipart('mixed')
    msg['Subject'] = subject
    msg['From'] = from_email
    msg['To'] = to_email
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    client = boto3.client(
        'sesv2',
        region_name=get_config('AWS_SES_REGION', 'us-east-1'),
        aws_access_key_id=get_config('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=get_config('AWS_SECRET_ACCESS_KEY'),
    )
    response = client.send_email(
        FromEmailAddress=from_email,
        Destination={'ToAddresses': [to_email]},
        Content={'Raw': {'Data': msg.as_string()}},
    )
    return response.get('MessageId', '')


def _send_raw_email(to_email, subject, html_body, ics_content, method='REQUEST'):
    """Send a raw email via SES with .ics calendar attachment.

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

    msg = MIMEMultipart('mixed')
    msg['Subject'] = subject
    msg['From'] = from_email
    msg['To'] = to_email

    # HTML body
    body = MIMEText(html_body, 'html', 'utf-8')
    msg.attach(body)

    # .ics attachment
    ics_str = ics_content.decode('utf-8') if isinstance(ics_content, bytes) else ics_content
    cal_part = MIMEText(ics_str, 'calendar; method=' + method, 'utf-8')
    cal_part.add_header('Content-Disposition', 'attachment', filename='event.ics')
    msg.attach(cal_part)

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
