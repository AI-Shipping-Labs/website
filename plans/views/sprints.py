"""Member-facing sprint detail + self-join / leave views (issue #443).

Four URLs live here:

- ``GET /sprints/<slug>``: public detail page with one of four CTAs
  (login-to-join, upgrade, join, leave) decided by viewer state.
- ``POST /sprints/<slug>/join``: tier-gated, idempotent self-enrollment.
- ``POST /sprints/<slug>/leave``: idempotent unenrollment that
  auto-privates an existing plan but leaves the plan row intact.
- ``POST /sprints/<slug>/ask-team``: enrolled-but-no-plan members ask
  the team to plan with them; rate-limited and audited (issue #585).

Studio bulk-enroll lives in ``studio/views/sprints_enroll.py``;
the JSON API mirror lives in ``api/views/enrollments.py``.
"""

import datetime
import logging

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Prefetch
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from content.access import LEVEL_TO_TIER_NAME, get_user_level
from events.models import Event
from integrations.config import get_config, is_enabled, site_base_url
from notifications.models import Notification
from plans.models import Plan, PlanRequest, Sprint, SprintEnrollment

logger = logging.getLogger(__name__)
User = get_user_model()

PLAN_REQUEST_RATE_LIMIT = datetime.timedelta(hours=24)


def _viewer_plan(sprint, user):
    """Return the viewer's plan in this sprint or None.

    Anonymous viewers always get None (anonymous users never own a plan).
    """
    if user is None or not user.is_authenticated:
        return None
    return Plan.objects.filter(sprint=sprint, member=user).first()


def _is_enrolled(sprint, user):
    """Whether the user has a SprintEnrollment row for this sprint."""
    if user is None or not user.is_authenticated:
        return False
    return SprintEnrollment.objects.filter(sprint=sprint, user=user).exists()


def _resolve_sprint_or_404(slug, user, *, with_event_series=False):
    """Look up a sprint by slug, hiding draft sprints from non-staff.

    Mirrors the events surface: a sprint with status=draft is invisible
    to anonymous and non-staff users. Staff can preview the page.

    When ``with_event_series=True`` the lookup pulls the linked
    ``EventSeries`` (one extra row) and prefetches its events ordered by
    start datetime so the public sprint detail page can render the
    "Meeting schedule" section without N+1 queries (issue #565).
    """
    qs = Sprint.objects.all()
    if with_event_series:
        qs = qs.select_related('event_series').prefetch_related(
            Prefetch(
                'event_series__events',
                queryset=Event.objects.order_by('start_datetime'),
                to_attr='ordered_events',
            ),
        )
    sprint = get_object_or_404(qs, slug=slug)
    if sprint.status == 'draft':
        if not user.is_authenticated or not user.is_staff:
            raise Http404('Sprint not found')
    return sprint


def sprint_detail(request, sprint_slug):
    """Public detail page for a sprint with a tier-aware Join CTA."""
    sprint = _resolve_sprint_or_404(
        sprint_slug, request.user, with_event_series=True,
    )

    user = request.user
    is_authenticated = user.is_authenticated
    user_level = get_user_level(user) if is_authenticated else 0
    eligible = user_level >= sprint.min_tier_level
    enrolled = _is_enrolled(sprint, user)
    viewer_plan = _viewer_plan(sprint, user)
    required_tier_name = LEVEL_TO_TIER_NAME.get(sprint.min_tier_level, 'Premium')

    event_series = sprint.event_series
    event_series_events = (
        getattr(event_series, 'ordered_events', []) if event_series else []
    )

    return render(
        request,
        'plans/sprint_detail.html',
        {
            'sprint': sprint,
            'is_authenticated': is_authenticated,
            'enrolled': enrolled,
            'eligible': eligible,
            'viewer_plan': viewer_plan,
            'required_tier_name': required_tier_name,
            'event_series': event_series,
            'event_series_events': event_series_events,
        },
    )


@login_required
@require_POST
def sprint_join(request, sprint_slug):
    """Self-join a sprint. Tier-gated and idempotent."""
    sprint = _resolve_sprint_or_404(sprint_slug, request.user)

    user_level = get_user_level(request.user)
    required_tier_name = LEVEL_TO_TIER_NAME.get(sprint.min_tier_level, 'Premium')
    if user_level < sprint.min_tier_level:
        messages.error(
            request,
            f'Upgrade to {required_tier_name} to join this sprint.',
        )
        return redirect('/pricing')

    _enrollment, created = SprintEnrollment.objects.get_or_create(
        sprint=sprint,
        user=request.user,
        defaults={'enrolled_by': None},
    )
    if created:
        messages.success(request, f'Welcome to {sprint.name}.')
    else:
        messages.info(request, "You're already enrolled in this sprint.")

    plan = _viewer_plan(sprint, request.user)
    if plan is not None:
        return redirect(
            'my_plan_detail',
            sprint_slug=sprint.slug,
            plan_id=plan.pk,
        )
    return redirect('cohort_board', sprint_slug=sprint.slug)


def _staff_users():
    """Return the queryset of active staff users for plan-request fanout."""
    return User.objects.filter(is_active=True, is_staff=True)


def _member_display_name(user):
    """Best-effort display name for a member in plan-request messages."""
    full = (user.get_full_name() or '').strip()
    if full:
        return full
    return user.email


def _member_admin_url(user):
    """Absolute URL of the Django admin user-change page."""
    user_admin_path = reverse(
        'admin:accounts_user_change', args=[user.pk],
    ) if _has_user_admin() else f'/admin/accounts/user/{user.pk}/change/'
    return f'{site_base_url()}{user_admin_path}'


def _has_user_admin():
    """Whether the accounts.User model is registered with the admin.

    Falls back to a hand-built URL if reverse fails. Wrapping in a tiny
    helper keeps the call site readable and the assumption testable.
    """
    try:
        reverse('admin:accounts_user_change', args=[1])
        return True
    except Exception:
        return False


def _post_plan_request_to_slack(*, member, sprint, board_url, admin_url):
    """Post a Block Kit plan-request message to the team-requests channel.

    Returns True if the request was posted (best effort -- network
    failures log a warning and return False so the caller knows to fall
    back to email). Returns False without trying when Slack is disabled
    or no team-requests channel is configured.
    """
    # Inline import keeps slack_config / requests off the module-level
    # import path so test runs that mock these don't have to patch the
    # views file's globals. (See feedback_inline_imports.md -- documented
    # reason: avoid cross-cutting test setup.)
    from community.slack_config import get_slack_team_requests_channel_id

    if not is_enabled('SLACK_ENABLED'):
        return False
    channel_id = get_slack_team_requests_channel_id()
    if not channel_id:
        return False
    bot_token = get_config('SLACK_BOT_TOKEN')
    if not bot_token:
        return False

    import requests  # noqa: PLC0415 -- network dep, kept off module top.

    member_name = _member_display_name(member)
    text_fallback = f'Plan request: {member.email} for {sprint.name}'
    blocks = [
        {
            'type': 'section',
            'text': {
                'type': 'mrkdwn',
                'text': (
                    f'*Plan request* from <{admin_url}|{member_name}>'
                    f' (`{member.email}`) in *{sprint.name}*.'
                ),
            },
        },
        {
            'type': 'section',
            'text': {
                'type': 'mrkdwn',
                'text': f'<{board_url}|Open the cohort board>',
            },
        },
        {
            'type': 'actions',
            'elements': [
                {
                    'type': 'button',
                    'text': {
                        'type': 'plain_text',
                        'text': 'Open user in admin',
                    },
                    'url': admin_url,
                    'action_id': 'open_member_admin',
                },
            ],
        },
    ]

    try:
        response = requests.post(
            'https://slack.com/api/chat.postMessage',
            json={
                'channel': channel_id,
                'text': text_fallback,
                'blocks': blocks,
            },
            headers={
                'Authorization': f'Bearer {bot_token}',
                'Content-Type': 'application/json; charset=utf-8',
            },
            timeout=10,
        )
        data = response.json()
        if not data.get('ok'):
            logger.warning(
                'Plan-request Slack post failed: %s', data.get('error'),
            )
            return False
        return True
    except Exception:
        logger.exception('Failed to post plan request to Slack')
        return False


def _email_staff_about_plan_request(*, member, sprint, board_url, admin_url):
    """Email every active staff user about a fresh plan request."""
    recipients = list(
        _staff_users().values_list('email', flat=True),
    )
    if not recipients:
        return 0
    subject = f'Plan request: {member.email} - {sprint.name}'
    body = (
        f'{_member_display_name(member)} ({member.email}) is enrolled in '
        f'sprint "{sprint.name}" but does not have a plan yet, and just '
        f'asked the team to prepare one.\n\n'
        f'Cohort board: {board_url}\n'
        f'Open user in admin: {admin_url}\n'
    )
    from_email = get_config('SES_FROM_EMAIL', 'community@aishippinglabs.com')
    send_mail(
        subject=subject,
        message=body,
        from_email=from_email,
        recipient_list=recipients,
        fail_silently=True,
    )
    return len(recipients)


def _create_staff_plan_request_notifications(*, member, sprint, admin_url):
    """Create one ``plan_request`` Notification per active staff user."""
    member_name = _member_display_name(member)
    title = f'Plan request from {member_name}'
    body = f'In sprint {sprint.name}'
    notifications = [
        Notification(
            user=staff,
            title=title,
            body=body,
            url=admin_url,
            notification_type='plan_request',
        )
        for staff in _staff_users()
    ]
    if notifications:
        Notification.objects.bulk_create(notifications)
    return len(notifications)


@login_required
@require_POST
def sprint_ask_team(request, sprint_slug):
    """Ask the team to prepare a plan for the viewer (issue #585).

    Caller MUST be enrolled in the sprint and MUST NOT already own a
    plan in the sprint -- otherwise 404 (anything richer would leak
    membership state to outsiders, mirroring ``cohort_board``).

    Rate-limited to one ping per ``(sprint, member)`` per 24 hours via
    ``PlanRequest`` rows. The first successful ping inside the window
    creates one ``PlanRequest``, fans out a Slack post (when configured)
    or a staff email (fallback), and ALWAYS creates one in-app
    ``Notification`` per active staff user. Re-pings inside the window
    are no-ops with an info-level success message redirected back to
    the cohort board.
    """
    sprint = _resolve_sprint_or_404(sprint_slug, request.user)
    if not _is_enrolled(sprint, request.user):
        raise Http404('Not enrolled in this sprint')
    if _viewer_plan(sprint, request.user) is not None:
        raise Http404('Plan already exists')

    board_path = reverse(
        'cohort_board', kwargs={'sprint_slug': sprint.slug},
    )

    cutoff = timezone.now() - PLAN_REQUEST_RATE_LIMIT
    recent_exists = PlanRequest.objects.filter(
        sprint=sprint, member=request.user, created_at__gte=cutoff,
    ).exists()
    if recent_exists:
        messages.info(
            request,
            "You already pinged the team in the last 24 hours. "
            "Hang tight — we'll get to your plan soon.",
        )
        return redirect(board_path)

    with transaction.atomic():
        PlanRequest.objects.create(sprint=sprint, member=request.user)

    board_url = f'{site_base_url()}{board_path}'
    admin_url = _member_admin_url(request.user)

    posted_to_slack = _post_plan_request_to_slack(
        member=request.user, sprint=sprint,
        board_url=board_url, admin_url=admin_url,
    )
    if not posted_to_slack:
        _email_staff_about_plan_request(
            member=request.user, sprint=sprint,
            board_url=board_url, admin_url=admin_url,
        )

    _create_staff_plan_request_notifications(
        member=request.user, sprint=sprint, admin_url=admin_url,
    )

    messages.success(
        request,
        "Asked the team to plan with you. We'll be in touch.",
    )
    return redirect(board_path)


@login_required
@require_POST
def sprint_leave(request, sprint_slug):
    """Leave a sprint. Auto-privates the user's plan if one exists.

    Idempotent: re-leaving emits an info message and returns the same
    redirect; never 404 / 409.
    """
    sprint = _resolve_sprint_or_404(sprint_slug, request.user)

    with transaction.atomic():
        deleted_count, _ = SprintEnrollment.objects.filter(
            sprint=sprint, user=request.user,
        ).delete()

        if deleted_count:
            plan = _viewer_plan(sprint, request.user)
            if plan is not None and plan.visibility != 'private':
                plan.visibility = 'private'
                plan.save(update_fields=['visibility', 'updated_at'])
            messages.success(request, f'You left {sprint.name}.')
        else:
            messages.info(request, "You weren't enrolled.")

    return redirect('sprint_detail', sprint_slug=sprint.slug)
