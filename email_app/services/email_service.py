"""Legacy email service for sending transactional emails via Amazon SES.

Usage:
    from email_app.services import EmailService

    service = EmailService()
    service.send(user, 'welcome', {'tier_name': 'Main'})

This class is the transitional A1.2 surface: transactional sends move to
``community_base.mail`` (via ``email_app.package_mail.send_package_mail``)
one app per pull request, and the class shrinks to a DeprecationWarning
shim in the final slice. The rendering and SES transport it used to own
live in :mod:`email_app.services.email_rendering` and
:mod:`email_app.services.ses_transport`; the methods below delegate so
every remaining caller keeps its exact behavior until its own slice
converts.

Templates are stored as markdown files in email_app/email_templates/.
Each template has YAML frontmatter with a subject line, and a markdown
body that supports Django template variables.
"""

import logging
from dataclasses import dataclass

from accounts.utils.tokens import generate_user_action_token
from email_app.services.email_classification import (
    EMAIL_KIND_PROMOTIONAL,
    EmailClassificationError,
    classify_email_type,
)
from email_app.services.email_errors import (
    EmailServiceError,
    EmailTransportOutcomeUnknown,  # noqa: F401 (compatibility re-export)
)
from email_app.services.email_rendering import (
    TEMPLATES_DIR,  # noqa: F401 (compatibility re-export)
    load_template_source,
    render_html_email,
    render_markdown_email,
    render_plain_text_email,
    render_template_parts,
    render_template_with_footer,
)
from email_app.services.ses_transport import (
    DEFAULT_WELCOME_REPLY_TO_EMAIL,  # noqa: F401 (compatibility re-export)
    WELCOME_REPLY_TO_KEY,  # noqa: F401 (compatibility re-export)
    build_ses_client,
    build_unsubscribe_headers,
    normalize_cc,  # noqa: F401 (compatibility re-export)
    send_ses_email,
)
from integrations.config import site_base_url

logger = logging.getLogger(__name__)

# Old private name kept importable for existing callers and tests.
_normalize_cc = normalize_cc

# Issue #450: footer "verify your email" CTA shown to unverified recipients.
# Default-on for every transactional + campaign email; this set names the
# template_name values that opt OUT because the footer would be redundant or
# would derail the user mid-flow.
#   - "email_verification_signup" / "email_verification_subscribe" (issue
#     #767 split): the body itself is a verify CTA; duplicating it in the
#     footer is absurd.
#   - "password_reset": the recipient is here to reset a password; nudging
#     them to click "verify your email" first would derail that flow.
#   - "maven_welcome" (issue #1593): its body asks the enrollee to verify
#     their email as the way to OPT IN to the newsletter, via
#     ``/api/verify-and-subscribe``. The generic footer offers the same verb
#     pointing at ``/api/verify-email``, which verifies WITHOUT subscribing
#     and says nothing about the newsletter. A reader who takes the more
#     directive footer CTA gets "your email address is verified", is not
#     subscribed, and is never told. Two links for one verb, where only one
#     carries the consent, is a trap rather than a redundancy.
EMAIL_TYPES_WITHOUT_VERIFY_FOOTER = {
    "email_verification_signup",
    "email_verification_subscribe",
    "download_delivery",
    "account_email_change_confirm",
    "account_deletion_request",
    "account_deletion_completed",
    "password_reset",
    # Payment-grace copy is contract-locked, and the team diagnostic uses a
    # recipient override for the member's User row. Never append a member
    # verification bearer link to either member or operator billing mail.
    "payment_grace_failure_member",
    "payment_grace_failure_team",
    "payment_grace_reminder_member",
    "payment_grace_expired_member",
    "checkout_payment_failed",
    "maven_welcome",
    "campaign_repermission",
}

# Token lifetime for the footer verify link. 7 days is long enough that an
# email opened days after delivery still works, but bounded so an old archived
# email does not stay verifiable forever.
VERIFY_FOOTER_TOKEN_EXPIRY_HOURS = 24 * 7

UNSUBSCRIBED_AT_SEND = "unsubscribed_at_send"


@dataclass(frozen=True)
class RenderedEmailSendResult:
    """Outcome from the guarded already-rendered email boundary."""

    ses_message_id: str | None = None
    skip_reason: str | None = None

    @property
    def sent(self):
        return self.skip_reason is None


@dataclass(frozen=True)
class PreparedRenderedEmail:
    """A rendered message whose local validation and consent checks passed."""

    to_email: str = ''
    subject: str = ''
    full_html: str = ''
    plain_text: str = ''
    email_type: str = ''
    unsubscribe_url: str | None = None
    cc: object = None
    bcc: object = None
    skip_reason: str | None = None
    redact_transport_recipient: bool = False


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
            self._ses_client = build_ses_client()
        return self._ses_client

    def send(
        self,
        user,
        template_name,
        context=None,
        cc=None,
        bcc=None,
        recipient_email=None,
        dedupe_key=None,
    ):
        """Send a transactional email to a user.

        Args:
            user: User model instance (must have .email attribute). The
                ``user.email_verified`` value at the moment of ``.send()``
                is what determines whether the verify-email footer CTA
                renders (issue #450). Callers passing a stale instance
                accept the staleness — refresh from DB if needed.
            template_name: Name of the email template (e.g. 'welcome').
            context: Dict of template variables to render the template with.
            cc: Optional CC recipient(s). Either a single string email
                address or a list/tuple of strings. ``None`` or empty
                values send without a CC. This is generic plumbing for
                callers that intentionally need visible copies; paid
                welcomes do not pass staff here.
            bcc: Optional BCC recipient(s). Same shape rules as ``cc``.
                Paid checkout welcomes use this for the hidden staff copy;
                other callers may use it for their own hidden-copy needs.
            recipient_email: Optional destination override. Used for
                account-security flows where the email must be addressed to
                a verified pending/former address before or after
                ``user.email`` changes.
            dedupe_key: Optional durable lifecycle-send idempotency key.

        Returns:
            EmailLog instance for the sent email.

        Raises:
            EmailServiceError: If template not found or SES send fails.
        """
        if context is None:
            context = {}
        to_email = (recipient_email or user.email).strip()

        if dedupe_key:
            from email_app.models import EmailLog
            existing = EmailLog.objects.filter(dedupe_key=dedupe_key).first()
            if existing is not None:
                return existing

        email_kind, skip_reason = self._delivery_decision(user, template_name)
        if skip_reason is not None:
            logger.info(
                "Skipping email email_type=%s user_id=%s reason=%s",
                template_name,
                getattr(user, "pk", None),
                skip_reason,
            )
            return None

        # Load and render the template. DB overrides beat filesystem
        # templates, but no override keeps the historical file path.
        subject, body_markdown, body_html, footer_note = self._render_template_parts(
            template_name,
            user,
            context,
        )

        unsubscribe_url = None
        if email_kind == EMAIL_KIND_PROMOTIONAL:
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
        plain_text = self.render_plain_text_email(
            body_markdown,
            footer_note=footer_note,
            verify_email_url=verify_email_url,
            unsubscribe_url=unsubscribe_url,
        )

        # Send via SES
        ses_message_id = self._send_ses(
            to_email,
            subject,
            full_html,
            text_body=plain_text,
            email_type=template_name,
            unsubscribe_url=unsubscribe_url,
            cc=cc,
            bcc=bcc,
        )

        # Log the send. Internal sends to an ad-hoc recipient surrogate
        # (issue #703 staff signup heads-up) pass an object that is NOT
        # a saved User row — those can't satisfy the EmailLog FK and we
        # explicitly do not want a Studio "emails sent" entry for them.
        # Detect by ``_meta`` presence + a non-falsy pk; anything else
        # is a surrogate and we skip the log write.
        from django.db.models import Model

        from email_app.models import EmailLog

        if isinstance(user, Model) and getattr(user, "pk", None):
            email_log = EmailLog.objects.create(
                user=user,
                recipient_email=to_email,
                email_type=template_name,
                subject=subject,
                ses_message_id=ses_message_id,
                dedupe_key=dedupe_key,
            )
        else:
            email_log = None

        logger.info(
            'Sent "%s" email to %s (SES message ID: %s)',
            template_name,
            to_email,
            ses_message_id,
        )

        return email_log

    def send_rendered(
        self,
        user,
        subject,
        body_markdown,
        *,
        email_type,
        campaign_id=None,
        footer_note=None,
        cc=None,
        bcc=None,
    ):
        """Guard and send an email from resolved Markdown source.

        This is the public delivery boundary for callers whose content does
        not come from a named email template, notably ``EmailCampaign``. A
        persisted recipient is refreshed immediately before the consent
        decision and before any unsubscribe/verification token is minted.

        The caller remains responsible for its domain-specific ``EmailLog``
        row because already-rendered sends may need associations such as a
        campaign or event. A promotional opt-out returns a distinct no-send
        result and never reaches the private SES transport.
        """
        prepared = self.prepare_rendered(
            user,
            subject,
            body_markdown,
            email_type=email_type,
            campaign_id=campaign_id,
            footer_note=footer_note,
            cc=cc,
            bcc=bcc,
        )
        if prepared.skip_reason is not None:
            return RenderedEmailSendResult(skip_reason=prepared.skip_reason)
        ses_message_id = self.send_prepared(prepared)
        return RenderedEmailSendResult(ses_message_id=ses_message_id)

    def prepare_rendered(
        self,
        user,
        subject,
        body_markdown,
        *,
        email_type,
        campaign_id=None,
        footer_note=None,
        cc=None,
        bcc=None,
    ):
        """Complete every local operation before a caller claims transport."""
        if getattr(user, "pk", None):
            user.refresh_from_db()

        email_kind, skip_reason = self._delivery_decision(user, email_type)
        if skip_reason is not None:
            logger.info(
                "Skipping rendered email email_type=%s campaign_id=%s "
                "user_id=%s reason=%s",
                email_type,
                campaign_id,
                getattr(user, "pk", None),
                skip_reason,
            )
            return PreparedRenderedEmail(skip_reason=skip_reason)

        unsubscribe_url = None
        if email_kind == EMAIL_KIND_PROMOTIONAL:
            unsubscribe_url = self._build_unsubscribe_url(user)

        verify_email_url = None
        if self._should_include_verify_footer(user, email_type):
            verify_email_url = self._build_verify_email_url(user)

        full_html = render_markdown_email(
            subject,
            body_markdown,
            unsubscribe_url=unsubscribe_url,
            footer_note=footer_note,
            verify_email_url=verify_email_url,
        )
        plain_text = render_plain_text_email(
            body_markdown,
            footer_note=footer_note,
            verify_email_url=verify_email_url,
            unsubscribe_url=unsubscribe_url,
        )
        return PreparedRenderedEmail(
            to_email=user.email,
            subject=subject,
            full_html=full_html,
            plain_text=plain_text,
            email_type=email_type,
            unsubscribe_url=unsubscribe_url,
            cc=cc,
            bcc=bcc,
        )

    def send_prepared(self, prepared):
        """Cross only the SES transport boundary for a prepared message."""
        return self._send_ses(
            prepared.to_email,
            prepared.subject,
            prepared.full_html,
            text_body=prepared.plain_text,
            email_type=prepared.email_type,
            unsubscribe_url=prepared.unsubscribe_url,
            cc=prepared.cc,
            bcc=prepared.bcc,
            redact_recipient=prepared.redact_transport_recipient,
        )

    def prepare_template(
        self,
        user,
        template_name,
        context=None,
        *,
        recipient_email=None,
        cc=None,
        bcc=None,
        redact_transport_recipient=False,
    ):
        """Render and validate a named template without crossing transport."""
        context = context or {}
        email_kind, skip_reason = self._delivery_decision(user, template_name)
        if skip_reason is not None:
            return PreparedRenderedEmail(skip_reason=skip_reason)

        subject, body_markdown, body_html, footer_note = self._render_template_parts(
            template_name,
            user,
            context,
        )
        unsubscribe_url = None
        if email_kind == EMAIL_KIND_PROMOTIONAL:
            unsubscribe_url = self._build_unsubscribe_url(user)
        verify_email_url = None
        if self._should_include_verify_footer(user, template_name):
            verify_email_url = self._build_verify_email_url(user)
        full_html = self.render_html_email(
            subject,
            body_html,
            unsubscribe_url=unsubscribe_url,
            footer_note=footer_note,
            verify_email_url=verify_email_url,
        )
        plain_text = self.render_plain_text_email(
            body_markdown,
            footer_note=footer_note,
            verify_email_url=verify_email_url,
            unsubscribe_url=unsubscribe_url,
        )
        return PreparedRenderedEmail(
            to_email=(recipient_email or user.email).strip(),
            subject=subject,
            full_html=full_html,
            plain_text=plain_text,
            email_type=template_name,
            unsubscribe_url=unsubscribe_url,
            cc=cc,
            bcc=bcc,
            redact_transport_recipient=redact_transport_recipient,
        )

    @staticmethod
    def _delivery_decision(user, email_type):
        """Return the canonical kind and any consent-based skip reason."""
        try:
            email_kind = classify_email_type(email_type)
        except EmailClassificationError as exc:
            raise EmailServiceError(str(exc)) from exc

        # Promotional sends respect the global newsletter unsubscribe flag.
        # Transactional sends must remain deliverable for account/service
        # continuity even when the user opted out of marketing.
        if (
            email_kind == EMAIL_KIND_PROMOTIONAL
            and getattr(user, "unsubscribed", False)
        ):
            return email_kind, UNSUBSCRIBED_AT_SEND
        return email_kind, None

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
        return render_template_with_footer(template_name, user, context)

    def _render_template_parts(self, template_name, user, context):
        """Resolve a named template once and return its Markdown and HTML bodies."""
        return render_template_parts(template_name, user, context)

    def _load_template_source(self, template_name):
        """Return ``(subject, body_markdown, footer_note)`` for a template."""
        return load_template_source(template_name)

    def _build_unsubscribe_url(self, user):
        """Build a one-click unsubscribe URL for the user.

        Uses a JWT token containing the user ID that does not expire.
        """
        site_url = site_base_url()
        token = generate_user_action_token(user.pk, "unsubscribe")

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
        return render_html_email(
            subject,
            body_html,
            unsubscribe_url=unsubscribe_url,
            footer_note=footer_note,
            verify_email_url=verify_email_url,
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
        """Convert markdown to HTML and wrap it in the shared template.

        Issue #989: routes through the canonical ``render_email_markdown`` so
        the Studio campaign preview and sent campaigns parse markdown exactly
        like the website (only mermaid + codehilite are disabled for email).
        """
        return render_markdown_email(
            subject,
            body_markdown,
            unsubscribe_url=unsubscribe_url,
            footer_note=footer_note,
            verify_email_url=verify_email_url,
        )

    def render_plain_text_email(
        self,
        body_markdown,
        *,
        unsubscribe_url=None,
        footer_note=None,
        verify_email_url=None,
    ):
        """Render resolved Markdown plus the same actions as the HTML footer."""
        return render_plain_text_email(
            body_markdown,
            unsubscribe_url=unsubscribe_url,
            footer_note=footer_note,
            verify_email_url=verify_email_url,
        )

    def _build_unsubscribe_headers(self, unsubscribe_url):
        """Build SES-compatible one-click unsubscribe headers."""
        return build_unsubscribe_headers(unsubscribe_url)

    def _send_ses(
        self,
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

        Returns:
            str: SES message ID.

        Raises:
            EmailServiceError: If SES API call fails.
        """
        return send_ses_email(
            to_email,
            subject,
            html_body,
            text_body=text_body,
            email_type=email_type,
            unsubscribe_url=unsubscribe_url,
            cc=cc,
            bcc=bcc,
            redact_recipient=redact_recipient,
        )
