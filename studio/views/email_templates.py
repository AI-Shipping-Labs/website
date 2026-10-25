"""Studio Relay template catalog, draft, publish, and preview views."""

import logging

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils.html import escape
from django.views.decorators.http import require_POST

from email_app.relay_versions import template_version_for
from email_app.services.preview_contexts import (
    RECIPIENT_CHOICES,
    RECIPIENT_NAMED,
    RECIPIENT_NO_NAME,
    get_preview_context,
)
from email_app.services.relay_templates import (
    INTERNAL_TEMPLATE_KEYS,
    READ_ONLY_TEMPLATE_KEYS,
    RelayTemplateError,
    configured_template_client,
)
from studio.decorators import staff_required

logger = logging.getLogger(__name__)

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
    'content_comment',
    'course_announcement',
    'submission_recorded',
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
    'campaign-broadcast': 'Sent by staff-created campaigns and explicit campaign test sends.',
    'cofounder_welcome': 'Sent when a cofounder membership signup is completed.',
    'coursework.pool_ready': 'Sent to each member of a self-paced peer-review group when the group forms, with links to the projects they review.',
    'coursework.review_assigned': 'Sent to each learner in a dated cohort when staff assigns the peer reviews for a project.',
    'coursework.review_received': 'Sent to a learner when a peer submits a review of their project.',
    'coursework.review_window_expired': 'Sent to a learner when a self-paced review window closes before they submitted a review they owed.',
    'course_unenroll_notification': 'Sent to staff when a learner unenrolls from a course, leaves a cohort, or loses course access.',
    'community_invite': 'Sent when an eligible member is invited to the private community.',
    'content_comment': 'Sent to linked course instructors and workshop authors when a member comments or replies on their content.',
    'course_announcement': 'Sent once to actively enrolled learners when staff first publishes a course announcement.',
    'checkout_payment_failed': 'Sent when a delayed Checkout payment fails before membership or course access is granted.',
    'download_delivery': 'Sent when a visitor requests delivery of a downloadable resource.',
    'email_verification_signup': 'Sent when a new account must verify its email address.',
    'email_verification_signup_reminder': 'Sent when an unverified account is reminded to verify its email address.',
    'email_verification_subscribe': 'Sent when a newsletter subscriber must verify their email address.',
    'email_verification_subscribe_reminder': 'Sent when an unverified newsletter subscriber is reminded to verify.',
    'event_cancelled': 'Sent to registered attendees when an event is cancelled.',
    'event_recording_ready': 'Sent to event hosts when a recording is ready for Studio review.',
    'event_recap_ready': 'Sent to everyone interested in an event (registrants, linked cohort members, linked book club readers) once, automatically, when its recap becomes ready, or when staff announces the recap manually.',
    'event_registration': 'Sent when a member successfully registers for an event.',
    'event_reminder': 'Sent to registered attendees and linked cohort members 24 hours and 20 minutes before an upcoming event.',
    'event_rescheduled': 'Sent to registered attendees when an event schedule changes.',
    'event_workshop_ready': 'Sent to registered attendees when the related workshop is ready.',
    'free_welcome': 'Sent when a new Free member account is created.',
    'lead_magnet_delivery': 'Sent when a visitor requests a free lead-magnet resource.',
    'maven_cohort_removal_notification': 'Sent to staff after a Maven cohort removal, listing what changed.',
    'maven_enrollment_notification': 'Sent to staff when a new Maven enrollment occurrence is admitted.',
    'maven_removal': 'Sent to a student after a Maven cohort removal ends their course and membership access.',
    'maven_welcome': 'Sent when a Maven enrollee receives course access.',
    'onboarding_submitted': 'Sent to members upon completing their onboarding submission.',
    'onboarding_reminder': 'Sent when a paid member has not completed onboarding after one week.',
    'password_reset': 'Sent when someone requests a password-reset link.',
    'payment_failed': 'Sent when a paid membership invoice payment fails.',
    'payment_grace_expired_member': 'Sent when an unresolved monthly payment grace period expires and the member\'s paid base tier changes to Free.',
    'payment_grace_failure_member': 'Sent when a qualifying monthly payment failure starts a grace period for the member.',
    'payment_grace_failure_team': 'Sent to staff when a qualifying monthly payment failure starts a grace period for a member.',
    'payment_grace_reminder_member': 'Sent to the member 48 hours before an unresolved monthly payment grace period expires.',
    'plan_shared': 'Sent when staff shares a sprint plan with its member.',
    'submission_recorded': 'Sent when staff record a homework or project submission on a member\'s behalf and choose to notify them.',
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
    return TEMPLATE_SENT_WHEN.get(template_name, "Relay-managed template.")


def _ordered_templates(templates):
    order = {name: index for index, name in enumerate(TEMPLATE_DISPLAY_ORDER)}
    return sorted(templates, key=lambda row: (order.get(row.get("key"), 999), row.get("key", "")))


def _relay_error_message(error):
    return f"Relay template operation failed ({error.code}). Try again or check Relay health."


@staff_required
def email_template_list(request):
    """List editable Relay templates without any local fallback."""
    try:
        visible = []
        for row in configured_template_client().templates():
            if row.get("key") not in INTERNAL_TEMPLATE_KEYS:
                visible.append(row)
        rows = _ordered_templates(visible)
    except RelayTemplateError as error:
        rows = ()
        messages.error(request, _relay_error_message(error))
    for row in rows:
        row["template_name"] = row["key"]
        row["sent_when"] = _sent_when(row["key"])
        row["read_only"] = row["key"] in READ_ONLY_TEMPLATE_KEYS
        row["active_send_version"] = template_version_for(row["key"])
    return render(request, "studio/email_templates/list.html", {"rows": rows})


def _editor_context(template_name, template, versions, rendered=None):
    return {
        "template_name": template_name,
        "initial": template,
        "versions": versions,
        "rendered": rendered,
        "sent_when": _sent_when(template_name),
        "active_send_version": template_version_for(template_name),
    }


def _draft_from_post(request, existing):
    draft = dict(existing)
    editable = (
        "name", "description", "subject", "html_body", "text_body",
        "markdown_body", "category", "default_sender_id",
    )
    for field in editable:
        draft[field] = request.POST.get(field, "")
    draft["is_active"] = request.POST.get("is_active") == "on"
    draft["markdown_layout_context"] = (
        request.POST.get("markdown_layout_context") == "on"
    )
    return draft


def _require_manageable_template(template_name, *, allow_read_only=False):
    forbidden = INTERNAL_TEMPLATE_KEYS
    if not allow_read_only:
        forbidden |= READ_ONLY_TEMPLATE_KEYS
    if template_name in forbidden:
        raise Http404("This Relay template cannot be managed in Studio.")


@staff_required
def email_template_edit(request, template_name):
    """Read or save a Relay draft. Publishing remains a separate action."""
    _require_manageable_template(template_name)
    try:
        client = configured_template_client()
        template = client.template(template_name)
        if request.method == "POST":
            template = client.save_draft(template_name, _draft_from_post(request, template))
            messages.success(request, f'Saved Relay draft for "{template_name}".')
        versions = client.versions(template_name)
    except RelayTemplateError as error:
        messages.error(request, _relay_error_message(error))
        return redirect("studio_email_template_list")
    return render(request, "studio/email_templates/edit.html", _editor_context(template_name, template, versions))


@staff_required
@require_POST
def email_template_publish(request, template_name):
    """Publish the saved Relay draft as a new immutable version."""
    _require_manageable_template(template_name)
    try:
        version = configured_template_client().publish(template_name)
    except RelayTemplateError as error:
        messages.error(request, _relay_error_message(error))
    else:
        active = template_version_for(template_name)
        messages.success(
            request,
            f'Published "{template_name}" as Relay version {version}. '
            f'Website sends remain pinned to v{active} until a release changes the pin.',
        )
    return redirect("studio_email_template_edit", template_name=template_name)


@staff_required
@require_POST
def email_template_preview(request, template_name):
    """Render a saved draft or immutable version through Relay."""
    _require_manageable_template(template_name, allow_read_only=True)
    recipient = request.POST.get("recipient") or RECIPIENT_NAMED
    if recipient not in dict(RECIPIENT_CHOICES):
        recipient = RECIPIENT_NAMED
    context = get_preview_context(template_name, recipient=recipient)
    _add_preview_recipient(context, recipient)
    raw_version = request.POST.get("version", "").strip()
    try:
        version = None
        if raw_version:
            version = int(raw_version)
        rendered = configured_template_client().preview(
            template_name, context, version=version, draft=not raw_version,
        )
        return HttpResponse(rendered["html_body"], content_type="text/html")
    except (RelayTemplateError, ValueError) as error:
        code = "invalid_version"
        if isinstance(error, RelayTemplateError):
            code = error.code
        logger.warning("Relay template preview failed for %s: %s", template_name, code)
        html = (
            '<!DOCTYPE html><html><body style="font-family:sans-serif;padding:1rem;color:#900">'
            '<p>Relay preview failed. Save the draft or select a published version, then retry.</p>'
            f'<pre>{escape(code)}</pre></body></html>'
        )
        return HttpResponse(html, content_type="text/html", status=502)


def _add_preview_recipient(context, recipient):
    if recipient == RECIPIENT_NO_NAME:
        context.setdefault("user_name", "there")
        context.setdefault("member_name", "there")
        context.setdefault("user_email", "no-name@example.com")
    else:
        context.setdefault("user_name", "Ada")
        context.setdefault("user_email", "ada@example.com")
    context.setdefault("site_url", "https://aishippinglabs.com")
    context.setdefault("site_name", "AI Shipping Labs")
