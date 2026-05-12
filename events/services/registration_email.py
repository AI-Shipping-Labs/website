"""Send registration confirmation emails with .ics calendar attachments."""

import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import boto3
from django.conf import settings
from django.template.loader import render_to_string

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
            'event_datetime': event.formatted_start(),
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
