"""Studio views for editing transactional email templates (issue #455).

Operators get a list page that surfaces every transactional template, and
an edit page with subject / body / footer fields plus an iframe-based live
preview. Saves persist into ``EmailTemplateOverride``; the render path
prefers the override row over the on-disk template, so deleting the row
reverts to the file.
"""

import logging

import frontmatter
from community_base.mail.models import EmailDelivery
from community_base.mail.service import MailError
from django.contrib import messages
from django.http import (
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
)
from django.shortcuts import redirect, render
from django.utils.html import escape
from django.views.decorators.http import require_POST

from accounts.utils.display import GREETING_FALLBACK, greeting_name
from content.utils.template_comments import describe_offender, find_token_offenders
from email_app.models import EmailTemplateOverride
from email_app.package_mail import send_package_mail
from email_app.services.context_guard import looks_like_url
from email_app.services.email_classification import (
    EMAIL_KIND_PROMOTIONAL,
    EmailClassificationError,
    classify_email_type,
)
from email_app.services.email_service import TEMPLATES_DIR
from email_app.services.preview_contexts import (
    RECIPIENT_CHOICES,
    RECIPIENT_NAMED,
    RECIPIENT_NO_NAME,
    get_preview_context,
)
from studio.decorators import staff_required

logger = logging.getLogger(__name__)

# A Studio test send marks its durable delivery with this category (A1.2
# remainder slice 4) so the worker resolver knows the delivery has no
# producer relation: test sends persist scalar placeholder copy only and
# must never dispatch a relation-dependent production resolver.
STUDIO_TEST_SEND_CATEGORY = "studio_test_send"


def _durable_test_context(context):
    """Drop URL-bearing placeholder values from a test-send context.

    Issue #1613: a durable context may not carry a rendered link, and the
    preview placeholders are demo links by design. A test send probes
    deliverability with the persisted subject and body copy, so the
    URL-shaped keys are removed and only the scalar copy travels in the
    durable context. Uses the same predicate as the send-time guard, so a
    stripped context can never trip it.
    """

    if isinstance(context, dict):
        return {
            key: _durable_test_context(value)
            for key, value in context.items()
            if not looks_like_url(value)
        }
    if isinstance(context, (list, tuple)):
        return [_durable_test_context(item) for item in context]
    return context


# Display order on the list page. Mirrors the order operators usually
# think about (onboarding -> account -> billing -> events -> imports).
TEMPLATE_DISPLAY_ORDER = [
    'welcome',
    'free_welcome',
    'account_deletion_request',
    'account_deletion_completed',
    'email_verification_signup',
    'email_verification_subscribe',
    'password_reset',
    'community_invite',
    'lead_magnet_delivery',
    'download_delivery',
    'event_registration',
    'event_reminder',
    'event_recap_ready',
    'cancellation',
    'checkout_payment_failed',
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
    'account_deletion_completed': 'Sent after a superuser completes an accepted local account-deletion request.',
    'account_deletion_request': 'Sent when a signed-in member asks the team to delete their local account.',
    'account_email_change_confirm': 'Sent when a member requests an account email-address change.',
    'account_email_changed_notice': 'Sent to the previous address after an account email change succeeds.',
    'basic_welcome': 'Sent when a member first receives Basic membership access.',
    'bookclub_book_summary': 'Sent to book-access members when an organizer publishes a full-book summary.',
    'bookclub_chapter_summary': 'Sent to book-access members when an organizer publishes a chapter summary.',
    'cancellation': 'Sent when a paid membership is scheduled to cancel.',
    'cofounder_welcome': 'Sent when a cofounder membership signup is completed.',
    'community_invite': 'Sent when an eligible member is invited to the private community.',
    'checkout_payment_failed': 'Sent when a delayed Checkout payment fails before membership or course access is granted.',
    'download_delivery': 'Sent when a visitor requests delivery of a downloadable resource.',
    'email_verification_signup': 'Sent when a new account must verify its email address.',
    'email_verification_signup_reminder': 'Sent when an unverified account is reminded to verify its email address.',
    'email_verification_subscribe': 'Sent when a newsletter subscriber must verify their email address.',
    'email_verification_subscribe_reminder': 'Sent when an unverified newsletter subscriber is reminded to verify.',
    'event_cancelled': 'Sent to registered attendees when an event is cancelled.',
    'event_recording_ready': 'Sent to event hosts when a recording is ready for Studio review.',
    'event_recap_ready': 'Sent to active registrants when staff explicitly announces a verified public event recap.',
    'event_registration': 'Sent when a member successfully registers for an event.',
    'event_reminder': 'Sent to registered attendees before an upcoming event.',
    'event_rescheduled': 'Sent to registered attendees when an event schedule changes.',
    'event_workshop_ready': 'Sent to registered attendees when the related workshop is ready.',
    'free_welcome': 'Sent when a new Free member account is created.',
    'lead_magnet_delivery': 'Sent when a visitor requests a free lead-magnet resource.',
    'maven_cohort_removal_notification': 'Sent to staff when a member is removed from a Maven cohort.',
    'maven_enrollment_notification': 'Sent to staff when a new Maven enrollment occurrence is admitted.',
    'maven_welcome': 'Sent when a Maven enrollee receives course access.',
    'onboarding_reminder': 'Sent when a paid member has not completed onboarding after one week.',
    'password_reset': 'Sent when someone requests a password-reset link.',
    'payment_failed': 'Sent when a paid membership invoice payment fails.',
    'payment_grace_expired_member': 'Sent when an unresolved monthly payment grace period expires and the member\'s paid base tier changes to Free.',
    'payment_grace_failure_member': 'Sent when a qualifying monthly payment failure starts a grace period for the member.',
    'payment_grace_failure_team': 'Sent to staff when a qualifying monthly payment failure starts a grace period for a member.',
    'payment_grace_reminder_member': 'Sent to the member 48 hours before an unresolved monthly payment grace period expires.',
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


def _render_preview_html(
    template_name,
    subject,
    body_markdown,
    footer_note,
    recipient=RECIPIENT_NAMED,
):
    """Render the preview through the same chrome the real send uses.

    Variables in the body are filled with placeholder values from
    ``preview_contexts.PREVIEW_CONTEXTS`` so no real user data leaks.

    ``recipient='no_name'`` shows the copy as a member with no name on file
    receives it (issue #1591).
    """
    from django.template import Context, Template
    from django.template.loader import render_to_string

    from content.utils.markdown import render_email_markdown

    placeholder = get_preview_context(template_name, recipient=recipient)
    # ``user_name`` and ``user_email`` are also auto-injected by the send
    # path for real mail; mirror that here so previews look the same.
    if recipient == RECIPIENT_NO_NAME:
        placeholder.setdefault('user_name', GREETING_FALLBACK)
        placeholder.setdefault('member_name', GREETING_FALLBACK)
        placeholder.setdefault('user_email', 'no-name@example.com')
    else:
        placeholder.setdefault('user_name', 'Ada')
        placeholder.setdefault('user_email', 'ada@example.com')
    placeholder.setdefault('site_url', 'https://aishippinglabs.com')
    placeholder.setdefault('site_name', 'AI Shipping Labs')

    rendered_subject = Template(subject or '').render(Context(placeholder))
    rendered_body = Template(body_markdown or '').render(Context(placeholder))
    body_html = render_email_markdown(rendered_body)

    try:
        is_promotional = (
            classify_email_type(template_name) == EMAIL_KIND_PROMOTIONAL
        )
    except EmailClassificationError:
        is_promotional = False

    return render_to_string(
        'email_app/base_email.html',
        {
            'subject': rendered_subject,
            'body_html': body_html,
            # Show a fake unsubscribe link so the operator sees the
            # footer chrome that real recipients will get.
            'unsubscribe_url': (
                'https://aishippinglabs.com/api/unsubscribe?token=preview'
                if is_promotional
                else None
            ),
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


def _template_syntax_errors(subject, body_markdown, footer_note):
    """Return operator-facing errors for any construct Django cannot lex.

    Shares ``content.utils.template_comments`` with the repository file lint, so
    the rule applied to ``email_app/email_templates/*.md`` on disk and the rule
    applied to operator-authored ``EmailTemplateOverride`` copy are literally
    the same function. Fields are checked in form order and every offender is
    reported, so one save surfaces every problem.
    """
    return [
        describe_offender(offender)
        for field_source in (subject, body_markdown, footer_note)
        for offender in find_token_offenders(field_source)
    ]


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

        def reject(errors):
            """Re-render the form with the operator's text intact, writing nothing."""
            for error in errors:
                messages.error(request, error)
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

        if not subject:
            return reject(['Subject is required.'])
        if not body_markdown.strip():
            return reject(['Body is required.'])

        # Operator copy is compiled by the same ``django.template.Template``
        # call as the shipped ``.md`` files, so it needs the same single-line
        # rule the file lint enforces. A multi-line or unclosed construct is
        # not lexed by Django and its raw source ships to the recipient.
        syntax_errors = _template_syntax_errors(subject, body_markdown, footer_note)
        if syntax_errors:
            return reject(syntax_errors)

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
    # Unknown values fall back to the named recipient rather than erroring:
    # a stale tab or a hand-rolled POST must not break the editor.
    recipient = request.POST.get('recipient') or RECIPIENT_NAMED
    if recipient not in dict(RECIPIENT_CHOICES):
        recipient = RECIPIENT_NAMED

    try:
        html = _render_preview_html(
            template_name,
            subject,
            body_markdown,
            footer_note,
            recipient=recipient,
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

    The send goes through the durable package path (A1.2 remainder slice
    4): ``sent`` means the durable delivery exists and the SES outcome
    lands from the worker. Placeholder links are stripped (#1613) and the
    operator's own greeting scalar travels in the durable context.
    """
    if not _template_exists(template_name):
        raise Http404(f'Unknown email template: {template_name}')

    if not request.user.email:
        return HttpResponseBadRequest(
            'Logged-in user has no email address; cannot send test.',
        )

    # Strip ``user_name`` / ``user_email`` / ``site_url`` / ``site_name``
    # from the placeholder context so the operator's real values flow
    # through (the send path injects those from the recipient). The
    # remaining scalar keys (``tier_name``, etc.) keep the copy realistic;
    # URL-bearing placeholders are dropped by ``_durable_test_context``
    # because a durable context may not carry a rendered link (#1613).
    placeholder = get_preview_context(template_name)
    for k in ('user_name', 'user_email', 'site_url', 'site_name'):
        placeholder.pop(k, None)
    # Issue #1591: the greeting is resolved for the operator, never an
    # email handle; persisting it as a scalar keeps the worker render
    # identical to what the synchronous send used to inject.
    placeholder['user_name'] = greeting_name(request.user) or GREETING_FALLBACK
    try:
        delivery = send_package_mail(
            request.user,
            template_name,
            _durable_test_context(placeholder),
            category=STUDIO_TEST_SEND_CATEGORY,
        )
    except MailError as exc:
        # Guard refusal or unknown purpose: nothing durable exists, so
        # the loud operator message IS the failure surface.
        logger.warning(
            'Studio test send refused for %s: %s', template_name, exc,
        )
        messages.error(request, f'Failed to send test email: {exc}')
        return redirect('studio_email_template_list')

    if delivery.state == EmailDelivery.State.SUPPRESSED:
        # Preference suppression: the delivery is durable but no mail
        # will go out. Surface that to the operator so they know the
        # send was a no-op.
        messages.warning(
            request,
            'Test not sent: your account is marked unsubscribed.',
        )
    else:
        # Honest convention: the delivery exists; the SES outcome and
        # the ``EmailLog`` audit row land from the worker.
        messages.success(
            request,
            f'Test email sent to {request.user.email}.',
        )
    return redirect('studio_email_template_list')
