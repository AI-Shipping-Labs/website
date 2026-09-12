"""Direct SES v2 transport for the paths that keep a site-owned send.

Extracted from ``EmailService._send_ses`` (A1.2 slice 3). The calendar
lifecycle emails build raw MIME messages with ``.ics`` calendar parts the
package ``send`` cannot model, and the event operator notifications need a
synchronous per-recipient outcome plus ``EmailLog`` rows for recipients
without a user row (which the package recorder deliberately does not
write). Both keep this transport until the package grows the missing seams
or Phase 6 moves the site to Relay.

The SES_ENABLED kill-switch (issue #509) short-circuits BEFORE the boto3
client is built, so no real network call is ever made when sending is
disabled; the synthetic ``ses-disabled-noop`` message id lets callers'
``EmailLog`` rows still record the attempt.
"""

import logging
import re

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings

from email_app.services.email_classification import (
    WELCOME_EMAIL_TYPES,
    get_sender_for_email_type,
)
from email_app.services.email_errors import (
    EmailServiceError,
    EmailTransportOutcomeUnknown,
)
from integrations.config import (
    get_config,
    validate_email_config_value,
)

logger = logging.getLogger(__name__)

# Issue #950: welcome emails carry a Reply-To pointing at a monitored,
# team-forwarded inbox so a reply to a welcome reaches a human instead of
# bouncing off the noreply/welcome send-only mailbox. The address is
# editable from Studio via this IntegrationSetting key; an empty value
# omits the Reply-To header entirely.
WELCOME_REPLY_TO_KEY = "SES_WELCOME_REPLY_TO_EMAIL"
DEFAULT_WELCOME_REPLY_TO_EMAIL = "welcome@aishippinglabs.com"


def build_ses_client():
    """Build the SES v2 client from the runtime configuration."""
    return boto3.client(
        "sesv2",
        region_name=get_config("AWS_SES_REGION", "us-east-1"),
        aws_access_key_id=get_config("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=get_config("AWS_SECRET_ACCESS_KEY"),
    )


def normalize_cc(cc):
    """Normalize a ``cc`` argument into a list of non-empty email strings.

    Accepts ``None``, a single string, or any iterable of strings.
    Empty / whitespace-only entries are filtered out. Returns ``[]`` when
    nothing usable remains — callers MUST check truthiness before
    putting ``CcAddresses`` on the SES payload (SES rejects an empty
    list with a validation error).
    """
    if not cc:
        return []
    if isinstance(cc, str):
        cc = [cc]
    return [c.strip() for c in cc if c and c.strip()]


def build_unsubscribe_headers(unsubscribe_url):
    """Build SES-compatible one-click unsubscribe headers."""
    if not unsubscribe_url:
        return []

    header_value_parts = [f"<{unsubscribe_url}>"]
    unsubscribe_mailto = get_config("SES_UNSUBSCRIBE_EMAIL", "").strip()
    if unsubscribe_mailto:
        header_value_parts.append(f"<mailto:{unsubscribe_mailto}>")

    return [
        {
            "Name": "List-Unsubscribe",
            "Value": ", ".join(header_value_parts),
        },
        {
            "Name": "List-Unsubscribe-Post",
            "Value": "List-Unsubscribe=One-Click",
        },
    ]


def send_ses_email(
    to_email,
    subject,
    html_body,
    *,
    text_body,
    email_type=None,
    unsubscribe_url=None,
    cc=None,
    bcc=None,
    redact_recipient=False,
):
    """Send an email via Amazon SES v2 SendEmail API.

    Args:
        to_email: Recipient email address.
        subject: Email subject line.
        html_body: Full HTML email body.
        text_body: Full plain-text email body derived from the same source.
        email_type: The email template name (e.g. 'welcome',
            'password_reset'). Used to resolve the From address via
            ``get_sender_for_email_type`` so welcome types pick up the
            dedicated welcome sender while every other type resolves by
            its kind exactly as before (issue #937). Welcome types
            (issue #950) also get a Reply-To set to a monitored inbox.
        unsubscribe_url: Optional one-click unsubscribe URL for
            campaign-style mail.
        cc: Optional CC recipient(s) (string or list). Empty values
            are treated as "no CC" and the ``CcAddresses`` key is
            omitted from the SES payload.
        bcc: Optional BCC recipient(s) (string or list). Same
            normalisation as ``cc``; empty values omit the
            ``BccAddresses`` key (issue #950).
        redact_recipient: Never log or leak the recipient address in
            errors (privacy workflow sends).

    Returns:
        str: SES message ID.

    Raises:
        EmailServiceError: If SES API call fails.
        EmailTransportOutcomeUnknown: If SES may have accepted the
            request before the transport failed.
    """
    cc_list = normalize_cc(cc)
    bcc_list = normalize_cc(bcc)

    # Issue #509: kill-switch for tests / local dev. The gate fires
    # BEFORE the boto3 client is built, so no real network call is made
    # and production sender reputation is never touched. The synthetic
    # message id is intentionally recognisable in EmailLog queries
    # during incident response.
    if not getattr(settings, "SES_ENABLED", False):
        cc_label = f" cc={cc_list}" if cc_list else ""
        bcc_label = f" bcc={bcc_list}" if bcc_list else ""
        recipient_label = "[privacy-recipient]" if redact_recipient else to_email
        logger.info(
            "SES disabled - skipping send to %s%s%s (subject=%s)",
            recipient_label,
            cc_label,
            bcc_label,
            subject,
        )
        # Local dev affordance: when DEBUG is on, print the email's
        # action URLs to stdout so the developer can click through the
        # verification / password-reset / event-registration flow
        # without setting up real SES delivery.
        if getattr(settings, "DEBUG", False):
            urls = re.findall(r'href="(https?://[^"]+)"', html_body)
            print(
                f"\n[email_app] SES disabled (local dev). "
                f"To: {recipient_label} | Subject: {subject}",
                flush=True,
            )
            for url in urls:
                print(f"  - {url}", flush=True)
            print("", flush=True)
        return "ses-disabled-noop"

    from_email = get_sender_for_email_type(email_type)
    content = {
        "Simple": {
            "Subject": {
                "Data": subject,
                "Charset": "UTF-8",
            },
            "Body": {
                "Text": {
                    "Data": text_body,
                    "Charset": "UTF-8",
                },
                "Html": {
                    "Data": html_body,
                    "Charset": "UTF-8",
                },
            },
        },
    }
    headers = build_unsubscribe_headers(unsubscribe_url)
    if headers:
        content["Simple"]["Headers"] = headers

    destination = {"ToAddresses": [to_email]}
    if cc_list:
        destination["CcAddresses"] = cc_list
    if bcc_list:
        destination["BccAddresses"] = bcc_list
    send_kwargs = {
        "FromEmailAddress": from_email,
        "Destination": destination,
        "Content": content,
    }

    # Issue #950: route replies to welcome emails to a monitored inbox.
    # Only welcome types get a Reply-To; an empty or malformed configured
    # value omits the optional header so it cannot make SES reject the
    # member's primary delivery.
    if email_type in WELCOME_EMAIL_TYPES:
        reply_to = validate_email_config_value(
            WELCOME_REPLY_TO_KEY,
            get_config(
                WELCOME_REPLY_TO_KEY,
                DEFAULT_WELCOME_REPLY_TO_EMAIL,
            ),
        )
        if reply_to:
            send_kwargs["ReplyToAddresses"] = [reply_to]

    configuration_set_name = get_config("SES_CONFIGURATION_SET_NAME", "").strip()
    if configuration_set_name:
        send_kwargs["ConfigurationSetName"] = configuration_set_name

    try:
        response = build_ses_client().send_email(**send_kwargs)
        return response.get("MessageId", "")
    except ClientError as e:
        recipient_label = "[privacy-recipient]" if redact_recipient else to_email
        if redact_recipient:
            error_code = e.response.get("Error", {}).get("Code", "unknown")
            logger.error(
                "SES rejected redacted recipient request error_code=%s",
                error_code,
            )
            raise EmailServiceError("SES privacy send failed") from None
        logger.exception("Failed to send email via SES to %s", recipient_label)
        raise EmailServiceError(f"SES send failed for {to_email}: {e}") from e
    except BotoCoreError as e:
        recipient_label = "[privacy-recipient]" if redact_recipient else to_email
        if redact_recipient:
            logger.error(
                "SES transport outcome is unknown for redacted recipient "
                "error_type=%s",
                type(e).__name__,
            )
            raise EmailTransportOutcomeUnknown(
                "SES privacy send outcome is unknown",
            ) from None
        logger.exception(
            "SES transport outcome is unknown for %s",
            recipient_label,
        )
        raise EmailTransportOutcomeUnknown(
            f"SES send outcome is unknown for {to_email}: {e}",
        ) from e
