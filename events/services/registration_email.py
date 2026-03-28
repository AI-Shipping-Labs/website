"""Send registration confirmation emails with .ics calendar attachments."""

import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import boto3
from django.conf import settings
from django.template.loader import render_to_string

from email_app.services.email_service import EmailService
from events.services.calendar_invite import generate_ics

logger = logging.getLogger(__name__)


def send_registration_confirmation(registration):
    """Send a registration confirmation email with .ics attachment.

    Args:
        registration: EventRegistration model instance.

    Returns:
        EmailLog instance if sent, None if skipped (unsubscribed user).
    """
    user = registration.user
    event = registration.event

    # Don't send to unsubscribed users
    if getattr(user, 'unsubscribed', False):
        logger.info(
            'Skipping registration email to unsubscribed user %s',
            user.email,
        )
        return None

    site_url = getattr(settings, 'SITE_URL', 'https://aishippinglabs.com')
    join_url = f'{site_url}/events/{event.slug}/join'

    # Render the email template
    email_service = EmailService()
    subject, body_html = email_service._render_template(
        'event_registration',
        user,
        {
            'event_title': event.title,
            'event_datetime': event.formatted_start(),
            'join_url': join_url,
        },
    )

    # Wrap in base HTML email template
    unsubscribe_url = email_service._build_unsubscribe_url(user)
    full_html = render_to_string('email_app/base_email.html', {
        'subject': subject,
        'body_html': body_html,
        'unsubscribe_url': unsubscribe_url,
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
    from_email = getattr(
        settings, 'SES_FROM_EMAIL', 'community@aishippinglabs.com',
    )

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
        region_name=getattr(settings, 'AWS_SES_REGION', 'us-east-1'),
        aws_access_key_id=getattr(settings, 'AWS_ACCESS_KEY_ID', None),
        aws_secret_access_key=getattr(settings, 'AWS_SECRET_ACCESS_KEY', None),
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
