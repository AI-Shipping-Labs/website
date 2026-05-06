"""Email service for sending transactional emails via Amazon SES.

Usage:
    from email_app.services import EmailService

    service = EmailService()
    service.send(user, 'welcome', {'tier_name': 'Main'})

Templates are stored as markdown files in email_app/email_templates/.
Each template has YAML frontmatter with a subject line, and a markdown
body that supports Django template variables.
"""

import logging
from pathlib import Path

import boto3
import frontmatter
import markdown
from django.conf import settings
from django.template import Context, Template
from django.template.loader import render_to_string

from integrations.config import get_config, site_base_url

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "email_templates"

# Valid transactional email types
TRANSACTIONAL_TYPES = {
    "welcome",
    "payment_failed",
    "cancellation",
    "community_invite",
    "lead_magnet_delivery",
    "event_reminder",
    "email_verification",
    "password_reset",
    "event_registration",
    "welcome_imported",
}


class EmailServiceError(Exception):
    """Raised when email sending fails."""

    pass


class EmailService:
    """Service for sending transactional emails via Amazon SES v2.

    Loads markdown templates from email_app/email_templates/,
    renders them with context variables, wraps in HTML email template,
    sends via SES, and logs every send to EmailLog.
    """

    def __init__(self):
        self._ses_client = None

    @property
    def ses_client(self):
        """Lazy-initialize the SES v2 client."""
        if self._ses_client is None:
            self._ses_client = boto3.client(
                "sesv2",
                region_name=get_config("AWS_SES_REGION", "us-east-1"),
                aws_access_key_id=get_config("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=get_config("AWS_SECRET_ACCESS_KEY"),
            )
        return self._ses_client

    def send(self, user, template_name, context=None):
        """Send a transactional email to a user.

        Args:
            user: User model instance (must have .email attribute).
            template_name: Name of the email template (e.g. 'welcome').
            context: Dict of template variables to render the template with.

        Returns:
            EmailLog instance for the sent email.

        Raises:
            EmailServiceError: If template not found or SES send fails.
        """
        if context is None:
            context = {}

        # Don't send to unsubscribed users
        if getattr(user, "unsubscribed", False):
            logger.info(
                'Skipping email "%s" to unsubscribed user %s',
                template_name,
                user.email,
            )
            return None

        # Load and render the template
        subject, body_html = self._render_template(template_name, user, context)

        # Build the unsubscribe URL
        unsubscribe_url = self._build_unsubscribe_url(user)

        # Wrap in base HTML email template
        full_html = self.render_html_email(
            subject,
            body_html,
            unsubscribe_url=unsubscribe_url,
        )

        # Send via SES
        ses_message_id = self._send_ses(user.email, subject, full_html)

        # Log the send
        from email_app.models import EmailLog

        email_log = EmailLog.objects.create(
            user=user,
            email_type=template_name,
            ses_message_id=ses_message_id,
        )

        logger.info(
            'Sent "%s" email to %s (SES message ID: %s)',
            template_name,
            user.email,
            ses_message_id,
        )

        return email_log

    def _render_template(self, template_name, user, context):
        """Load a markdown template, render with context, convert to HTML.

        Args:
            template_name: Template file name (without .md extension).
            user: User model instance.
            context: Additional template variables.

        Returns:
            Tuple of (subject, body_html).

        Raises:
            EmailServiceError: If template file not found.
        """
        template_path = TEMPLATES_DIR / f"{template_name}.md"

        if not template_path.exists():
            raise EmailServiceError(f"Email template not found: {template_name} (looked in {template_path})")

        # Parse frontmatter and body
        post = frontmatter.load(str(template_path))

        # Build full context with defaults
        full_context = {
            "user_name": user.first_name or user.email.split("@")[0],
            "user_email": user.email,
            "site_url": site_base_url(),
            "site_name": getattr(settings, "SITE_NAME", "AI Shipping Labs"),
        }
        full_context.update(context)

        # Render subject as Django template
        subject_template = Template(post.metadata.get("subject", template_name))
        subject = subject_template.render(Context(full_context))

        # Render body as Django template first (for variable substitution)
        body_template = Template(post.content)
        rendered_body = body_template.render(Context(full_context))

        # Convert markdown to HTML
        body_html = markdown.markdown(
            rendered_body,
            extensions=["extra"],
        )

        return subject, body_html

    def _build_unsubscribe_url(self, user):
        """Build a one-click unsubscribe URL for the user.

        Uses a JWT token containing the user ID that does not expire.
        """
        import jwt

        site_url = site_base_url()
        secret = settings.SECRET_KEY

        token = jwt.encode(
            {"user_id": user.pk, "action": "unsubscribe"},
            secret,
            algorithm="HS256",
        )

        return f"{site_url}/api/unsubscribe?token={token}"

    def render_html_email(
        self,
        subject,
        body_html,
        *,
        unsubscribe_url=None,
        footer_note=None,
    ):
        """Wrap rendered HTML in the shared email chrome template."""
        return render_to_string(
            "email_app/base_email.html",
            {
                "subject": subject,
                "body_html": body_html,
                "unsubscribe_url": unsubscribe_url,
                "footer_note": footer_note,
            },
        )

    def render_markdown_email(
        self,
        subject,
        body_markdown,
        *,
        unsubscribe_url=None,
        footer_note=None,
    ):
        """Convert markdown to HTML and wrap it in the shared template."""
        body_html = markdown.markdown(body_markdown, extensions=["extra"])
        return self.render_html_email(
            subject,
            body_html,
            unsubscribe_url=unsubscribe_url,
            footer_note=footer_note,
        )

    def _build_unsubscribe_headers(self, unsubscribe_url):
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

    def _send_ses(
        self,
        to_email,
        subject,
        html_body,
        *,
        unsubscribe_url=None,
    ):
        """Send an email via Amazon SES v2 SendEmail API.

        Args:
            to_email: Recipient email address.
            subject: Email subject line.
            html_body: Full HTML email body.
            unsubscribe_url: Optional one-click unsubscribe URL for
                campaign-style mail.

        Returns:
            str: SES message ID.

        Raises:
            EmailServiceError: If SES API call fails.
        """
        from_email = get_config(
            "SES_FROM_EMAIL",
            "community@aishippinglabs.com",
        )
        content = {
            "Simple": {
                "Subject": {
                    "Data": subject,
                    "Charset": "UTF-8",
                },
                "Body": {
                    "Html": {
                        "Data": html_body,
                        "Charset": "UTF-8",
                    },
                },
            },
        }
        headers = self._build_unsubscribe_headers(unsubscribe_url)
        if headers:
            content["Simple"]["Headers"] = headers

        try:
            response = self.ses_client.send_email(
                FromEmailAddress=from_email,
                Destination={
                    "ToAddresses": [to_email],
                },
                Content=content,
            )
            return response.get("MessageId", "")
        except Exception as e:
            logger.exception("Failed to send email via SES to %s", to_email)
            raise EmailServiceError(f"SES send failed for {to_email}: {e}") from e
