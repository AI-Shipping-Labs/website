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
    "email_verification_reminder",
    "password_reset",
    "event_registration",
    "welcome_imported",
}

# Issue #450: footer "verify your email" CTA shown to unverified recipients.
# Default-on for every transactional + campaign email; this set names the
# template_name values that opt OUT because the footer would be redundant or
# would derail the user mid-flow.
#   - "email_verification": the body itself is a verify CTA; duplicating it
#     in the footer is absurd.
#   - "password_reset": the recipient is here to reset a password; nudging
#     them to click "verify your email" first would derail that flow.
EMAIL_TYPES_WITHOUT_VERIFY_FOOTER = {"email_verification", "password_reset"}

# Token lifetime for the footer verify link. 7 days is long enough that an
# email opened days after delivery still works, but bounded so an old archived
# email does not stay verifiable forever.
VERIFY_FOOTER_TOKEN_EXPIRY_HOURS = 24 * 7


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
            user: User model instance (must have .email attribute). The
                ``user.email_verified`` value at the moment of ``.send()``
                is what determines whether the verify-email footer CTA
                renders (issue #450). Callers passing a stale instance
                accept the staleness — refresh from DB if needed.
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

        # Load and render the template. DB overrides beat filesystem
        # templates, but no override keeps the historical file path.
        subject, body_html, footer_note = self._render_template_with_footer(
            template_name,
            user,
            context,
        )

        # Build the unsubscribe URL
        unsubscribe_url = self._build_unsubscribe_url(user)

        # Issue #450: only mint the verify-email token when the footer CTA
        # will actually render (unverified recipient + opted-in template).
        # Skip the token mint entirely for verified users — wasted work.
        verify_email_url = None
        if self._should_include_verify_footer(user, template_name):
            verify_email_url = self._build_verify_email_url(user)

        # Wrap in base HTML email template
        full_html = self.render_html_email(
            subject,
            body_html,
            unsubscribe_url=unsubscribe_url,
            footer_note=footer_note,
            verify_email_url=verify_email_url,
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
        """Load a template, render with context, convert to HTML."""
        subject, body_html, _ = self._render_template_with_footer(
            template_name,
            user,
            context,
        )
        return subject, body_html

    def _render_template_with_footer(self, template_name, user, context):
        """Load a template, render with context, convert to HTML.

        Database overrides are preferred over filesystem templates. When no
        override exists, behavior is identical to the original markdown file
        path.

        Args:
            template_name: Template file name (without .md extension).
            user: User model instance.
            context: Additional template variables.

        Returns:
            Tuple of (subject, body_html, footer_note).

        Raises:
            EmailServiceError: If no override or template file is found.
        """
        subject_source, body_source, footer_note = self._load_template_source(
            template_name,
        )

        # Build full context with defaults
        full_context = {
            "user_name": user.first_name or user.email.split("@")[0],
            "user_email": user.email,
            "site_url": site_base_url(),
            "site_name": getattr(settings, "SITE_NAME", "AI Shipping Labs"),
        }
        full_context.update(context)

        # Render subject as Django template
        subject_template = Template(subject_source)
        subject = subject_template.render(Context(full_context))

        # Render body as Django template first (for variable substitution)
        body_template = Template(body_source)
        rendered_body = body_template.render(Context(full_context))

        # Convert markdown to HTML
        body_html = markdown.markdown(
            rendered_body,
            extensions=["extra"],
        )

        return subject, body_html, footer_note

    def _load_template_source(self, template_name):
        """Return ``(subject, body_markdown, footer_note)`` for a template."""
        from email_app.models import EmailTemplateOverride

        override = EmailTemplateOverride.objects.filter(
            template_name=template_name,
        ).first()
        if override is not None:
            return override.subject, override.body_markdown, override.footer_note

        template_path = TEMPLATES_DIR / f"{template_name}.md"
        if not template_path.exists():
            raise EmailServiceError(
                f"Email template not found: {template_name} "
                f"(looked in {template_path})"
            )

        post = frontmatter.load(str(template_path))
        return post.metadata.get("subject", template_name), post.content, ""

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

    def _should_include_verify_footer(self, user, template_name):
        """Issue #450: decide if the verify-email footer renders for this send.

        Returns ``True`` only when:
        - the recipient is currently unverified
          (``user.email_verified is False``), AND
        - the template is not in ``EMAIL_TYPES_WITHOUT_VERIFY_FOOTER``.

        Verified users never see the footer — there is nothing to nudge.
        Templates in the opt-out set never carry the footer regardless of
        verification state (it would be redundant or off-flow).
        """
        if getattr(user, "email_verified", True):
            return False
        if template_name in EMAIL_TYPES_WITHOUT_VERIFY_FOOTER:
            return False
        return True

    def _build_verify_email_url(self, user):
        """Issue #450: build a one-click email-verification URL.

        Reuses the existing JWT primitive from ``accounts.views.auth`` so
        the registration-flow link and the footer link are identical and
        decode through the same handler at ``/api/verify-email``. Token
        lifetime is ``VERIFY_FOOTER_TOKEN_EXPIRY_HOURS`` (7 days), longer
        than the 24h registration link because email recipients open
        messages on their own schedule.
        """
        # Imported lazily to avoid a circular import at module load
        # (accounts.views.auth imports the EmailService back).
        from accounts.views.auth import _generate_verification_token

        token = _generate_verification_token(
            user.pk,
            expiry_hours=VERIFY_FOOTER_TOKEN_EXPIRY_HOURS,
        )
        return f"{site_base_url()}/api/verify-email?token={token}"

    def render_html_email(
        self,
        subject,
        body_html,
        *,
        unsubscribe_url=None,
        footer_note=None,
        verify_email_url=None,
    ):
        """Wrap rendered HTML in the shared email chrome template.

        ``verify_email_url`` (issue #450) is rendered as a footer CTA
        positioned ABOVE the unsubscribe block when set. Callers should
        not populate this directly when going through ``send`` —
        ``send`` decides per-recipient based on
        ``_should_include_verify_footer``.
        """
        return render_to_string(
            "email_app/base_email.html",
            {
                "subject": subject,
                "body_html": body_html,
                "unsubscribe_url": unsubscribe_url,
                "footer_note": footer_note,
                "verify_email_url": verify_email_url,
            },
        )

    def render_markdown_email(
        self,
        subject,
        body_markdown,
        *,
        unsubscribe_url=None,
        footer_note=None,
        verify_email_url=None,
    ):
        """Convert markdown to HTML and wrap it in the shared template."""
        body_html = markdown.markdown(body_markdown, extensions=["extra"])
        return self.render_html_email(
            subject,
            body_html,
            unsubscribe_url=unsubscribe_url,
            footer_note=footer_note,
            verify_email_url=verify_email_url,
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
        # Issue #509: kill-switch for tests / local dev. When SES_ENABLED is
        # False the gate short-circuits BEFORE the boto3 client is built, so
        # no real network call is made and production sender reputation is
        # never touched. The synthetic message id is intentionally
        # recognisable in EmailLog queries during incident response.
        if not getattr(settings, "SES_ENABLED", False):
            logger.info(
                "SES disabled - skipping send to %s (subject=%s)",
                to_email,
                subject,
            )
            return "ses-disabled-noop"

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

        send_kwargs = {
            "FromEmailAddress": from_email,
            "Destination": {
                "ToAddresses": [to_email],
            },
            "Content": content,
        }
        configuration_set_name = get_config("SES_CONFIGURATION_SET_NAME", "").strip()
        if configuration_set_name:
            send_kwargs["ConfigurationSetName"] = configuration_set_name

        try:
            response = self.ses_client.send_email(**send_kwargs)
            return response.get("MessageId", "")
        except Exception as e:
            logger.exception("Failed to send email via SES to %s", to_email)
            raise EmailServiceError(f"SES send failed for {to_email}: {e}") from e
