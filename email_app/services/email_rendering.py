"""Site email template rendering, shared by every site-owned send path.

Extracted from ``EmailService`` (A1.2 slice 3) so flows that keep their own
transport — the calendar lifecycle mail with its raw MIME ``.ics`` parts and
the event operator notifications — can render exactly as before without
importing the legacy ``EmailService`` class. ``EmailService`` delegates here,
so behavior is unchanged for callers still using the class: same override
precedence (``EmailTemplateOverride`` over ``email_app/email_templates/``),
same default context, same markdown renderers (issue #989) and chrome.
"""

from datetime import datetime
from pathlib import Path

import frontmatter
from django.conf import settings
from django.template import Context, Template
from django.template.loader import render_to_string

from accounts.services.timezones import format_user_datetime
from accounts.utils.display import GREETING_FALLBACK, greeting_name
from content.utils.markdown import render_email_markdown, render_email_plain_text
from email_app.services.email_errors import EmailServiceError
from integrations.config import site_base_url

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "email_templates"


def load_template_source(template_name):
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


def render_template_parts(template_name, user, context):
    """Resolve a named template once and return its Markdown and HTML bodies."""
    subject_source, body_source, footer_note = load_template_source(
        template_name,
    )

    # Build full context with defaults
    full_context = {
        # Issue #1591: the greeting must never be an email handle.
        # Resolved here rather than as ``|default:"there"`` in the
        # template files so operator overrides stored in the DB get
        # the fix too, and so an empty value can never ship "Hi ,".
        # Caller context still wins via ``full_context.update``.
        "user_name": greeting_name(user) or GREETING_FALLBACK,
        "user_email": user.email,
        "site_url": site_base_url(),
        "site_name": getattr(settings, "SITE_NAME", "AI Shipping Labs"),
    }
    full_context.update(context)

    # Issue #666 guardrail: future email senders may forget to pre-format
    # the event_datetime and pass a raw ``datetime`` instead. Convert
    # any ``datetime`` value in the context to the recipient's timezone
    # via the shared helper so the rendered body is correct regardless
    # of caller hygiene. Strings are passed through unchanged so the
    # existing pre-formatted callers keep working.
    for key, value in list(full_context.items()):
        if isinstance(value, datetime):
            full_context[key] = format_user_datetime(value, user)

    # Render subject as Django template
    subject_template = Template(subject_source)
    subject = subject_template.render(Context(full_context))

    # Render body as Django template first (for variable substitution)
    body_template = Template(body_source)
    rendered_body = body_template.render(Context(full_context))

    # Convert markdown to HTML through the canonical renderer (issue #989)
    # so transactional email bodies parse identically to the website.
    body_html = render_email_markdown(rendered_body)

    return subject, rendered_body, body_html, footer_note


def render_template_with_footer(template_name, user, context):
    """Return ``(subject, body_html, footer_note)`` for focused render callers."""
    subject, _body_markdown, body_html, footer_note = render_template_parts(
        template_name,
        user,
        context,
    )
    return subject, body_html, footer_note


def render_template(template_name, user, context):
    """Return ``(subject, body_html)`` — the template body only, no chrome."""
    subject, body_html, _ = render_template_with_footer(
        template_name,
        user,
        context,
    )
    return subject, body_html


def render_html_email(
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
    ``send`` decides per-recipient based on the verify-footer policy.
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
    body_html = render_email_markdown(body_markdown)
    return render_html_email(
        subject,
        body_html,
        unsubscribe_url=unsubscribe_url,
        footer_note=footer_note,
        verify_email_url=verify_email_url,
    )


def render_plain_text_email(
    body_markdown,
    *,
    unsubscribe_url=None,
    footer_note=None,
    verify_email_url=None,
):
    """Render resolved Markdown plus the same actions as the HTML footer."""
    sections = [render_email_plain_text(body_markdown), 'AI Shipping Labs']
    if footer_note:
        sections.append(str(footer_note).strip())
    if verify_email_url:
        sections.append(
            'Your email is not verified on our platform.\n'
            f'Verify your email: {verify_email_url}'
        )
    if unsubscribe_url:
        sections.append(f'Unsubscribe from all emails: {unsubscribe_url}')
    return '\n\n'.join(section for section in sections if section).strip()
