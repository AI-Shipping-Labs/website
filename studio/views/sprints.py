"""Studio views for managing sprints (issue #432).

A sprint is a rolling cohort window. Plans (one per member per sprint)
hang off a sprint. Weeks per plan are bounded by ``sprint.duration_weeks``
in practice but not enforced at the DB layer.

All views are staff-only. Anonymous users are redirected to the login
page; authenticated non-staff users get a 403. See
``studio/decorators.py``.

Issue #444 adds ``sprint_add_member`` -- a one-click enrollment +
plan-creation flow off the sprint detail page that reuses the
existing plan create form (``templates/studio/plans/form.html``)
with the sprint locked from the URL.
"""

import logging
from datetime import datetime
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db.models import Count, Max
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from content.access import LEVEL_MAIN
from content.access import VISIBILITY_CHOICES as TIER_LEVEL_CHOICES
from crm.models import CRMRecord
from crm.services.member_profile import build_member_profile_context
from events.models import EventSeries
from integrations.config import get_config
from integrations.services import llm
from integrations.services.feedback_synthesis import (
    LLMError,
    SprintFeedbackInput,
    synthesize_feedback,
)
from plans.models import (
    PLAN_READY_EMAIL_STATUS_FAILED,
    PLAN_READY_EMAIL_STATUS_SENDING,
    PLAN_READY_EMAIL_STATUS_SENT,
    SPRINT_STATUS_CHOICES,
    Plan,
    PlanReadyEmailLog,
    PlanRequest,
    Sprint,
    SprintEnrollment,
    SprintFeedbackRequest,
    SprintFeedbackSummary,
)
from plans.services import (
    create_plan_for_enrollment,
    distribute_sprint_feedback,
    preview_plan_ready_emails,
    send_plan_ready_email_for_plan,
    send_plan_ready_emails,
)
from questionnaires.models import Questionnaire, Response
from questionnaires.onboarding import get_onboarding_response
from studio.decorators import staff_required

User = get_user_model()

logger = logging.getLogger(__name__)

# The set of tier levels accepted by the form. Mirror the values in
# ``content.access.VISIBILITY_CHOICES`` so the dropdown stays consistent
# with the rest of the gating surface.
_VALID_TIER_LEVELS = {value for value, _label in TIER_LEVEL_CHOICES}


def _parse_min_tier_level(raw):
    """Parse the ``min_tier_level`` form field. ``(value, error)``."""
    if raw in (None, ''):
        return LEVEL_MAIN, ''
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, 'Min tier level must be a whole number.'
    if value not in _VALID_TIER_LEVELS:
        return None, 'Min tier level must be one of 0, 10, 20, 30.'
    return value, ''


def _parse_event_series(raw):
    """Parse the ``event_series`` value. ``(EventSeries|None, error)``.

    Empty string / missing / ``None`` -> ``(None, '')`` (sprint becomes
    unlinked). Resolution accepts either form:

    - an integer or numeric string -> resolve by ``EventSeries.pk``
    - a non-numeric string -> resolve by ``EventSeries.slug``

    An unknown id/slug -> ``(None, error_message)`` so the caller
    re-renders the Studio form with HTTP 400 (or, in the API, returns a
    422 ``unknown_series``) and the sprint is NOT written. The Studio
    form only ever submits ids; the slug branch is used by the sprint
    API, which shares this helper.
    """
    if raw in (None, ''):
        return None, ''
    if isinstance(raw, bool):
        return None, 'Selected event series does not exist.'
    # Numeric (int or numeric string) -> resolve by pk; otherwise by slug.
    if isinstance(raw, int):
        series = EventSeries.objects.filter(pk=raw).first()
    elif isinstance(raw, str) and raw.lstrip('-').isdigit():
        series = EventSeries.objects.filter(pk=int(raw)).first()
    elif isinstance(raw, str):
        series = EventSeries.objects.filter(slug=raw).first()
    else:
        return None, 'Selected event series does not exist.'
    if series is None:
        return None, 'Selected event series does not exist.'
    return series, ''


def _parse_duration_weeks(raw):
    """Parse the ``duration_weeks`` form field into a validated int.

    Returns ``(value, error_message)``. ``error_message`` is empty on
    success. Rejects non-integers and values outside ``[1, 26]``.
    """
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, 'Duration (weeks) must be a whole number.'
    try:
        MinValueValidator(1)(value)
        MaxValueValidator(26)(value)
    except ValidationError:
        return None, 'Duration (weeks) must be between 1 and 26.'
    return value, ''


def _parse_start_date(raw):
    """Parse the ``start_date`` field. Returns ``(date, error_message)``."""
    if not raw:
        return None, 'Start date is required.'
    try:
        return datetime.strptime(raw, '%Y-%m-%d').date(), ''
    except ValueError:
        return None, 'Start date must be in YYYY-MM-DD format.'


def _normalize_status(raw):
    """Coerce the raw status value to a valid choice; default to draft."""
    valid = {choice[0] for choice in SPRINT_STATUS_CHOICES}
    if raw in valid:
        return raw
    return 'draft'


def _render_form(request, *, sprint, form_action, form_data, error='', status=200):
    context = {
        'sprint': sprint,
        'form_action': form_action,
        'form_data': form_data,
        'status_choices': SPRINT_STATUS_CHOICES,
        'tier_level_choices': TIER_LEVEL_CHOICES,
        'event_series_list': EventSeries.objects.all().order_by('name'),
        'error': error,
        'primary_label': 'Save changes' if form_action == 'edit' else 'Create sprint',
    }
    return render(request, 'studio/sprints/form.html', context, status=status)


def _form_data_from_post(request):
    return {
        'name': (request.POST.get('name') or '').strip(),
        'slug': (request.POST.get('slug') or '').strip(),
        'start_date': (request.POST.get('start_date') or '').strip(),
        'duration_weeks': (request.POST.get('duration_weeks') or '').strip(),
        'status': (request.POST.get('status') or '').strip(),
        'min_tier_level': (request.POST.get('min_tier_level') or '').strip(),
        'event_series': (request.POST.get('event_series') or '').strip(),
    }


def _form_data_from_sprint(sprint):
    return {
        'name': sprint.name,
        'slug': sprint.slug,
        'start_date': sprint.start_date.isoformat() if sprint.start_date else '',
        'duration_weeks': str(sprint.duration_weeks),
        'status': sprint.status,
        'min_tier_level': str(sprint.min_tier_level),
        'event_series': (
            str(sprint.event_series_id) if sprint.event_series_id else ''
        ),
    }


def _pending_request_member_ids(sprint):
    """Return distinct member ids with outstanding plan requests in ``sprint``.

    A request is "outstanding" iff the member has at least one
    :class:`PlanRequest` row for the sprint AND no :class:`Plan` row
    yet. The set is computed as a SQL left-anti-join in two steps:

    1. distinct member ids that requested in this sprint, then
    2. exclude any whose pk has a Plan row in the sprint.
    """
    # ``order_by()`` with no args clears the model's default ordering
    # by ``-created_at`` so ``DISTINCT`` operates on ``member_id``
    # alone -- otherwise PostgreSQL would distinct on the (member_id,
    # created_at) tuple and return one row per ping rather than one
    # row per pinger. See Django docs: "Note that ordering fields are
    # part of the SQL query, and can therefore affect the results."
    requested_member_ids = set(
        PlanRequest.objects
        .filter(sprint=sprint)
        .order_by()
        .values_list('member_id', flat=True)
        .distinct()
    )
    members_with_plans = set(
        Plan.objects
        .filter(sprint=sprint, member_id__in=requested_member_ids)
        .values_list('member_id', flat=True)
    )
    return sorted(requested_member_ids - members_with_plans)


def _onboarding_state_for_member(member):
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


def _member_context_target(member):
    record = CRMRecord.objects.filter(user=member).first()
    if record is not None:
        return {
            'label': 'Open CRM',
            'url': reverse('studio_crm_detail', kwargs={'crm_id': record.pk}),
            'has_crm_record': True,
        }
    return {
        'label': 'Open Studio user',
        'url': reverse('studio_user_detail', kwargs={'user_id': member.pk}),
        'has_crm_record': False,
    }


def _plan_request_prepare_url(*, sprint, member):
    return reverse(
        'studio_sprint_plan_request_prepare',
        kwargs={'sprint_id': sprint.pk, 'member_id': member.pk},
    )


@staff_required
def sprint_list(request):
    """Table of sprints with status badge, start date, duration, plan count.

    Also surfaces a "Pending requests" column per sprint -- the count
    of distinct members with at least one outstanding ``PlanRequest``
    and no ``Plan`` in the sprint. Clicking the count deep-links to
    the sprint detail page's ``#pending-requests`` anchor (issue #718).
    """
    sprints = list(
        Sprint.objects
        .annotate(plan_count=Count('plans'))
        .order_by('-start_date')
    )
    # Compute pending counts in Python because the left-anti-join is
    # awkward to express as a single annotation across both PlanRequest
    # and Plan with distinct counts. N is bounded by the number of
    # sprints (small) so the extra round trips are cheap.
    for sprint in sprints:
        sprint.pending_request_count = len(_pending_request_member_ids(sprint))
    return render(request, 'studio/sprints/list.html', {
        'sprints': sprints,
    })


@staff_required
def sprint_create(request):
    """Form to create a sprint."""
    if request.method != 'POST':
        return _render_form(
            request,
            sprint=None,
            form_action='create',
            form_data={
                'name': '',
                'slug': '',
                'start_date': '',
                'duration_weeks': '6',
                'status': 'draft',
                'min_tier_level': str(LEVEL_MAIN),
                'event_series': '',
            },
        )

    form_data = _form_data_from_post(request)

    name = form_data['name']
    raw_slug = form_data['slug']
    start_date, date_error = _parse_start_date(form_data['start_date'])
    duration, duration_error = _parse_duration_weeks(form_data['duration_weeks'])
    status_value = _normalize_status(form_data['status'])
    min_tier_level, tier_error = _parse_min_tier_level(form_data['min_tier_level'])
    event_series, event_series_error = _parse_event_series(form_data['event_series'])

    if not name:
        return _render_form(
            request, sprint=None, form_action='create',
            form_data=form_data, error='Name is required.', status=400,
        )

    slug = raw_slug or slugify(name)
    if not slug:
        return _render_form(
            request, sprint=None, form_action='create',
            form_data=form_data, error='Slug could not be derived from name.', status=400,
        )

    if date_error:
        return _render_form(
            request, sprint=None, form_action='create',
            form_data=form_data, error=date_error, status=400,
        )
    if duration_error:
        return _render_form(
            request, sprint=None, form_action='create',
            form_data=form_data, error=duration_error, status=400,
        )
    if tier_error:
        return _render_form(
            request, sprint=None, form_action='create',
            form_data=form_data, error=tier_error, status=400,
        )
    if event_series_error:
        return _render_form(
            request, sprint=None, form_action='create',
            form_data=form_data, error=event_series_error, status=400,
        )

    if Sprint.objects.filter(slug=slug).exists():
        return _render_form(
            request, sprint=None, form_action='create',
            form_data=form_data,
            error=f'A sprint with slug "{slug}" already exists. Pick a different slug.',
            status=400,
        )

    sprint = Sprint.objects.create(
        name=name,
        slug=slug,
        start_date=start_date,
        duration_weeks=duration,
        status=status_value,
        min_tier_level=min_tier_level,
        event_series=event_series,
    )
    messages.success(request, f'Sprint "{sprint.name}" created.')
    return redirect('studio_sprint_detail', sprint_id=sprint.pk)


def _build_pending_plan_requests(sprint):
    """Return one row per member with outstanding plan requests.

    Each row dict is shaped for the template: ``{member, request_count,
    last_requested_at}``. Members who already have a :class:`Plan` in
    the sprint are excluded (a left-anti-join against ``Plan``).

    Ordering: most-recent-request-first. The PM did not specify but the
    operator dictation framed this as a working inbox, so newest-on-top
    is the conventional choice — it surfaces the freshest pings first
    and keeps the panel useful when an operator is processing a queue.
    """
    pending_member_ids = _pending_request_member_ids(sprint)
    if not pending_member_ids:
        return []

    # Aggregate request count and latest timestamp per member.
    aggregates = (
        PlanRequest.objects
        .filter(sprint=sprint, member_id__in=pending_member_ids)
        .values('member_id')
        .annotate(
            request_count=Count('id'),
            last_requested_at=Max('created_at'),
        )
    )
    members = {
        u.pk: u for u in User.objects.filter(pk__in=pending_member_ids)
    }
    rows = []
    for agg in aggregates:
        member = members.get(agg['member_id'])
        if member is None:
            continue
        onboarding_state = _onboarding_state_for_member(member)
        rows.append({
            'member': member,
            'request_count': agg['request_count'],
            'last_requested_at': agg['last_requested_at'],
            'onboarding_state': onboarding_state,
            'prepare_url': _plan_request_prepare_url(
                sprint=sprint, member=member,
            ),
        })
    # Most-recent-request-first.
    rows.sort(key=lambda r: r['last_requested_at'], reverse=True)
    return rows


def _build_sprint_member_rows(*, sprint, enrollments, plans):
    """Merge enrollment and plan rows for the Studio sprint detail page.

    Ordering is intentionally stable for operators: enrolled members stay
    first in enrollment order, then plan-only rows follow by plan creation
    date. A plan-only row can happen when staff unenroll a member because
    unenrollment keeps the plan.
    """
    rows_by_user_id = {}

    for index, enrollment in enumerate(enrollments):
        rows_by_user_id[enrollment.user_id] = {
            'member': enrollment.user,
            'enrollment': enrollment,
            'plan': None,
            'create_plan_url': (
                reverse('studio_plan_create')
                + '?'
                + urlencode({
                    'user': enrollment.user_id,
                    'sprint': sprint.pk,
                })
            ),
            'sort_group': 0,
            'sort_index': index,
        }

    for index, plan in enumerate(plans):
        row = rows_by_user_id.get(plan.member_id)
        if row is None:
            rows_by_user_id[plan.member_id] = {
                'member': plan.member,
                'enrollment': None,
                'plan': plan,
                'create_plan_url': '',
                'sort_group': 1,
                'sort_index': index,
            }
        else:
            row['plan'] = plan

    return sorted(
        rows_by_user_id.values(),
        key=lambda row: (row['sort_group'], row['sort_index'], row['member'].pk),
    )


@staff_required
def sprint_detail(request, sprint_id):
    """Sprint metadata + pending request inbox + merged member rows.

    The "Pending plan requests" inbox (issue #718) is the primary CTA
    for plan creation: it lists distinct members who pinged the team
    for a plan in this sprint and don't yet have one. The "Add member"
    button is demoted to a secondary, less-common path.
    """
    sprint = get_object_or_404(
        Sprint.objects.select_related('event_series'),
        pk=sprint_id,
    )
    plans = list(
        Plan.objects.filter(sprint=sprint)
        .select_related('member')
        .order_by('created_at', 'pk')
    )
    _attach_plan_ready_email_state(plans)
    enrollments = list(
        sprint.enrollments
        .select_related('user', 'enrolled_by')
        .order_by('enrolled_at', 'pk')
    )
    enrollment_count = len(enrollments)
    sprint_member_rows = _build_sprint_member_rows(
        sprint=sprint,
        enrollments=enrollments,
        plans=plans,
    )
    event_series = sprint.event_series
    event_series_events = (
        list(event_series.events.all().order_by('start_datetime'))
        if event_series else []
    )
    pending_plan_requests = _build_pending_plan_requests(sprint)
    feedback_context = _build_sprint_feedback_context(sprint)
    plan_ready_email_preview = preview_plan_ready_emails(sprint)
    return render(request, 'studio/sprints/detail.html', {
        'sprint': sprint,
        'plans': plans,
        'enrollments': enrollments,
        'enrollment_count': enrollment_count,
        'plan_count': len(plans),
        'sprint_member_rows': sprint_member_rows,
        'event_series': event_series,
        'event_series_events': event_series_events,
        'pending_plan_requests': pending_plan_requests,
        'plan_ready_email_preview': plan_ready_email_preview,
        **feedback_context,
    })


def _attach_plan_ready_email_state(plans):
    logs = PlanReadyEmailLog.objects.filter(
        plan_id__in=[plan.pk for plan in plans],
    )
    logs_by_plan_id = {log.plan_id: log for log in logs}
    for plan in plans:
        log = logs_by_plan_id.get(plan.pk)
        plan.ready_email_log = log
        if log is None:
            plan.ready_email_state = 'not_emailed'
            plan.ready_email_state_label = 'Not emailed'
            plan.ready_email_sent_at = None
            plan.ready_email_last_error = ''
        elif log.status == PLAN_READY_EMAIL_STATUS_SENT:
            plan.ready_email_state = 'sent'
            plan.ready_email_state_label = 'Emailed'
            plan.ready_email_sent_at = log.sent_at
            plan.ready_email_last_error = ''
        elif log.status == PLAN_READY_EMAIL_STATUS_FAILED:
            plan.ready_email_state = 'failed'
            plan.ready_email_state_label = 'Failed'
            plan.ready_email_sent_at = None
            plan.ready_email_last_error = log.last_error
        elif log.status == PLAN_READY_EMAIL_STATUS_SENDING:
            plan.ready_email_state = 'sending'
            plan.ready_email_state_label = 'Sending'
            plan.ready_email_sent_at = None
            plan.ready_email_last_error = ''
        else:
            plan.ready_email_state = 'not_emailed'
            plan.ready_email_state_label = 'Not emailed'
            plan.ready_email_sent_at = None
            plan.ready_email_last_error = ''


def _flash_plan_create_ready_email_result(request, plan, result):
    if result['sent']:
        messages.success(
            request,
            f'Plan created for {plan.member.email} in "{plan.sprint.name}" '
            'and plan-ready email sent.',
        )
    elif result['failed']:
        messages.warning(
            request,
            f'Plan created for {plan.member.email} in "{plan.sprint.name}", '
            'but the plan-ready email failed. The member can be retried from '
            'the sprint plan-ready email panel.',
        )
    else:
        messages.info(
            request,
            f'Plan created for {plan.member.email} in "{plan.sprint.name}".',
        )


@staff_required
@require_POST
def sprint_send_plan_ready_emails(request, sprint_id):
    """Send idempotent bulk plan-ready emails for one sprint."""
    sprint = get_object_or_404(Sprint, pk=sprint_id)
    summary = send_plan_ready_emails(
        sprint=sprint,
        actor=request.user,
        dry_run=False,
    )
    if summary['failed_count']:
        messages.warning(
            request,
            (
                'Plan-ready emails sent: '
                f'{summary["sent_count"]} sent, '
                f'{summary["skipped_already_sent_count"]} skipped, '
                f'{summary["failed_count"]} failed.'
            ),
        )
    elif summary['sent_count']:
        messages.success(
            request,
            (
                'Plan-ready emails sent: '
                f'{summary["sent_count"]} sent, '
                f'{summary["skipped_already_sent_count"]} skipped, '
                '0 failed.'
            ),
        )
    else:
        messages.info(
            request,
            (
                'All plan-ready emails have already been sent. '
                f'{summary["skipped_already_sent_count"]} skipped.'
            ),
        )
    return redirect('studio_sprint_detail', sprint_id=sprint.pk)


def _build_sprint_feedback_context(sprint):
    """Build the "Sprint feedback" section context for the sprint detail page.

    Returns:
    - ``feedback_request``: the attached :class:`SprintFeedbackRequest`
      or None (a sprint may hold several over time; we surface the most
      recent for the inline section).
    - ``feedback_questionnaire_choices``: active feedback questionnaires
      offered by the attach picker.
    - ``feedback_completion_rows``: per-enrolled-member status rows when
      distributed; empty otherwise.
    - ``feedback_submitted_count`` / ``feedback_total_count``: the
      "X of Y submitted" aggregate.
    """
    feedback_request = (
        SprintFeedbackRequest.objects
        .filter(sprint=sprint)
        .select_related('questionnaire')
        .order_by('-created_at')
        .first()
    )
    choices = list(
        Questionnaire.objects
        .filter(purpose='feedback', is_active=True)
        .order_by('title')
    )

    rows = []
    submitted_count = 0
    total_count = 0
    if feedback_request and feedback_request.distributed_at:
        enrollments = list(
            sprint.enrollments.select_related('user').order_by('enrolled_at')
        )
        total_count = len(enrollments)
        # One Response lookup keyed by respondent for this questionnaire.
        responses_by_user = {}
        responses = (
            Response.objects
            .filter(questionnaire=feedback_request.questionnaire)
            .annotate(answer_count=Count('answers', distinct=True))
        )
        for resp in responses:
            responses_by_user[resp.respondent_id] = resp
        for enrollment in enrollments:
            resp = responses_by_user.get(enrollment.user_id)
            if resp is None:
                status_label = 'Not started'
                status_key = 'not_started'
                submitted_at = None
                response_id = None
            elif resp.status == 'submitted':
                status_label = 'Submitted'
                status_key = 'submitted'
                submitted_at = resp.submitted_at
                response_id = resp.pk
                submitted_count += 1
            elif resp.answer_count > 0:
                status_label = 'In progress'
                status_key = 'in_progress'
                submitted_at = None
                response_id = resp.pk
            else:
                status_label = 'Not started'
                status_key = 'not_started'
                submitted_at = None
                response_id = resp.pk
            rows.append({
                'member': enrollment.user,
                'status_label': status_label,
                'status_key': status_key,
                'submitted_at': submitted_at,
                'response_id': response_id,
            })

    context = {
        'feedback_request': feedback_request,
        'feedback_questionnaire_choices': choices,
        'feedback_completion_rows': rows,
        'feedback_submitted_count': submitted_count,
        'feedback_total_count': total_count,
    }
    context.update(_build_ai_summary_context(feedback_request))
    return context


def _submitted_response_count(feedback_request):
    """Count submitted responses to ``feedback_request``'s questionnaire.

    Independent of the completion-rows aggregate (which is only built for
    distributed requests) so the AI-summary subsection always knows how
    many responses are available to synthesize.
    """
    if feedback_request is None:
        return 0
    return (
        Response.objects
        .filter(
            questionnaire=feedback_request.questionnaire,
            status='submitted',
        )
        .count()
    )


def _build_ai_summary_context(feedback_request):
    """Build the "AI summary" subsection context for the feedback section.

    Returns the gating state (``ai_summary_enabled``), the current
    submitted-response count, the stored :class:`SprintFeedbackSummary`
    (if any), and a ``ai_summary_is_stale`` flag set when more responses
    have been submitted than the stored summary covered.
    """
    enabled = llm.is_enabled()
    submitted_count = _submitted_response_count(feedback_request)
    summary = None
    if feedback_request is not None:
        summary = SprintFeedbackSummary.objects.filter(
            feedback_request=feedback_request,
        ).select_related('generated_by').first()
    is_stale = bool(
        summary is not None and summary.response_count != submitted_count
    )
    return {
        'ai_summary_enabled': enabled,
        'ai_summary_submitted_count': submitted_count,
        'ai_summary': summary,
        'ai_summary_is_stale': is_stale,
        'ai_summary_settings_url': reverse('studio_settings'),
    }


@staff_required
@require_POST
def sprint_feedback_attach(request, sprint_id):
    """Attach a feedback questionnaire to a sprint (issue #803).

    Validates the chosen questionnaire is ``purpose='feedback'`` and
    active. On an invalid / missing pick, re-renders the sprint detail
    page with HTTP 400 and an error -- no ``SprintFeedbackRequest`` row
    is written.
    """
    sprint = get_object_or_404(Sprint, pk=sprint_id)
    raw_id = (request.POST.get('questionnaire') or '').strip()

    def _reject(error):
        plans = (
            Plan.objects.filter(sprint=sprint)
            .select_related('member').order_by('-created_at')
        )
        event_series = sprint.event_series
        context = {
            'sprint': sprint,
            'plans': plans,
            'enrollment_count': sprint.enrollments.count(),
            'event_series': event_series,
            'event_series_events': (
                list(event_series.events.all().order_by('start_datetime'))
                if event_series else []
            ),
            'pending_plan_requests': _build_pending_plan_requests(sprint),
            'feedback_error': error,
            **_build_sprint_feedback_context(sprint),
        }
        return render(request, 'studio/sprints/detail.html', context, status=400)

    if not raw_id.isdigit():
        return _reject('Pick a feedback questionnaire to attach.')
    questionnaire = (
        Questionnaire.objects
        .filter(pk=int(raw_id), purpose='feedback', is_active=True)
        .first()
    )
    if questionnaire is None:
        return _reject(
            'Selected questionnaire is not an active feedback questionnaire.'
        )

    SprintFeedbackRequest.objects.get_or_create(
        sprint=sprint,
        questionnaire=questionnaire,
        defaults={'created_by': request.user},
    )
    messages.success(
        request,
        f'Attached "{questionnaire.title}" as feedback for this sprint.',
    )
    return redirect('studio_sprint_detail', sprint_id=sprint.pk)


@staff_required
@require_POST
def sprint_feedback_distribute(request, sprint_id, feedback_request_id):
    """Distribute a sprint's feedback questionnaire to enrolled members.

    Idempotent (see :func:`plans.services.distribute_sprint_feedback`):
    re-running picks up newly enrolled members without duplicating
    existing responses. Reports created-vs-existing counts.
    """
    sprint = get_object_or_404(Sprint, pk=sprint_id)
    feedback_request = get_object_or_404(
        SprintFeedbackRequest, pk=feedback_request_id, sprint=sprint,
    )

    summary = distribute_sprint_feedback(feedback_request, actor=request.user)
    if summary['existing']:
        messages.success(
            request,
            f'{summary["created"]} feedback response(s) created; '
            f'{summary["existing"]} already existed.',
        )
    else:
        messages.success(
            request,
            f'{summary["created"]} feedback response(s) created.',
        )
    return redirect('studio_sprint_detail', sprint_id=sprint.pk)


def _assemble_feedback_input(sprint, feedback_request):
    """Map submitted ORM responses to the ORM-free ``SprintFeedbackInput``.

    Reads only ``status='submitted'`` responses for the feedback
    request's questionnaire, then flattens each response's answers into
    ``(question_text, question_type, answer_text)`` tuples. All ORM
    access lives here; the synthesis callable receives plain data only.
    """
    responses = (
        Response.objects
        .filter(
            questionnaire=feedback_request.questionnaire,
            status='submitted',
        )
        .prefetch_related('answers__question', 'answers__selected_options')
        .order_by('submitted_at', 'id')
    )

    response_entries = []
    for response in responses:
        answers = []
        for answer in response.answers.all():
            question = answer.question
            answers.append((
                question.prompt,
                question.question_type,
                answer.display_value,
            ))
        response_entries.append({'answers': answers})

    return SprintFeedbackInput(
        sprint_name=sprint.name,
        start_date=sprint.start_date.isoformat() if sprint.start_date else '',
        duration_weeks=sprint.duration_weeks,
        response_count=len(response_entries),
        responses=response_entries,
    )


@staff_required
@require_POST
def sprint_feedback_synthesize(request, sprint_id, feedback_request_id):
    """Generate (or regenerate) the AI summary of a sprint's feedback.

    The thin wrapper over
    :func:`integrations.services.feedback_synthesis.synthesize_feedback`:
    it does the ORM reads, maps them to the plain ``SprintFeedbackInput``,
    calls the pure callable, and upserts the single
    :class:`SprintFeedbackSummary` row via ``update_or_create`` (regenerate
    overwrites, never duplicates). No prompt or LLM logic lives here.

    Gating: if the LLM service is off, redirect with the disabled message
    rather than calling the callable. On an ``LLMError`` from the callable,
    redirect back with an error message and write no row -- no partial
    summary is ever stored.
    """
    sprint = get_object_or_404(Sprint, pk=sprint_id)
    feedback_request = get_object_or_404(
        SprintFeedbackRequest, pk=feedback_request_id, sprint=sprint,
    )

    if not llm.is_enabled():
        messages.error(
            request,
            'AI synthesis is off — configure an LLM provider in '
            'Settings > AI before generating a summary.',
        )
        return redirect('studio_sprint_detail', sprint_id=sprint.pk)

    feedback_input = _assemble_feedback_input(sprint, feedback_request)
    if not feedback_input.responses:
        messages.error(
            request,
            'No submitted feedback to summarize yet.',
        )
        return redirect('studio_sprint_detail', sprint_id=sprint.pk)

    try:
        result = synthesize_feedback(feedback_input)
    except LLMError:
        messages.error(
            request,
            'Could not generate summary — the LLM request failed. Try again.',
        )
        return redirect('studio_sprint_detail', sprint_id=sprint.pk)

    SprintFeedbackSummary.objects.update_or_create(
        feedback_request=feedback_request,
        defaults={
            'result_json': result.model_dump(),
            'response_count': result.response_count,
            'model_name': get_config('LLM_MODEL', 'claude-sonnet-4-5'),
            'generated_by': request.user,
            'generated_at': timezone.now(),
        },
    )
    messages.success(
        request,
        f'AI summary generated from {result.response_count} response(s).',
    )
    return redirect('studio_sprint_detail', sprint_id=sprint.pk)


@staff_required
def sprint_edit(request, sprint_id):
    """Edit name, slug, start date, duration, status."""
    sprint = get_object_or_404(Sprint, pk=sprint_id)

    if request.method != 'POST':
        return _render_form(
            request,
            sprint=sprint,
            form_action='edit',
            form_data=_form_data_from_sprint(sprint),
        )

    form_data = _form_data_from_post(request)
    name = form_data['name']
    raw_slug = form_data['slug']
    start_date, date_error = _parse_start_date(form_data['start_date'])
    duration, duration_error = _parse_duration_weeks(form_data['duration_weeks'])
    status_value = _normalize_status(form_data['status'])
    min_tier_level, tier_error = _parse_min_tier_level(form_data['min_tier_level'])
    event_series, event_series_error = _parse_event_series(form_data['event_series'])

    if not name:
        return _render_form(
            request, sprint=sprint, form_action='edit',
            form_data=form_data, error='Name is required.', status=400,
        )

    slug = raw_slug or slugify(name)
    if not slug:
        return _render_form(
            request, sprint=sprint, form_action='edit',
            form_data=form_data, error='Slug could not be derived from name.', status=400,
        )

    if date_error:
        return _render_form(
            request, sprint=sprint, form_action='edit',
            form_data=form_data, error=date_error, status=400,
        )
    if duration_error:
        return _render_form(
            request, sprint=sprint, form_action='edit',
            form_data=form_data, error=duration_error, status=400,
        )
    if tier_error:
        return _render_form(
            request, sprint=sprint, form_action='edit',
            form_data=form_data, error=tier_error, status=400,
        )
    if event_series_error:
        return _render_form(
            request, sprint=sprint, form_action='edit',
            form_data=form_data, error=event_series_error, status=400,
        )

    if Sprint.objects.filter(slug=slug).exclude(pk=sprint.pk).exists():
        return _render_form(
            request, sprint=sprint, form_action='edit',
            form_data=form_data,
            error=f'A different sprint already uses slug "{slug}".',
            status=400,
        )

    sprint.name = name
    sprint.slug = slug
    sprint.start_date = start_date
    sprint.duration_weeks = duration
    sprint.status = status_value
    sprint.min_tier_level = min_tier_level
    sprint.event_series = event_series
    sprint.save()

    messages.success(request, f'Sprint "{sprint.name}" updated.')
    return redirect('studio_sprint_detail', sprint_id=sprint.pk)


@staff_required
def sprint_add_member(request, sprint_id):
    """Form: pick a member and one-click enroll + create their plan.

    Issue #444. The sprint is locked from the URL; the member picker
    is the same reusable people picker the standalone create-plan form
    uses (``templates/studio/plans/form.html``). On a valid POST we
    delegate to :func:`plans.services.create_plan_for_enrollment`,
    which is shared with ``studio_plan_create`` so the empty-plan
    artefact (one Week per ``sprint.duration_weeks``, theme blank,
    zero checkpoints) stays consistent across surfaces.

    Idempotent. Re-submitting the same ``(sprint, user)`` pair never
    duplicates rows: we redirect back to the existing plan editor with
    a ``messages.info`` flash containing ``Already enrolled``.

    Issue #735 swapped the inline member ``<select>`` for the people
    picker include. The sprint is always known here, so the picker's
    ``extra_query`` is always set to ``sprint=<slug>`` -- suggestion
    rows always carry the sprint-context badges (``In this sprint`` /
    ``Has plan in sprint``). On a POST validation error, the picker's
    visible input is re-seeded from the submitted ``member`` pk so the
    operator doesn't have to retype.
    """
    sprint = get_object_or_404(Sprint, pk=sprint_id)
    user_search_url = reverse('studio_user_search')
    # The sprint is locked from the URL; the picker passes ``sprint=<slug>``
    # so suggestion rows show the sprint-context badges (issue #735).
    picker_extra_query = urlencode({'sprint': sprint.slug})

    def _prefill_display(member_id_str):
        """Resolve the picker's visible-input prefill on POST re-render."""
        if not member_id_str or not member_id_str.isdigit():
            return ''
        user = User.objects.filter(pk=int(member_id_str)).first()
        if user is None:
            return ''
        full = (user.get_full_name() or '').strip()
        return full or user.email

    if request.method != 'POST':
        return render(request, 'studio/plans/form.html', {
            'plan': None,
            'form_action': 'add_member',
            'form_action_url': request.path,
            'form_data': {
                'member': '',
                'sprint': str(sprint.pk),
                'send_ready_email': True,
            },
            'sprint': sprint,
            'user_search_url': user_search_url,
            'picker_extra_query': picker_extra_query,
            'prefill_member_display': '',
            'ready_email_sprint_name': sprint.name,
            'error': '',
            'primary_label': 'Add member',
        })

    raw_member = (request.POST.get('member') or '').strip()
    form_data = {
        'member': raw_member,
        'sprint': str(sprint.pk),
        'send_ready_email': request.POST.get('send_ready_email') == 'on',
    }

    def _render_with_error(error, status=400):
        return render(request, 'studio/plans/form.html', {
            'plan': None,
            'form_action': 'add_member',
            'form_action_url': request.path,
            'form_data': form_data,
            'sprint': sprint,
            'user_search_url': user_search_url,
            'picker_extra_query': picker_extra_query,
            'prefill_member_display': _prefill_display(raw_member),
            'ready_email_sprint_name': sprint.name,
            'error': error,
            'primary_label': 'Add member',
        }, status=status)

    if not raw_member.isdigit():
        return _render_with_error('Pick a member.')

    member = User.objects.filter(pk=int(raw_member)).first()
    if member is None:
        return _render_with_error('Selected member does not exist.')

    plan, _enrollment, created_now = create_plan_for_enrollment(
        sprint=sprint,
        user=member,
        enrolled_by=request.user,
    )

    if created_now:
        if form_data['send_ready_email']:
            result = send_plan_ready_email_for_plan(plan, actor=request.user)
            _flash_plan_create_ready_email_result(request, plan, result)
        else:
            messages.success(
                request,
                f'Plan created for {member.email} in "{sprint.name}". '
                'Plan-ready email not sent.',
            )
    else:
        messages.info(
            request,
            f'Already enrolled — opening existing plan for {member.email}.',
        )

    return redirect('studio_plan_edit', plan_id=plan.pk)


@staff_required
def sprint_plan_request_prepare(request, sprint_id, member_id):
    """Locked staff workflow for preparing a requested member plan."""
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])

    sprint = get_object_or_404(Sprint, pk=sprint_id)
    member = get_object_or_404(User, pk=member_id)
    existing_plan = Plan.objects.filter(sprint=sprint, member=member).first()
    if existing_plan is not None:
        messages.info(
            request,
            f'{member.email} already has a plan in "{sprint.name}".',
        )
        return redirect('studio_plan_edit', plan_id=existing_plan.pk)

    request_qs = PlanRequest.objects.filter(sprint=sprint, member=member)
    if not request_qs.exists():
        raise Http404('No outstanding plan request for this sprint member')

    request_summary = request_qs.aggregate(
        request_count=Count('id'),
        last_requested_at=Max('created_at'),
    )
    onboarding_state = _onboarding_state_for_member(member)
    target = _member_context_target(member)
    member_profile = build_member_profile_context(member)

    return render(request, 'studio/sprints/plan_request_prepare.html', {
        'sprint': sprint,
        'member': member,
        'request_summary': request_summary,
        'onboarding_state': onboarding_state,
        'member_profile': member_profile,
        'member_context_target': target,
        'create_url': reverse(
            'studio_sprint_plan_request_create_plan',
            kwargs={'sprint_id': sprint.pk, 'member_id': member.pk},
        ),
    })


@staff_required
def sprint_plan_request_create_plan(request, sprint_id, member_id):
    """Inbox button: create a plan for a member who pinged for one.

    Issue #718. POST-only, staff-only, idempotent. Delegates to
    :func:`plans.services.create_plan_for_enrollment` so the artefact
    shape stays consistent with the other plan-creation surfaces.

    The :class:`PlanRequest` rows are NOT deleted on success -- they
    remain as audit history. The member just disappears from the
    inbox on the next page load because the left-anti-join now matches
    the new ``Plan`` row.

    Race / double-click safe: ``create_plan_for_enrollment`` already
    catches ``IntegrityError`` on its inner ``Plan.objects.create``
    and re-fetches the existing row, so two concurrent POSTs cannot
    create two plans for the same ``(sprint, member)`` pair.
    """
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    sprint = get_object_or_404(Sprint, pk=sprint_id)
    member = get_object_or_404(User, pk=member_id)

    existing_plan = Plan.objects.filter(sprint=sprint, member=member).first()
    if existing_plan is not None:
        messages.info(
            request,
            f'{member.email} already has a plan in "{sprint.name}".',
        )
        return redirect('studio_plan_edit', plan_id=existing_plan.pk)

    if not PlanRequest.objects.filter(sprint=sprint, member=member).exists():
        raise Http404('No outstanding plan request for this sprint member')

    onboarding_state = _onboarding_state_for_member(member)
    if onboarding_state['is_incomplete']:
        messages.error(
            request,
            'Onboarding is incomplete. Wait for the member to submit '
            'onboarding before creating a plan from this request.',
        )
        return redirect(
            'studio_sprint_plan_request_prepare',
            sprint_id=sprint.pk,
            member_id=member.pk,
        )

    plan, _enrollment, created_now = create_plan_for_enrollment(
        sprint=sprint,
        user=member,
        enrolled_by=request.user,
    )

    if created_now:
        result = send_plan_ready_email_for_plan(plan, actor=request.user)
        _flash_plan_create_ready_email_result(request, plan, result)
    else:
        messages.info(
            request,
            f'{member.email} already has a plan in "{sprint.name}".',
        )

    return redirect('studio_plan_edit', plan_id=plan.pk)


@staff_required
@require_POST
def sprint_cancel(request, sprint_id):
    """Soft-cancel a sprint (issue #949).

    Sets ``status='cancelled'`` -- a reversible state change. All plans,
    enrollments, feedback requests, and AI summaries are retained; a
    cancelled sprint is just excluded from member-facing self-join and
    surfaces a "Cancelled" badge. Idempotent: cancelling an
    already-cancelled sprint is a no-op info message.
    """
    sprint = get_object_or_404(Sprint, pk=sprint_id)
    if sprint.status == 'cancelled':
        messages.info(request, f'Sprint "{sprint.name}" is already cancelled.')
        return redirect('studio_sprint_detail', sprint_id=sprint.pk)

    sprint.status = 'cancelled'
    sprint.save(update_fields=['status'])
    logger.info(
        'studio.sprint_cancel actor=%s sprint_id=%s',
        request.user.pk, sprint.pk,
    )
    messages.success(request, f'Sprint "{sprint.name}" cancelled.')
    return redirect('studio_sprint_detail', sprint_id=sprint.pk)


@staff_required
@require_POST
def sprint_delete(request, sprint_id):
    """Hard-delete a sprint, guarded to empty sprints only (issue #949).

    Allowed ONLY when the sprint has zero plans AND zero enrollments (a
    draft created by mistake). If either exists, the delete is blocked
    and the operator is told to cancel instead. This mirrors the API's
    409 guard semantics; ``Plan.sprint`` is also ``PROTECT`` so a
    populated sprint cannot be deleted anyway.
    """
    sprint = get_object_or_404(Sprint, pk=sprint_id)
    if sprint.plans.exists() or sprint.enrollments.exists():
        messages.error(
            request,
            'Cannot delete a sprint with plans or enrollments — '
            'cancel it instead.',
        )
        return redirect('studio_sprint_detail', sprint_id=sprint.pk)

    name = sprint.name
    sprint_pk = sprint.pk
    sprint.delete()
    logger.info(
        'studio.sprint_delete actor=%s sprint_id=%s',
        request.user.pk, sprint_pk,
    )
    messages.success(request, f'Sprint "{name}" deleted.')
    return redirect('studio_sprint_list')


@staff_required
@require_POST
def sprint_unenroll(request, sprint_id, enrollment_id):
    """Hard-delete a single sprint enrollment row (issue #949).

    Cross-sprint safety: the enrollment must belong to the sprint in the
    URL. A mismatched ``enrollment_id`` returns 404 instead of
    unenrolling someone from the wrong sprint (mirrors the course
    ``enrollment_unenroll`` pattern). Deleting the membership row leaves
    the user account and any plan in this sprint untouched.
    """
    enrollment = get_object_or_404(
        SprintEnrollment, pk=enrollment_id, sprint_id=sprint_id,
    )
    sprint = enrollment.sprint
    user = enrollment.user
    email = user.email
    has_plan = Plan.objects.filter(sprint=sprint, member=user).exists()

    enrollment.delete()
    logger.info(
        'studio.sprint_unenroll actor=%s sprint_id=%s user_id=%s plan_kept=%s',
        request.user.pk, sprint.pk, user.pk, has_plan,
    )
    if has_plan:
        messages.success(
            request,
            f'Unenrolled {email} from "{sprint.name}". Their plan was kept.',
        )
    else:
        messages.success(
            request,
            f'Unenrolled {email} from "{sprint.name}".',
        )
    return redirect('studio_sprint_detail', sprint_id=sprint.pk)
