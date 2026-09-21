"""Transitional site hooks for community_base.mail (plan issue A1.2).

The package owns the durable delivery, the worker and the SES transport.
These hooks keep the site behavior the EmailService path had: the EmailLog
audit row, EmailTemplateOverride precedence, the newsletter opt-out, the
promotional one-click unsubscribe URL and the issue #450 verify-email
footer. Every hook receives package objects, never request state.
"""

from __future__ import annotations

import logging

from community_base.jobs.runner import PermanentJobError
from community_base.mail.service import MailError
from django.utils import timezone

from accounts.utils.display import GREETING_FALLBACK, greeting_name
from email_app.services.email_classification import (
    EMAIL_KIND_PROMOTIONAL,
    EmailClassificationError,
    classify_email_type,
)
from email_app.services.email_service import (
    EMAIL_TYPES_WITHOUT_VERIFY_FOOTER,
    UNSUBSCRIBED_AT_SEND,
    VERIFY_FOOTER_TOKEN_EXPIRY_HOURS,
)

logger = logging.getLogger(__name__)

# Package Course rows store ``cb_curriculum.course``. Rows queued before
# the curriculum cutover still say ``content.course``.
_COURSE_RELATED_TYPES = frozenset({"cb_curriculum.course", "content.course"})


def _is_course_related(delivery):
    return delivery.related_object_type in _COURSE_RELATED_TYPES


def preference_resolver(*, purpose, category, to, user):
    """Port of ``EmailService._delivery_decision`` as a package hook.

    Promotional sends respect the global newsletter unsubscribe flag;
    transactional sends stay deliverable for account continuity. Returning
    the reason code (rather than ``False``) keeps the historical skip reason
    visible as the delivery's ``reason_code``. An unknown purpose raises
    ``MailError`` — the callers' soft-fail set — instead of the raw
    classification error, so a typo'd slug degrades the same way it did on
    the EmailService path.
    """

    try:
        email_kind = classify_email_type(purpose)
    except EmailClassificationError as error:
        logger.error("Unknown mail purpose %s", purpose)
        raise MailError(f"unknown mail purpose: {purpose}") from error
    if email_kind == EMAIL_KIND_PROMOTIONAL and getattr(user, "unsubscribed", False):
        return UNSUBSCRIBED_AT_SEND
    return None


def unsubscribe_url_builder(delivery):
    """One-click unsubscribe URL for promotional mail; ``None`` otherwise.

    Transactional mail must not carry an unsubscribe action, mirroring the
    old ``email_kind == EMAIL_KIND_PROMOTIONAL`` gate in ``send``.
    """

    try:
        email_kind = classify_email_type(delivery.purpose)
    except EmailClassificationError:
        return None
    if email_kind != EMAIL_KIND_PROMOTIONAL:
        return None
    user = delivery.recipient_user
    if user is None or not getattr(user, "pk", None):
        return None

    from accounts.utils.tokens import generate_user_action_token  # noqa: PLC0415
    from integrations.config import site_base_url  # noqa: PLC0415

    token = generate_user_action_token(user.pk, "unsubscribe")
    return (
        f"{site_base_url().rstrip('/')}/api/unsubscribe?token={token}"
    )


def verify_email_url_builder(delivery):
    """Issue #450 footer link for unverified recipients; ``None`` otherwise.

    The decision runs in the worker against the recipient row as it is
    ``now`` — slightly fresher than the old send-time instance, which the
    old docstring already warned could be stale.
    """

    user = delivery.recipient_user
    if user is None or not getattr(user, "pk", None):
        return None
    if getattr(user, "email_verified", True):
        return None
    if delivery.purpose in EMAIL_TYPES_WITHOUT_VERIFY_FOOTER:
        return None

    from accounts.views.auth import _generate_verification_token  # noqa: PLC0415
    from integrations.config import site_base_url  # noqa: PLC0415

    token = _generate_verification_token(
        user.pk,
        expiry_hours=VERIFY_FOOTER_TOKEN_EXPIRY_HOURS,
    )
    return (
        f"{site_base_url().rstrip('/')}/api/verify-email?token={token}"
    )


def _related_event(delivery):
    """Load the delivery's ``events.event`` relation, ``None`` when absent."""

    if delivery.related_object_type != "events.event":
        return None
    from events.models import Event  # noqa: PLC0415

    return Event.objects.filter(pk=delivery.related_object_id).first()


def _resolve_auth_links(delivery, context):
    """Mint the signup-verification and password-reset bearer links.

    The auth sends are member-facing, so the worker also restores the
    issue #1591 greeting rule (``greeting_name`` — never an email handle,
    ``there`` when nameless) that the synchronous renderer used to inject
    for every template; the package's built-in display name has no
    nameless fallback.
    """

    user = delivery.recipient_user
    if user is None or not getattr(user, "pk", None):
        return
    _member_greeting(delivery, context)

    from accounts.utils.tokens import generate_password_reset_token  # noqa: PLC0415
    from integrations.config import site_base_url  # noqa: PLC0415

    base_url = site_base_url().rstrip("/")
    if delivery.purpose == "password_reset":
        token = generate_password_reset_token(user, expiry_hours=1)
        context["reset_url"] = f"{base_url}/api/password-reset?token={token}"
        return

    from accounts.views.auth import _generate_verification_token  # noqa: PLC0415

    token = _generate_verification_token(
        user.pk,
        return_path=context.get("return_path"),
    )
    context["verify_url"] = f"{base_url}/api/verify-email?token={token}"


def _resolve_email_change_confirm(delivery, context):
    """Validate the related request and mint its confirm URL.

    Deliveries created before #1613 carry no relation; their stored
    legacy random-token link may finish within the request's original
    24-hour lifetime, so they pass through unchanged. Anything newer
    must present a live, matching ``EmailChangeRequest`` or the send
    fails closed with no token or URL in the error.
    """

    from accounts.models import EmailChangeRequest  # noqa: PLC0415
    from accounts.services.email_change import (  # noqa: PLC0415
        derive_email_change_token,
        hash_email_change_token,
    )
    from integrations.config import site_base_url  # noqa: PLC0415

    if delivery.related_object_type != "accounts.emailchangerequest":
        return

    request_obj = (
        EmailChangeRequest.objects
        .filter(pk=delivery.related_object_id)
        .first()
    )
    if (
        request_obj is None
        or delivery.recipient_user_id is None
        or request_obj.user_id != delivery.recipient_user_id
        or request_obj.new_email != delivery.recipient_email
        or request_obj.confirmed_at is not None
        or request_obj.invalidated_at is not None
    ):
        raise PermanentJobError("email_change_request_unusable")
    if request_obj.expires_at <= timezone.now():
        raise PermanentJobError("email_change_request_expired")
    token = derive_email_change_token(request_obj)
    if request_obj.token_hash != hash_email_change_token(token):
        raise PermanentJobError("email_change_request_token_mismatch")

    context["confirm_url"] = (
        f"{site_base_url().rstrip('/')}"
        f"/account/change-email/confirm?token={token}"
    )


def _resolve_email_changed_notice(delivery, context):
    """Mint the security notice's account link (no binding required)."""

    from integrations.config import site_base_url  # noqa: PLC0415

    context["account_url"] = f"{site_base_url().rstrip('/')}/account/"


def _resolve_privacy_studio_url(delivery, context):
    """Mint the Studio member link for the deletion request to support."""

    if delivery.recipient_user_id is None:
        raise PermanentJobError("privacy_request_user_missing")

    from django.urls import reverse  # noqa: PLC0415

    from integrations.config import site_base_url  # noqa: PLC0415

    context["studio_member_url"] = (
        f"{site_base_url().rstrip('/')}"
        f"{reverse('studio_user_detail', kwargs={'user_id': delivery.recipient_user_id})}"
        "#privacy-deletion-request"
    )


def _resolve_recap_context(delivery, context):
    """Mint recap links from the saved Event at delivery time."""

    from events.services.event_recap_notification import (  # noqa: PLC0415
        absolute_recap_url,
    )
    from integrations.config import site_base_url  # noqa: PLC0415

    event = _related_event(delivery)
    if event is None:
        raise PermanentJobError("recap_event_missing")
    recap_url = absolute_recap_url(event)
    if not recap_url:
        raise PermanentJobError("recap_url_missing")
    context["event_title"] = event.title
    context["recap_url"] = recap_url
    context["event_url"] = (
        f"{site_base_url().rstrip('/')}{event.get_absolute_url()}"
    )


def _resolve_followup_context(delivery, context):
    """Mint the whole follow-up context from the saved Event."""

    from events.services.post_event_mail import (  # noqa: PLC0415
        PostEventMailContextError,
        build_followup_context,
    )

    event = _related_event(delivery)
    if event is None:
        raise PermanentJobError("followup_event_missing")
    try:
        context.update(build_followup_context(event))
    except PostEventMailContextError as error:
        raise PermanentJobError(error.reason) from error


def _related_plan(delivery):
    """Load the delivery's ``plans.plan`` relation, ``None`` when absent."""

    if delivery.related_object_type != "plans.plan":
        return None
    from plans.models import Plan  # noqa: PLC0415

    return (
        Plan.objects.select_related("sprint")
        .filter(pk=delivery.related_object_id)
        .first()
    )


def _related_sprint(delivery):
    """Load the delivery's ``plans.sprint`` relation, ``None`` when absent."""

    if delivery.related_object_type != "plans.sprint":
        return None
    from plans.models import Sprint  # noqa: PLC0415

    return Sprint.objects.filter(pk=delivery.related_object_id).first()


def _resolve_event_reminder_context(delivery, context):
    """Mint the whole event-reminder context from the Event and recipient.

    A1.2 slice 4: the reminder email's links and its recipient-local time
    never sit in the durable row. ``format_user_datetime`` needs the
    recipient, so the worker formats the raw start datetime exactly as the
    old synchronous renderer did at send time (issue #666 guardrail).
    """

    from accounts.services.timezones import (  # noqa: PLC0415
        build_timezone_account_url,
        build_timezone_email_line,
        format_user_datetime,
    )
    from integrations.config import site_base_url  # noqa: PLC0415

    event = _related_event(delivery)
    if event is None:
        raise PermanentJobError("event_reminder_event_missing")
    user = delivery.recipient_user
    if user is None or not getattr(user, "pk", None):
        raise PermanentJobError("event_reminder_user_missing")
    base_url = site_base_url().rstrip("/")
    context["event_title"] = event.title
    context["event_datetime"] = format_user_datetime(
        event.start_datetime, user,
    )
    context["event_url"] = f"{base_url}{event.get_join_url()}"
    context["timezone_help"] = build_timezone_email_line(
        user, build_timezone_account_url(base_url),
    )


def _resolve_workshop_announcement_context(delivery, context):
    """Mint the whole workshop-announcement context from the Workshop.

    The announcement body embeds the description, which may legitimately
    contain links, so the producer persists only the relation (#1613) and
    the worker rebuilds the exact scalar context the old synchronous send
    passed to the template.
    """

    from notifications.services.notification_service import (  # noqa: PLC0415
        _get_body,
    )

    if delivery.related_object_type != "content.workshop":
        raise PermanentJobError("workshop_announcement_content_missing")
    from content.models import Workshop  # noqa: PLC0415

    workshop = (
        Workshop.objects.filter(pk=delivery.related_object_id).first()
    )
    if workshop is None:
        raise PermanentJobError("workshop_announcement_content_missing")
    context["workshop_title"] = workshop.title
    context["workshop_slug"] = workshop.slug
    context["workshop_description"] = _get_body(workshop)
    # The template prepends the render-only ``site_url``.
    context["workshop_url"] = workshop.get_absolute_url()


def _resolve_plan_shared_context(delivery, context):
    """Mint the whole plan-shared context from the saved Plan."""

    from django.urls import reverse  # noqa: PLC0415

    from integrations.config import site_base_url  # noqa: PLC0415

    plan = _related_plan(delivery)
    if plan is None:
        raise PermanentJobError("plan_shared_plan_missing")
    context["sprint_name"] = plan.sprint.name
    context["plan_url"] = (
        f"{site_base_url().rstrip('/')}"
        f"{reverse('my_plan_detail', kwargs={'sprint_slug': plan.sprint.slug, 'plan_id': plan.pk})}"
    )


# A1.2 slice 1: the four internal staff heads-ups. Each stores scalar
# inputs only (#1613) — the subject member's id under a per-purpose key,
# and for the paid-signup note the raw Stripe object ids.
_STAFF_NOTIFICATION_PURPOSES = frozenset({
    "staff_signup_notification",
    "maven_enrollment_notification",
    "maven_cohort_removal_notification",
    "slack_join_notification",
})

# Stripe object ids always carry their resource prefix; the stored
# scalars substitute "—" when an id is absent, so the prefix check is
# what separates a mintable dashboard link from plain copyable text.
_STRIPE_ID_PREFIXES = ("cus_", "pi_", "sub_")


def _staff_subject_user_id(context):
    """The subject member's pk from the per-purpose scalar key, if real."""

    for key in ("user_id", "enrolled_user_id", "removed_user_id"):
        text = str(context.get(key) or "").strip()
        if text.isdigit():
            return text
    return None


def _resolve_staff_notification_context(delivery, context):
    """Mint the staff heads-ups' Studio and Stripe dashboard links.

    A1.2 slice 1: the four internal notification purposes persist scalar
    inputs only (#1613) and the worker re-mints the exact links the old
    synchronous send built from the same inputs. A missing subject id or
    Stripe object id degrades exactly as the old builders did — the
    template renders the bare id (or nothing) — so this resolver raises
    no binding error for these best-effort internal notes.
    """

    from integrations.config import get_config, site_base_url  # noqa: PLC0415

    base_url = site_base_url().rstrip("/")
    user_id = _staff_subject_user_id(context)
    if user_id:
        context["studio_user_url"] = f"{base_url}/studio/users/{user_id}/"
    occurrence_id = str(context.get("occurrence_id") or "").strip()
    if occurrence_id.isdigit():
        context["studio_occurrence_url"] = (
            f"{base_url}/studio/maven-events/{occurrence_id}/"
        )

    from community.services.staff_notifications import _dashboard_url  # noqa: PLC0415

    account_id = (get_config("STRIPE_DASHBOARD_ACCOUNT_ID", "") or "").strip()
    for scalar_key, resource, url_key in (
        ("stripe_customer_id", "customers", "stripe_customer_url"),
        ("stripe_payment_intent_id", "payments", "stripe_payment_url"),
        ("stripe_subscription_id", "subscriptions", "stripe_subscription_url"),
    ):
        object_id = str(context.get(scalar_key) or "").strip()
        if object_id.startswith(_STRIPE_ID_PREFIXES):
            context[url_key] = _dashboard_url(account_id, resource, object_id)


def _resolve_bookclub_summary_context(delivery, context):
    """Rebuild the bookclub summary context from the related model.

    A1.2 slice 1: a summary excerpt may legitimately contain links, so —
    like the workshop announcement — the producer persists only the
    ``Book``/``Chapter`` relation and the worker rebuilds the exact
    scalar context the old synchronous send passed to the template.
    """

    from django.urls import reverse  # noqa: PLC0415

    from integrations.config import site_base_url  # noqa: PLC0415

    kind = delivery.related_object_type
    if kind == "bookclub.chapter":
        from bookclub.models import Chapter  # noqa: PLC0415
        from bookclub.summaries import summary_excerpt  # noqa: PLC0415

        chapter = (
            Chapter.objects.filter(pk=delivery.related_object_id)
            .select_related("book")
            .first()
        )
        if chapter is None:
            raise PermanentJobError("bookclub_summary_content_missing")
        book = chapter.book
        context["chapter_number"] = chapter.number
        context["chapter_title"] = chapter.title
        context["summary_line"] = summary_excerpt(chapter.summary) or (
            f'Read the summary for "{chapter.title}."'
        )
        summary_path = reverse(
            "bookclub_chapter_detail",
            kwargs={"slug": book.slug, "number": chapter.number},
        ) + "#summary"
    elif kind == "bookclub.book":
        from bookclub.models import Book  # noqa: PLC0415
        from bookclub.summaries import summary_excerpt  # noqa: PLC0415

        book = Book.objects.filter(pk=delivery.related_object_id).first()
        if book is None:
            raise PermanentJobError("bookclub_summary_content_missing")
        context["summary_line"] = summary_excerpt(book.summary) or (
            f'The full summary for "{book.title}" is ready.'
        )
        summary_path = reverse("bookclub_book_summary", kwargs={"slug": book.slug})
    else:
        raise PermanentJobError("bookclub_summary_content_missing")
    context["book_title"] = book.title
    context["summary_url"] = f"{site_base_url().rstrip('/')}{summary_path}"


# A1.2 slice 2: plans and payments member mail. Every producer persists a
# scalar-only context plus the natural relation (#1613): the worker reloads
# the relation and re-mints each absolute URL with the exact expression the
# old synchronous send used. A missing relation fails closed — a stale
# Sprint/Plan/Grace/Course must never deliver a broken link.


def _member_greeting(delivery, context):
    """Restore the legacy renderer's greeting for slice-2 member mail.

    The legacy synchronous renderer resolved ``user_name`` with the issue
    #1591 rule (``greeting_name`` — never an email handle, ``there`` when
    nameless); the package's built-in display name has neither property.
    Context values win over the render defaults, so slice-2 resolvers
    inject the old value without touching the stored context semantics.
    """

    context["user_name"] = (
        greeting_name(delivery.recipient_user) or GREETING_FALLBACK
    )


def _resolve_sprint_end_recap_context(delivery, context):
    """Mint the recap's plan, feedback and next-action links."""

    from django.urls import reverse  # noqa: PLC0415

    from integrations.config import site_base_url  # noqa: PLC0415
    from plans.models import Plan, Sprint  # noqa: PLC0415
    from questionnaires.models import Response  # noqa: PLC0415

    plan = _related_plan(delivery)
    if plan is None:
        raise PermanentJobError("sprint_end_recap_plan_missing")
    _member_greeting(delivery, context)
    base_url = site_base_url().rstrip("/")
    context["plan_url"] = (
        f"{base_url}"
        f"{reverse('my_plan_detail', kwargs={'sprint_slug': plan.sprint.slug, 'plan_id': plan.pk})}"
    )
    feedback_response_id = str(context.get("feedback_response_id") or "").strip()
    if feedback_response_id.isdigit():
        feedback_response = Response.objects.filter(
            pk=feedback_response_id,
        ).first()
        if feedback_response is None:
            raise PermanentJobError("sprint_end_recap_feedback_missing")
        context["feedback_url"] = (
            f"{base_url}"
            f"{reverse('sprint_feedback_fill', kwargs={'sprint_slug': plan.sprint.slug, 'response_id': feedback_response.pk})}"
        )
    kind = str(context.get("next_action_kind") or "")
    if kind:
        next_sprint_id = str(context.get("next_action_sprint_id") or "").strip()
        next_sprint = (
            Sprint.objects.filter(pk=next_sprint_id).first()
            if next_sprint_id.isdigit()
            else None
        )
        if next_sprint is None:
            raise PermanentJobError("sprint_end_recap_next_action_missing")
        if kind == "carry_over":
            next_plan_id = str(context.get("next_action_plan_id") or "").strip()
            next_plan = (
                Plan.objects.filter(pk=next_plan_id).first()
                if next_plan_id.isdigit()
                else None
            )
            if next_plan is None:
                raise PermanentJobError("sprint_end_recap_next_action_missing")
            next_path = reverse(
                "my_plan_detail",
                kwargs={"sprint_slug": next_sprint.slug, "plan_id": next_plan.pk},
            )
        elif kind == "prepare_plan":
            next_path = reverse(
                "cohort_board", kwargs={"sprint_slug": next_sprint.slug},
            )
        elif kind == "join_next":
            next_path = reverse(
                "sprint_detail", kwargs={"sprint_slug": next_sprint.slug},
            )
        else:
            raise PermanentJobError("sprint_end_recap_next_action_missing")
        context["next_action_url"] = f"{base_url}{next_path}"


def _resolve_sprint_partner_intro_context(delivery, context):
    """Re-add the board link and each partner's Slack profile link."""

    from django.urls import reverse  # noqa: PLC0415

    from community.services.slack_links import (  # noqa: PLC0415
        build_slack_profile_url,
    )
    from integrations.config import get_config, site_base_url  # noqa: PLC0415

    sprint = _related_sprint(delivery)
    if sprint is None:
        raise PermanentJobError("sprint_partner_intro_sprint_missing")
    base_url = site_base_url().rstrip("/")
    context["board_url"] = (
        f"{base_url}{reverse('cohort_board', kwargs={'sprint_slug': sprint.slug})}"
    )
    slack_team_id = (get_config("SLACK_TEAM_ID", "") or "").strip()
    for partner in context.get("partners") or []:
        partner["slack_profile_url"] = build_slack_profile_url(
            str(partner.get("slack_user_id") or "").strip(),
            slack_team_id,
        )


def _resolve_sprint_cadence_context(delivery, context):
    """Mint the cadence plan link (with its week anchor when stored)."""

    from django.urls import reverse  # noqa: PLC0415

    from integrations.config import site_base_url  # noqa: PLC0415
    from plans.models import Week  # noqa: PLC0415

    plan = _related_plan(delivery)
    if plan is None:
        raise PermanentJobError("sprint_cadence_plan_missing")
    _member_greeting(delivery, context)
    path = reverse(
        "my_plan_detail",
        kwargs={"sprint_slug": plan.sprint.slug, "plan_id": plan.pk},
    )
    week_id = str(context.get("week_id") or "").strip()
    if week_id:
        week = Week.objects.filter(pk=week_id, plan_id=plan.pk).first()
        if week is None:
            raise PermanentJobError("sprint_cadence_week_missing")
        path = f"{path}#week-{week.pk}"
    context["plan_url"] = f"{site_base_url().rstrip('/')}{path}"


_GRACE_MAIL_PURPOSES = frozenset({
    "payment_grace_failure_member",
    "payment_grace_failure_team",
    "payment_grace_reminder_member",
    "payment_grace_expired_member",
})


def _resolve_payment_grace_context(delivery, context):
    """Mint the grace mail's portal and Studio links from the Grace row.

    ``recovery_url`` reads the portal configuration at delivery time — the
    same read the synchronous send made, so an unsafe or missing portal
    still renders the template's reply-instead fallback. ``user_email``
    pins the member's address because the package defaults it to the
    delivery recipient, which is the team mailbox for the failure-team
    send, while the template documents the member.
    """

    from integrations.config import site_base_url  # noqa: PLC0415
    from payments.models import MonthlyPaymentGrace  # noqa: PLC0415
    from payments.services.monthly_payment_grace import (  # noqa: PLC0415
        safe_portal_url,
    )

    grace = None
    if delivery.related_object_type == "payments.monthlypaymentgrace":
        grace = MonthlyPaymentGrace.objects.filter(
            pk=delivery.related_object_id,
        ).first()
    if grace is None:
        raise PermanentJobError("payment_grace_record_missing")
    _member_greeting(delivery, context)
    context["user_email"] = grace.user.email
    context["recovery_url"] = safe_portal_url()
    base_url = site_base_url().rstrip("/")
    context["studio_member_url"] = f"{base_url}/studio/users/{grace.user_id}/"
    context["studio_report_url"] = (
        f"{base_url}/studio/payments/subscription-reconciliation/"
        "?filter=payment_grace"
    )


def _resolve_checkout_payment_failed_context(delivery, context):
    """Mint the checkout retry link from the Course relation."""

    from integrations.config import site_base_url  # noqa: PLC0415

    _member_greeting(delivery, context)
    if _is_course_related(delivery):
        from content.models import Course  # noqa: PLC0415

        course = Course.objects.filter(
            pk=delivery.related_object_id,
        ).first()
        if course is None:
            raise PermanentJobError("checkout_payment_failed_course_missing")
        retry_path = course.get_absolute_url()
    else:
        retry_path = "/membership"
    context["retry_url"] = f"{site_base_url().rstrip('/')}{retry_path}"


# A1.2 slice 3: the content download delivery and the Maven welcome.


def _resolve_download_delivery_context(delivery, context):
    """Mint the grant or verification link from the Download relation.

    The bearer secret of a download grant is unrecoverable from its stored
    hash, so the worker mints a fresh one-time grant at delivery time from
    the stored scalars (opt-in, surface) and the ``content.download``
    relation. The TTL clock therefore starts when the mail is produced,
    not when the request arrived, and no bearer token ever sits in the
    durable row (#1613). ``verification_required`` is re-read from the
    recipient at the same moment so the copy and the link shape can never
    disagree.
    """

    from urllib.parse import quote  # noqa: PLC0415

    from accounts.utils.tokens import generate_user_action_token  # noqa: PLC0415
    from content.models import Download  # noqa: PLC0415
    from content.services.download_delivery import (  # noqa: PLC0415
        create_delivery_grant,
        get_delivery_token_ttl_hours,
    )
    from integrations.config import site_base_url  # noqa: PLC0415

    download = None
    if delivery.related_object_type == "content.download":
        download = Download.objects.filter(
            pk=delivery.related_object_id,
        ).first()
    if download is None:
        raise PermanentJobError("download_delivery_download_missing")
    user = delivery.recipient_user
    if user is None or not getattr(user, "pk", None):
        raise PermanentJobError("download_delivery_user_missing")

    expires_hours = get_delivery_token_ttl_hours()
    grant_token = create_delivery_grant(
        user,
        download,
        newsletter_opt_in=bool(context.get("newsletter_opt_in")),
        surface=str(context.get("surface") or "detail"),
    )
    internal_path = (
        f'/api/downloads/{download.slug}/file?grant={quote(grant_token)}'
    )
    base_url = site_base_url().rstrip("/")
    if user.email_verified:
        context["delivery_url"] = f"{base_url}{internal_path}"
        context["verification_required"] = False
    else:
        verify_token = generate_user_action_token(
            user.pk,
            'verify_email',
            expiry_hours=expires_hours,
            return_path=internal_path,
        )
        context["delivery_url"] = (
            f"{base_url}/api/verify-email?token={verify_token}"
        )
        context["verification_required"] = True
    context["expires_hours"] = expires_hours


def _resolve_maven_welcome_context(delivery, context):
    """Mint every welcome link, token and config scalar at delivery time.

    The expiry clocks on the three tokens start here rather than at
    webhook intake, so a worker backlog or a retry loop never shortens
    the recipient's usable window (issue #1593: welcome links are not
    held to tight contracts). The greeting keeps the issue #1591 rule —
    ``greeting_name`` resolution, never the email handle — which the
    legacy synchronous renderer used to own.
    """

    from accounts.utils.tokens import (  # noqa: PLC0415
        generate_password_reset_token,
        generate_user_action_token,
    )
    from integrations.config import site_base_url  # noqa: PLC0415
    from integrations.maven_config import maven_course_slack_channel  # noqa: PLC0415
    from integrations.services.maven import (  # noqa: PLC0415
        NEWSLETTER_OPT_IN_TOKEN_EXPIRY_HOURS,
    )

    user = delivery.recipient_user
    if user is None or not getattr(user, "pk", None):
        raise PermanentJobError("maven_welcome_user_missing")

    # Issue #1682: the member-facing course name is the linked Course's
    # title, read from the delivery's course relation at delivery time so
    # the title is always fresh at send. Package Course rows label as
    # ``cb_curriculum.course``; in-flight rows queued before the cutover
    # still say ``content.course``. The producer persists no course
    # identifier — Maven's raw course/cohort labels are integration
    # identifiers, not display copy. A relation row missing here (course
    # deleted between queue and delivery) degrades to the stored (empty)
    # context and delivers: this relation is display copy, not a link
    # target, so the #1613 fail-closed rule for URL-minting relations does
    # not apply. Deliveries with no relation at all (legacy in-flight
    # rows) keep rendering their stored ``course_name`` scalar.
    if _is_course_related(delivery):
        from content.models import Course  # noqa: PLC0415

        course = Course.objects.filter(
            pk=delivery.related_object_id,
        ).first()
        if course is not None:
            context["course_name"] = course.title

    _member_greeting(delivery, context)
    base_url = site_base_url().rstrip("/")
    context["course_channel"] = maven_course_slack_channel()
    context["sign_in_url"] = f"{base_url}/accounts/login/"
    context["onboarding_url"] = f"{base_url}/onboarding/"
    context["slack_join_url"] = f"{base_url}/community/slack"
    reset_token = generate_password_reset_token(user, expiry_hours=24)
    context["password_reset_url"] = (
        f"{base_url}/api/password-reset?token={reset_token}"
    )
    opt_out_token = generate_user_action_token(user.pk, "maven_email_opt_out")
    context["opt_out_url"] = (
        f"{base_url}/api/maven-email-opt-out?token={opt_out_token}"
    )
    opt_in_token = generate_user_action_token(
        user.pk,
        "verify_and_subscribe",
        expiry_hours=NEWSLETTER_OPT_IN_TOKEN_EXPIRY_HOURS,
    )
    context["newsletter_opt_in_url"] = (
        f"{base_url}/api/verify-and-subscribe?token={opt_in_token}"
    )
# A1.2 slice 4: operator surfaces. The Studio test send marks its delivery
# with a category so the worker knows the delivery has no producer relation
# to mint links from.
STUDIO_TEST_SEND_CATEGORY = "studio_test_send"


def _resolve_studio_test_send_context(delivery, context):
    """Keep a Studio test send deliverable without a producer relation.

    The test send persists scalar placeholder copy only (#1613): the
    caller strips every URL-bearing preview value and stores the
    operator's own greeting scalar. Relation-dependent purposes must not
    dispatch their production resolvers here — with no relation attached
    they would fail closed and strand a permanently failing delivery for
    a send whose only job is to probe deliverability.
    """

    return context


def _resolve_lead_magnet_context(delivery, context):
    """Mint the lead magnet's verify and download links at delivery.

    A1.2 slice 4: the newsletter caller stores only the sanitized
    ``return_path`` (a resolver input, never a rendered link — issue
    #1613) and the worker re-mints the bearer token, exactly as the
    synchronous send did in the request. Both template links point at the
    same verification URL, which redirects to the download after the
    address is verified.
    """

    user = delivery.recipient_user
    if user is None or not getattr(user, "pk", None):
        raise PermanentJobError("lead_magnet_recipient_missing")

    from accounts.views.auth import _generate_verification_token  # noqa: PLC0415
    from integrations.config import site_base_url  # noqa: PLC0415

    _member_greeting(delivery, context)
    base_url = site_base_url().rstrip("/")
    token = _generate_verification_token(
        user.pk,
        return_path=context.get("return_path"),
    )
    url = f"{base_url}/api/verify-email?token={token}"
    context["verify_url"] = url
    context["download_url"] = url


def _resolve_welcome_imported_context(delivery, context):
    """Mint the imported member's reset and sign-in links.

    A1.2 slice 4: the import tags and course slugs stay scalar text and
    the two links are minted from the recipient row at delivery time —
    the one-hour password-reset token is never durable (#1613).
    """

    user = delivery.recipient_user
    if user is None or not getattr(user, "pk", None):
        raise PermanentJobError("welcome_imported_user_missing")

    from accounts.utils.tokens import generate_password_reset_token  # noqa: PLC0415
    from integrations.config import site_base_url  # noqa: PLC0415

    _member_greeting(delivery, context)
    base_url = site_base_url().rstrip("/")
    token = generate_password_reset_token(user, expiry_hours=1)
    context["password_reset_url"] = (
        f"{base_url}/api/password-reset?token={token}"
    )
    context["sign_in_url"] = f"{base_url}/login/"


def resolve_auth_mail_context(*, delivery, context):
    """Mint every rendered link in the worker, not in the stored context.

    Auth sends (#1610 slices 1-2), the email-change confirm and notice,
    the privacy deletion request, the recap, the post-event follow-up, the
    notification sends (slice 4: event reminder, workshop announcement,
    plan share), the staff heads-ups and the bookclub summaries (slice 1),
    the plans and payments member mail (slice 2: sprint-end recap,
    partner intro, cadence week notes, the four payment-grace templates
    and checkout failure), the content download delivery and Maven
    welcome (slice 3), and the operator surfaces (A1.2 remainder
    slice 4: the Studio test send, the newsletter subscribe verification
    and lead magnet delivery, the imported-member welcome) persist only
    non-secret inputs and relations; this resolver builds their URLs at
    delivery time so ``EmailDelivery.context_data`` never retains a
    clickable link (issue #1613, enforced by the site guard in
    ``email_app.services.context_guard``). Binding failures raise
    ``PermanentJobError`` — a stale relation must fail closed, not
    retry forever — and never name the token or URL. The resolver
    mutates only the in-memory copy the worker passes in; the stored
    context and its idempotency hash stay byte-for-byte unchanged.
    """

    from integrations.config import site_base_url  # noqa: PLC0415

    if delivery.category == STUDIO_TEST_SEND_CATEGORY:
        _resolve_studio_test_send_context(delivery, context)
    elif delivery.purpose == "account_email_change_confirm":
        _resolve_email_change_confirm(delivery, context)
    elif delivery.purpose == "account_email_changed_notice":
        _resolve_email_changed_notice(delivery, context)
    elif delivery.purpose == "account_deletion_request":
        _resolve_privacy_studio_url(delivery, context)
    elif delivery.purpose == "event_recap_ready":
        _resolve_recap_context(delivery, context)
    elif delivery.purpose == "post_event_followup":
        _resolve_followup_context(delivery, context)
    elif delivery.purpose == "event_reminder":
        _resolve_event_reminder_context(delivery, context)
    elif delivery.purpose == "workshop_announcement":
        _resolve_workshop_announcement_context(delivery, context)
    elif delivery.purpose == "plan_shared":
        _resolve_plan_shared_context(delivery, context)
    elif delivery.purpose in _STAFF_NOTIFICATION_PURPOSES:
        _resolve_staff_notification_context(delivery, context)
    elif delivery.purpose in ("bookclub_book_summary", "bookclub_chapter_summary"):
        _resolve_bookclub_summary_context(delivery, context)
    elif delivery.purpose == "sprint_end_recap":
        _resolve_sprint_end_recap_context(delivery, context)
    elif delivery.purpose == "sprint_partner_intro":
        _resolve_sprint_partner_intro_context(delivery, context)
    elif delivery.purpose in ("sprint_week_start", "sprint_week_note_prompt"):
        _resolve_sprint_cadence_context(delivery, context)
    elif delivery.purpose in _GRACE_MAIL_PURPOSES:
        _resolve_payment_grace_context(delivery, context)
    elif delivery.purpose == "checkout_payment_failed":
        _resolve_checkout_payment_failed_context(delivery, context)
    elif delivery.purpose == "download_delivery":
        _resolve_download_delivery_context(delivery, context)
    elif delivery.purpose == "maven_welcome":
        _resolve_maven_welcome_context(delivery, context)
    elif delivery.purpose == "lead_magnet_delivery":
        _resolve_lead_magnet_context(delivery, context)
    elif delivery.purpose == "welcome_imported":
        _resolve_welcome_imported_context(delivery, context)
    elif delivery.purpose in (
        "email_verification_signup",
        "email_verification_subscribe",
        "password_reset",
        "email_verification_signup_reminder",
        "email_verification_subscribe_reminder",
    ):
        _resolve_auth_links(delivery, context)

    # Render-only injection: templates render absolute links with it,
    # and it never enters the stored context.
    context.setdefault("site_url", site_base_url())
    return context


def template_override_loader(template_key):
    """``(subject, body_markdown, footer_note)`` from EmailTemplateOverride."""

    from email_app.models import EmailTemplateOverride  # noqa: PLC0415

    override = EmailTemplateOverride.objects.filter(
        template_name=template_key,
    ).first()
    if override is None:
        return None
    return override.subject, override.body_markdown, override.footer_note


def record_send(delivery, rendered, result):
    """Write the EmailLog audit row exactly as EmailService did.

    The package calls this from the worker after provider acceptance, so a
    recorded send now implies provider acceptance. Surrogate recipients
    (no saved user row, issue #703) still produce no Studio-visible row.

    A delivery carrying an ``events.event`` relation (the recap-ready send,
    A1.2 slice 3) keeps the event FK the recap flow attached to its
    ``EmailLog`` rows before the adoption, so the Studio audit surface and
    the event-scoped dedupe queries keep working.
    """

    from email_app.models import EmailLog  # noqa: PLC0415

    if delivery.recipient_user_id is None:
        return
    event_id = None
    if delivery.related_object_type == "events.event":
        event_id = int(delivery.related_object_id)
    EmailLog.objects.create(
        user_id=delivery.recipient_user_id,
        event_id=event_id,
        recipient_email=delivery.recipient_email,
        email_type=delivery.purpose,
        subject=rendered.subject,
        ses_message_id=result.message_id,
        dedupe_key=delivery.idempotency_key,
    )
