"""Studio views for editing transactional email templates (issue #455).

Operators get a list page that surfaces every transactional template, and
an edit page with subject / body / footer fields plus an iframe-based live
preview. Saves persist into ``EmailTemplateOverride``; ``EmailService``
prefers the override row over the on-disk template, so deleting the row
reverts to the file.
"""

import logging

import frontmatter
from django.contrib import messages
from django.http import (
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
)
from django.shortcuts import redirect, render
from django.utils.html import escape
from django.views.decorators.http import require_POST

from email_app.models import EmailTemplateOverride
from email_app.services.email_service import (
    TEMPLATES_DIR,
    EmailService,
    EmailServiceError,
)
from email_app.services.preview_contexts import get_preview_context
from studio.decorators import staff_required

logger = logging.getLogger(__name__)


# Display order on the list page. Mirrors the order operators usually
# think about (onboarding -> account -> billing -> events -> imports).
TEMPLATE_DISPLAY_ORDER = [
    'welcome',
    'free_welcome',
    'email_verification_signup',
    'email_verification_subscribe',
    'password_reset',
    'community_invite',
    'lead_magnet_delivery',
    'download_delivery',
    'event_registration',
    'event_reminder',
    'cancellation',
    'payment_failed',
    'welcome_imported',
    'sprint_week_start',
    'sprint_week_note_prompt',
]

# Canonical operator guidance for every editable/registered template. Keep
# this keyed by the actual template slug so list and edit/error renders cannot
# drift apart. The completeness test intentionally fails when registration is
# extended without documenting the real send trigger.
TEMPLATE_SENT_WHEN = {
    'account_email_change_confirm': 'Sent when a member requests an account email-address change.',
    'account_email_changed_notice': 'Sent to the previous address after an account email change succeeds.',
    'basic_welcome': 'Sent when a member first receives Basic membership access.',
    'cancellation': 'Sent when a paid membership is scheduled to cancel.',
    'cofounder_welcome': 'Sent when a cofounder membership signup is completed.',
    'community_invite': 'Sent when an eligible member is invited to the private community.',
    'download_delivery': 'Sent when a visitor requests delivery of a downloadable resource.',
    'email_verification_signup': 'Sent when a new account must verify its email address.',
    'email_verification_signup_reminder': 'Sent when an unverified account is reminded to verify its email address.',
    'email_verification_subscribe': 'Sent when a newsletter subscriber must verify their email address.',
    'email_verification_subscribe_reminder': 'Sent when an unverified newsletter subscriber is reminded to verify.',
    'event_cancelled': 'Sent to registered attendees when an event is cancelled.',
    'event_recording_ready': 'Sent to event hosts when a recording is ready for Studio review.',
    'event_registration': 'Sent when a member successfully registers for an event.',
    'event_reminder': 'Sent to registered attendees before an upcoming event.',
    'event_rescheduled': 'Sent to registered attendees when an event schedule changes.',
    'event_workshop_ready': 'Sent to registered attendees when the related workshop is ready.',
    'free_welcome': 'Sent when a new Free member account is created.',
    'lead_magnet_delivery': 'Sent when a visitor requests a free lead-magnet resource.',
    'maven_cohort_removal_notification': 'Sent to staff when a member is removed from a Maven cohort.',
    'maven_welcome': 'Sent when a Maven enrollee receives course access.',
    'onboarding_reminder': 'Sent when a paid member has not completed onboarding after one week.',
    'password_reset': 'Sent when someone requests a password-reset link.',
    'payment_failed': 'Sent when a paid membership invoice payment fails.',
    'plan_shared': 'Sent when staff shares a sprint plan with its member.',
    'post_event_followup': 'Sent to registered attendees after an event with its follow-up resources.',
    'premium_welcome': 'Sent when a member first receives Premium membership access.',
    'series_cancellation': 'Sent to registrants when one session in an event series is cancelled, with the calendar cancellation update.',
    'series_registration': 'Sent after a member registers for an event series, with the calendar invitation for its sessions.',
    'series_update': 'Sent to registrants when event-series session details change, with the updated calendar invitation.',
    'slack_join_notification': 'Sent to staff when a known member joins the Slack workspace.',
    'sprint_end_recap': 'Sent to sprint participants when their sprint-end recap is ready.',
    'sprint_partner_intro': 'Sent when enrolled sprint partners are introduced to each other.',
    'sprint_week_note_prompt': 'Sent to sprint participants when their weekly progress note is due.',
    'sprint_week_start': 'Sent to sprint participants when a new sprint week begins.',
    'staff_signup_notification': 'Sent to staff when a new paid member signs up.',
    'welcome': 'Sent when a new paid member account is created.',
    'welcome_back': 'Sent when a returning paid member resubscribes.',
    'welcome_imported': 'Sent when staff imports a contact and explicitly triggers its welcome.',
    'workshop_announcement': 'Sent when staff announces a published workshop to its selected audience.',
}


def _sent_when(template_name):
    """Return canonical trigger guidance, failing loudly if it is missing."""
    return TEMPLATE_SENT_WHEN[template_name]


def _all_template_names():
    """Return the canonical list of editable template slugs."""
    # Every on-disk Markdown template is editable. Classification sets are
    # delivery policy, not an editor registry, and may intentionally lag a
    # newly introduced transactional variant.
    on_disk = {path.stem for path in TEMPLATES_DIR.glob('*.md')}
    ordered = [name for name in TEMPLATE_DISPLAY_ORDER if name in on_disk]
    ordered.extend(sorted(on_disk - set(ordered)))
    return ordered


def _template_exists(template_name):
    if (TEMPLATES_DIR / f'{template_name}.md').exists():
        return True
    return EmailTemplateOverride.objects.filter(
        template_name=template_name,
    ).exists()


def _read_file_template(template_name):
    """Load the on-disk template, return ``(subject, body_markdown)``.

    Returns ``(None, None)`` when the file does not exist (override-only
    template names should not normally occur but the model allows it).
    """
    template_path = TEMPLATES_DIR / f'{template_name}.md'
    if not template_path.exists():
        return None, None
    post = frontmatter.load(str(template_path))
    return post.metadata.get('subject', template_name), post.content


def _resolve_initial(template_name):
    """Pick the prefill values for the edit form.

    Order: override row first, then the on-disk file. ``None`` if neither
    exists (treated as 404 by the view).
    """
    override = EmailTemplateOverride.objects.filter(
        template_name=template_name,
    ).first()
    if override is not None:
        return {
            'subject': override.subject,
            'body_markdown': override.body_markdown,
            'footer_note': override.footer_note,
            'has_override': True,
            'override': override,
        }
    file_subject, file_body = _read_file_template(template_name)
    if file_subject is None:
        return None
    return {
        'subject': file_subject,
        'body_markdown': file_body,
        'footer_note': '',
        'has_override': False,
        'override': None,
    }


def _render_preview_html(template_name, subject, body_markdown, footer_note):
    """Render the preview through the same chrome the real send uses.

    Variables in the body are filled with placeholder values from
    ``preview_contexts.PREVIEW_CONTEXTS`` so no real user data leaks.
    """
    from django.template import Context, Template
    from django.template.loader import render_to_string

    from content.utils.markdown import render_email_markdown

    placeholder = get_preview_context(template_name)
    # ``user_name`` and ``user_email`` are also auto-injected by EmailService
    # for real sends; mirror that here so previews look the same.
    placeholder.setdefault('user_name', 'Ada')
    placeholder.setdefault('user_email', 'ada@example.com')
    placeholder.setdefault('site_url', 'https://aishippinglabs.com')
    placeholder.setdefault('site_name', 'AI Shipping Labs')

    rendered_subject = Template(subject or '').render(Context(placeholder))
    rendered_body = Template(body_markdown or '').render(Context(placeholder))
    body_html = render_email_markdown(rendered_body)

    return render_to_string(
        'email_app/base_email.html',
        {
            'subject': rendered_subject,
            'body_html': body_html,
            # Show a fake unsubscribe link so the operator sees the
            # footer chrome that real recipients will get.
            'unsubscribe_url': 'https://aishippinglabs.com/api/unsubscribe?token=preview',
            'footer_note': footer_note or '',
        },
    )


@staff_required
def email_template_list(request):
    """List every transactional template with edit / status info."""
    overrides = {
        o.template_name: o
        for o in EmailTemplateOverride.objects.all()
    }
    rows = []
    for template_name in _all_template_names():
        override = overrides.get(template_name)
        if override is not None:
            subject = override.subject
            updated_at = override.updated_at
            edited = True
        else:
            file_subject, _ = _read_file_template(template_name)
            subject = file_subject or template_name
            updated_at = None
            edited = False
        rows.append({
            'template_name': template_name,
            'subject': subject,
            'edited': edited,
            'updated_at': updated_at,
            'sent_when': _sent_when(template_name),
        })
    return render(
        request,
        'studio/email_templates/list.html',
        {'rows': rows},
    )


@staff_required
def email_template_edit(request, template_name):
    """Edit one template: GET prefills the form, POST upserts the row."""
    initial = _resolve_initial(template_name)
    if initial is None:
        raise Http404(f'Unknown email template: {template_name}')

    if request.method == 'POST':
        subject = request.POST.get('subject', '').strip()
        body_markdown = request.POST.get('body_markdown', '')
        footer_note = request.POST.get('footer_note', '').strip()

        if not subject:
            messages.error(request, 'Subject is required.')
            initial['subject'] = subject
            initial['body_markdown'] = body_markdown
            initial['footer_note'] = footer_note
            return render(
                request,
                'studio/email_templates/edit.html',
                {
                    'template_name': template_name,
                    'initial': initial,
                    'sent_when': _sent_when(template_name),
                },
            )
        if not body_markdown.strip():
            messages.error(request, 'Body is required.')
            initial['subject'] = subject
            initial['body_markdown'] = body_markdown
            initial['footer_note'] = footer_note
            return render(
                request,
                'studio/email_templates/edit.html',
                {
                    'template_name': template_name,
                    'initial': initial,
                    'sent_when': _sent_when(template_name),
                },
            )

        EmailTemplateOverride.objects.update_or_create(
            template_name=template_name,
            defaults={
                'subject': subject,
                'body_markdown': body_markdown,
                'footer_note': footer_note,
                'updated_by': request.user,
            },
        )
        messages.success(
            request,
            f'Saved override for "{template_name}".',
        )
        return redirect('studio_email_template_list')

    return render(
        request,
        'studio/email_templates/edit.html',
        {
            'template_name': template_name,
            'initial': initial,
            'sent_when': _sent_when(template_name),
        },
    )


@staff_required
@require_POST
def email_template_reset(request, template_name):
    """Delete the override so the next send falls back to the file."""
    initial = _resolve_initial(template_name)
    if initial is None:
        raise Http404(f'Unknown email template: {template_name}')

    deleted, _ = EmailTemplateOverride.objects.filter(
        template_name=template_name,
    ).delete()
    if deleted:
        messages.success(
            request,
            f'Reverted "{template_name}" to the filesystem default.',
        )
    else:
        messages.info(
            request,
            f'No override existed for "{template_name}".',
        )
    return redirect('studio_email_template_list')


@staff_required
@require_POST
def email_template_preview(request, template_name):
    """Render the preview HTML for an in-progress edit.

    Posts ``subject``, ``body_markdown``, ``footer_note`` from the editor
    and returns the wrapped HTML that the iframe ``srcdoc`` uses.
    """
    if not _template_exists(template_name):
        raise Http404(f'Unknown email template: {template_name}')

    subject = request.POST.get('subject', '')
    body_markdown = request.POST.get('body_markdown', '')
    footer_note = request.POST.get('footer_note', '')

    try:
        html = _render_preview_html(
            template_name,
            subject,
            body_markdown,
            footer_note,
        )
    except Exception as exc:
        # Don't leak the operator's typo as a 500. Render a minimal
        # error block in the iframe so the editor stays usable.
        logger.warning(
            'Email template preview render failed for %s: %s',
            template_name, exc,
        )
        html = (
            '<!DOCTYPE html><html><body style="font-family:sans-serif;'
            'padding:1rem;color:#900">'
            '<p>Preview failed to render:</p>'
            f'<pre>{escape(exc)}</pre>'
            '</body></html>'
        )
        return HttpResponse(html, content_type='text/html', status=200)

    return HttpResponse(html, content_type='text/html')


@staff_required
@require_POST
def email_template_send_test(request, template_name):
    """Send a real email to the logged-in operator using the saved data.

    Always uses the persisted state -- override if present, file otherwise.
    The point is to verify deliverability after edits, not to preview an
    unsaved draft (the iframe already does that).
    """
    if not _template_exists(template_name):
        raise Http404(f'Unknown email template: {template_name}')

    if not request.user.email:
        return HttpResponseBadRequest(
            'Logged-in user has no email address; cannot send test.',
        )

    # Strip ``user_name`` / ``user_email`` / ``site_url`` / ``site_name``
    # from the placeholder context so the operator's real values flow
    # through (EmailService injects those from the user). The remaining
    # keys (``verify_url``, ``tier_name``, etc.) are still needed because
    # the templates reference them and there is no real source for them
    # in a manual test send.
    placeholder = get_preview_context(template_name)
    for k in ('user_name', 'user_email', 'site_url', 'site_name'):
        placeholder.pop(k, None)
    service = EmailService()
    try:
        log = service.send(request.user, template_name, placeholder)
    except EmailServiceError as exc:
        messages.error(request, f'Failed to send test email: {exc}')
        return redirect('studio_email_template_list')

    if log is None:
        # Unsubscribed user: send() returns None. Surface that to the
        # operator so they know the send was a no-op.
        messages.warning(
            request,
            'Test not sent: your account is marked unsubscribed.',
        )
    else:
        messages.success(
            request,
            f'Test email sent to {request.user.email}.',
        )
    return redirect('studio_email_template_list')
