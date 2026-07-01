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
from django.core.mail import EmailMessage
from django.db import transaction
from django.db.models import Prefetch
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.utils.display import display_name
from content.access import LEVEL_TO_TIER_NAME, get_user_level
from crm.models import CRMRecord
from events.models import Event
from events.services.display_time import build_event_time_display
from integrations.config import get_config, is_enabled, site_base_url
from notifications.models import Notification
from plans.models import (
    Plan,
    PlanRequest,
    Sprint,
    SprintEnrollment,
    SprintFeedbackRequest,
)
from questionnaires.models import Response
from questionnaires.onboarding import get_onboarding_response
from questionnaires.services import (
    AnswerSaveError,
    build_response_form_rows,
    find_unanswered_required,
    save_response_answers,
)

logger = logging.getLogger(__name__)
User = get_user_model()

PLAN_REQUEST_RATE_LIMIT = datetime.timedelta(hours=24)


def _build_sprint_call_entries(events, user):
    """Return presentation data for sprint call rows."""
    entries = []
    next_upcoming_marked = False
    for event in events:
        is_upcoming = event.is_upcoming
        is_past = event.is_past
        is_next_upcoming = False
        if is_upcoming and not next_upcoming_marked:
            is_next_upcoming = True
            next_upcoming_marked = True
        entries.append({
            'event': event,
            'time_display': build_event_time_display(event, user),
            'can_join_now': is_upcoming and event.can_show_zoom_link(),
            'is_upcoming': is_upcoming,
            'is_past': is_past,
            'is_next_upcoming': is_next_upcoming,
            'location_label': _event_location_label(event),
            'detail_url': event.get_absolute_url(),
            'join_url': event.get_join_url(),  # Issue #1082: id-canonical
        })
    return entries


def _event_location_label(event):
    """Return compact platform/location copy without Zoom/Zoom duplication."""
    platform = event.get_platform_display()
    location = (event.location or '').strip()
    if not location or location.lower() == platform.lower():
        return platform
    return f'{platform} · {location}'


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
    sprint_call_entries = _build_sprint_call_entries(event_series_events, user)

    feedback_response = _viewer_feedback_response(sprint, user)

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
            'sprint_call_entries': sprint_call_entries,
            'feedback_response': feedback_response,
        },
    )


def _viewer_feedback_response(sprint, user):
    """Return the viewer's distributed feedback Response for ``sprint`` or None.

    A response is surfaced only when the questionnaire is attached to this
    sprint via a :class:`~plans.models.SprintFeedbackRequest` that has
    been distributed (``distributed_at`` set). Anonymous viewers always
    get None.
    """
    if user is None or not user.is_authenticated:
        return None
    questionnaire_ids = list(
        SprintFeedbackRequest.objects
        .filter(sprint=sprint, distributed_at__isnull=False)
        .values_list('questionnaire_id', flat=True)
    )
    if not questionnaire_ids:
        return None
    return (
        Response.objects
        .filter(respondent=user, questionnaire_id__in=questionnaire_ids)
        .order_by('-created_at')
        .first()
    )


def _get_member_feedback_response_or_404(request, sprint_slug, response_id):
    """Resolve ``(sprint, response)`` for a member feedback URL or raise 404.

    The response MUST belong to ``request.user`` AND to a feedback
    questionnaire attached to this sprint -- otherwise 404, so a member
    can never open another member's response (and staff browsing this
    member-facing route get a 404 too; they read responses via Studio).
    """
    sprint = _resolve_sprint_or_404(sprint_slug, request.user)
    questionnaire_ids = list(
        SprintFeedbackRequest.objects
        .filter(sprint=sprint)
        .values_list('questionnaire_id', flat=True)
    )
    response = get_object_or_404(
        Response.objects.select_related('questionnaire'),
        pk=response_id,
        respondent=request.user,
        questionnaire_id__in=questionnaire_ids,
    )
    return sprint, response


@login_required
def sprint_feedback_fill(request, sprint_slug, response_id):
    """Member fill-in / save page for their own sprint feedback response.

    GET renders the reusable questionnaire form fragment with existing
    answers pre-filled. POST upserts ``Answer`` rows (save draft) and
    re-renders. Submitted responses are read-only: editing after submit
    is intentionally out of scope (a member who needs a change asks
    staff), so a POST to a submitted response is rejected.
    """
    sprint, response = _get_member_feedback_response_or_404(
        request, sprint_slug, response_id,
    )

    if request.method == 'POST':
        if response.status == 'submitted':
            messages.info(
                request,
                'This feedback was already submitted and can no longer be '
                'edited. Contact the team if you need to change an answer.',
            )
            return redirect('sprint_feedback_fill',
                            sprint_slug=sprint.slug, response_id=response.pk)
        try:
            save_response_answers(response, request.POST)
        except AnswerSaveError as exc:
            form_rows = build_response_form_rows(
                response, post_data=request.POST, field_errors=exc.field_errors,
            )
            return render(request, 'plans/sprint_feedback_fill.html', {
                'sprint': sprint,
                'response': response,
                'form_rows': form_rows,
                'error': 'Please fix the highlighted answers.',
            }, status=400)
        messages.success(request, 'Draft saved. You can come back to finish.')
        return redirect('sprint_feedback_fill',
                        sprint_slug=sprint.slug, response_id=response.pk)

    if response.status == 'submitted':
        # Read-only view of the submitted answers.
        rows = []
        answers_by_question = {
            a.question_id: a
            for a in response.answers.prefetch_related('selected_options').all()
        }
        for rq in response.response_questions.all():
            answer = answers_by_question.get(rq.pk)
            rows.append({
                'question': rq,
                'answer': answer,
                'is_answered': answer is not None and answer.display_value != '',
            })
        return render(request, 'plans/sprint_feedback_submitted.html', {
            'sprint': sprint,
            'response': response,
            'rows': rows,
        })

    form_rows = build_response_form_rows(response)
    return render(request, 'plans/sprint_feedback_fill.html', {
        'sprint': sprint,
        'response': response,
        'form_rows': form_rows,
        'error': '',
    })


@login_required
@require_POST
def sprint_feedback_submit(request, sprint_slug, response_id):
    """Validate required answers, mark the response submitted, redirect back."""
    sprint, response = _get_member_feedback_response_or_404(
        request, sprint_slug, response_id,
    )

    if response.status == 'submitted':
        messages.info(request, 'This feedback was already submitted.')
        return redirect('sprint_detail', sprint_slug=sprint.slug)

    # Persist whatever was typed before validating completeness.
    try:
        save_response_answers(response, request.POST)
    except AnswerSaveError as exc:
        form_rows = build_response_form_rows(
            response, post_data=request.POST, field_errors=exc.field_errors,
        )
        return render(request, 'plans/sprint_feedback_fill.html', {
            'sprint': sprint,
            'response': response,
            'form_rows': form_rows,
            'error': 'Please fix the highlighted answers.',
        }, status=400)

    missing = find_unanswered_required(response)
    if missing:
        prompts = ', '.join(rq.prompt for rq in missing)
        form_rows = build_response_form_rows(response)
        return render(request, 'plans/sprint_feedback_fill.html', {
            'sprint': sprint,
            'response': response,
            'form_rows': form_rows,
            'error': f'Please answer the required question(s): {prompts}',
        }, status=400)

    response.mark_submitted()
    messages.success(
        request,
        'Thank you! Your feedback helps shape the next sprint.',
    )
    return redirect('sprint_detail', sprint_slug=sprint.slug)


@login_required
@require_POST
def sprint_join(request, sprint_slug):
    """Self-join a sprint. Tier-gated and idempotent."""
    sprint = _resolve_sprint_or_404(sprint_slug, request.user)

    # A cancelled sprint is closed to new self-join (issue #949). Existing
    # enrollments/plans are untouched; only the join path is blocked.
    if sprint.status == 'cancelled':
        messages.error(request, 'This sprint has been cancelled.')
        return redirect('sprint_detail', sprint_slug=sprint.slug)

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
    return display_name(user)


def _member_staff_target_path(member):
    """Studio CRM detail path for ``member`` when tracked, else user detail."""
    record = CRMRecord.objects.filter(user=member).first()
    if record is not None:
        return reverse('studio_crm_detail', kwargs={'crm_id': record.pk})
    return reverse('studio_user_detail', kwargs={'user_id': member.pk})


def _member_staff_target_url(member):
    return f'{site_base_url()}{_member_staff_target_path(member)}'


def _studio_plan_request_prepare_path(*, member, sprint):
    return reverse(
        'studio_sprint_plan_request_prepare',
        kwargs={'sprint_id': sprint.pk, 'member_id': member.pk},
    )


def _studio_plan_request_prepare_url(*, member, sprint):
    return f'{site_base_url()}{_studio_plan_request_prepare_path(member=member, sprint=sprint)}'


def _onboarding_request_state(member):
    response = get_onboarding_response(member)
    if response is None:
        return {
            'key': 'not_started',
            'label': 'Not started',
            'is_submitted': False,
            'is_incomplete': True,
        }
    if response.status == 'submitted':
        return {
            'key': 'submitted',
            'label': 'Submitted',
            'is_submitted': True,
            'is_incomplete': False,
        }
    return {
        'key': 'draft',
        'label': 'Started but not submitted',
        'is_submitted': False,
        'is_incomplete': True,
    }


def _post_plan_request_to_slack(
    *, member, sprint, prepare_url, staff_target_url, onboarding_state,
):
    """Post a Block Kit plan-request message to the team-requests channel.

    Returns True if the request was posted (best effort -- network
    failures log a warning and return False). Returns False without
    trying when Slack is disabled or no team-requests channel is configured.
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
    staff_target_label = (
        'Open CRM' if CRMRecord.objects.filter(user=member).exists()
        else 'Open Studio user'
    )
    blocks = [
        {
            'type': 'section',
            'text': {
                'type': 'mrkdwn',
                'text': (
                    f'*Plan request* from <{staff_target_url}|{member_name}>'
                    f' (`{member.email}`) in *{sprint.name}*.\n'
                    f'Onboarding: *{onboarding_state["label"]}*.'
                ),
            },
        },
        {
            'type': 'actions',
            'elements': [
                {
                    'type': 'button',
                    'text': {
                        'type': 'plain_text',
                        'text': 'Prepare request',
                    },
                    'url': prepare_url,
                    'action_id': 'prepare_plan_request',
                },
                {
                    'type': 'button',
                    'text': {
                        'type': 'plain_text',
                        'text': staff_target_label,
                    },
                    'url': staff_target_url,
                    'action_id': 'open_member_context',
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


def _email_shared_plan_request_confirmation(
    *, member, sprint, board_url, onboarding_url, onboarding_state,
):
    """Send the member-safe shared confirmation email for a fresh request."""
    team_email = (get_config('STAFF_SIGNUP_NOTIFY_EMAIL', '') or '').strip()
    if team_email:
        to = [team_email]
        cc = [member.email]
    else:
        to = [member.email]
        cc = []
    subject = f'Plan request: {member.email} - {sprint.name}'
    member_name = _member_display_name(member)
    body = (
        f'{member_name} ({member.email}) asked the AI Shipping Labs team '
        f'to prepare a plan for {sprint.name}.\n\n'
        f'Cohort board: {board_url}\n'
    )
    if onboarding_state['is_incomplete']:
        body += (
            '\nBefore the team can prepare the plan, please complete '
            f'your onboarding: {onboarding_url}\n'
        )
    else:
        body += '\nThe team has the request and will prepare the plan.\n'

    from_email = get_config('SES_FROM_EMAIL', 'community@aishippinglabs.com')
    email = EmailMessage(
        subject=subject,
        body=body,
        from_email=from_email,
        to=to,
        cc=cc,
    )
    email.send(fail_silently=True)
    return len(to) + len(cc)


def _create_staff_plan_request_notifications(*, member, sprint):
    """Create one ``plan_request`` Notification per active staff user.

    The notification's ``url`` points at the locked request-preparation
    flow so staff review onboarding/CRM context before creating a plan.
    """
    member_name = _member_display_name(member)
    title = f'Plan request from {member_name}'
    body = f'In sprint {sprint.name}'
    studio_url = _studio_plan_request_prepare_path(member=member, sprint=sprint)
    notifications = [
        Notification(
            user=staff,
            title=title,
            body=body,
            url=studio_url,
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
    creates one ``PlanRequest``, sends the shared member-safe email,
    posts staff-only Slack when configured, and creates one in-app
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
    onboarding_url = f'{site_base_url()}{reverse("onboarding_start")}'
    onboarding_state = _onboarding_request_state(request.user)
    prepare_url = _studio_plan_request_prepare_url(
        member=request.user, sprint=sprint,
    )
    staff_target_url = _member_staff_target_url(request.user)

    _email_shared_plan_request_confirmation(
        member=request.user, sprint=sprint,
        board_url=board_url, onboarding_url=onboarding_url,
        onboarding_state=onboarding_state,
    )
    _post_plan_request_to_slack(
        member=request.user, sprint=sprint,
        prepare_url=prepare_url, staff_target_url=staff_target_url,
        onboarding_state=onboarding_state,
    )

    _create_staff_plan_request_notifications(
        member=request.user, sprint=sprint,
    )

    if onboarding_state['is_incomplete']:
        messages.success(
            request,
            'Asked the team to plan with you. Please complete onboarding '
            'before the team can prepare your plan.',
        )
    else:
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
