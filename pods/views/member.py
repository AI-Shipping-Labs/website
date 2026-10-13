"""Member-facing pod pages for cohort and sprint pods (issues #1918, #1921).

Cohort pods live under ``/courses/<slug>/home/pods``: the course must be
listed in ``PODS_COURSE_SLUGS`` or every page is a 404, and viewers need a
``CohortEnrollment`` in the pod's dated cohort plus course access.

Sprint pods live under ``/sprints/<sprint_slug>/pods``: the sprint must have
``pods_enabled`` (else 404 for everyone, staff included), draft and
cancelled sprints 404 for non-staff, and viewers need a ``SprintEnrollment``.

Every view takes either ``slug`` (course) or ``sprint_slug`` from its URL,
so one view and one template set serve both activities. Staff may open any
pod page as an operator view. Anonymous visitors go to login. Writes are
session POSTs that redirect back with a Django message.
"""

import time

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts.services.timezones import build_timezone_options, get_timezone_label, is_valid_timezone
from content.models import Cohort, Course
from content.services.course_schedule import select_display_cohort
from plans.models import Sprint
from pods.models import OPEN_REQUEST_STATUSES, POD_STATUS_ARCHIVED, Pod, PodJoinRequest
from pods.services import chat as chat_svc
from pods.services import config as pods_config
from pods.services import membership as svc
from pods.services.activity import activity_for_pod, cohort_activity, sprint_activity
from pods.services.availability import (
    AvailabilityError,
    editor_days,
    freshness_line,
    get_profile,
    has_windows,
    parse_window_rows,
    save_availability,
    summary_rows,
)
from pods.services.people import can_view_cohort_pods, is_slack_eligible
from pods.services.presentation import STATE_MEMBER, build_pod_page, build_pod_rows
from pods.services.slack import clean_channel_url
from pods.services.tab import pods_frame_context

LOGIN_URL = '/accounts/login/'
PAGE_SIZE = 25
DRAFT_MAX_CHARS = 4000

MSG_STARTED_NO_AVAILABILITY = 'Pod started. Add your availability so others can see if they fit.'


# --- Resolution helpers ----------------------------------------------------

def _enabled_course(slug):
    course = get_object_or_404(Course, slug=slug, status='published')
    if not pods_config.pods_enabled_for_course(course):
        raise Http404('Pods are not available for this course')
    return course


def _viewer_cohort(request, course):
    """The dated cohort whose pods this viewer sees, or 404."""
    requested = (request.GET.get('cohort') or request.POST.get('cohort') or '').strip()
    cohort, _is_preview = select_display_cohort(course, request.user, requested)
    if cohort is None and request.user.is_staff and not requested:
        cohort = Cohort.objects.filter(
            course=course, mode='cohort', is_active=True,
        ).order_by('start_date', 'pk').first()
    if cohort is None or not can_view_cohort_pods(request.user, cohort):
        raise Http404('Pods not found')
    return cohort


def _sprint_scope(request, sprint_slug):
    """The sprint activity for a viewer, or 404.

    ``pods_enabled`` off: 404 for everyone (staff use Studio). Draft and
    cancelled sprints, and users without a ``SprintEnrollment``: 404 for
    non-staff. Ended sprints keep their pods usable.
    """
    activity = sprint_activity(get_object_or_404(Sprint, slug=sprint_slug))
    if not activity.member_pages_enabled() or not activity.can_view(request.user):
        raise Http404('Pods not found')
    return activity


def _scope(request, slug=None, sprint_slug=None):
    """The activity whose pods the viewer sees (cohort or sprint), or 404."""
    if sprint_slug is not None:
        return _sprint_scope(request, sprint_slug)
    course = _enabled_course(slug)
    return cohort_activity(_viewer_cohort(request, course))


def _pod_for_viewer(request, pod_id, slug=None, sprint_slug=None):
    """The pod for a viewer under its course or sprint URL, or 404.

    A pod id from another course, another sprint or the other activity
    type is a 404.
    """
    if sprint_slug is not None:
        activity = _sprint_scope(request, sprint_slug)
        pod = get_object_or_404(
            Pod.objects.select_related('sprint', 'owner'), pk=pod_id, sprint=activity.sprint,
        )
    else:
        course = _enabled_course(slug)
        pod = get_object_or_404(
            Pod.objects.select_related('cohort__course', 'owner'),
            pk=pod_id, cohort__course=course,
        )
        if not can_view_cohort_pods(request.user, pod.cohort):
            raise Http404('Pod not found')
    if pod.status == POD_STATUS_ARCHIVED and not request.user.is_staff:
        raise Http404('Pod not found')
    return pod


def _safe_next(request, fallback):
    candidate = request.POST.get('next') or request.GET.get('next') or ''
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ):
        return candidate
    return fallback


def _page_number(raw, total_pages):
    try:
        number = int(raw)
    except (TypeError, ValueError):
        number = 1
    return min(max(number, 1), total_pages)


# --- Pages -------------------------------------------------------------------

@login_required(login_url=LOGIN_URL)
def pods_list(request, slug=None, sprint_slug=None):
    """GET ``/courses/<slug>/home/pods`` (the Pods tab) or ``/sprints/<sprint_slug>/pods``."""
    activity = _scope(request, slug, sprint_slug)
    rows, viewer_availability = build_pod_rows(activity, request.user)
    _add_unread_lines(rows, request.user)
    total_pages = max((len(rows) + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    page_number = _page_number(request.GET.get('page'), total_pages)
    base = activity.list_url()
    joiner = '&' if '?' in base else '?'
    context = pods_frame_context(activity, request.user)
    context.update({
        'rows': rows[(page_number - 1) * PAGE_SIZE:page_number * PAGE_SIZE],
        'page_number': page_number,
        'total_pages': total_pages,
        'prev_page_url': f'{base}{joiner}page={page_number - 1}' if page_number > 1 else '',
        'next_page_url': f'{base}{joiner}page={page_number + 1}' if page_number < total_pages else '',
        'can_start': activity.is_participant(request.user),
        'viewer_has_availability': bool(viewer_availability and viewer_availability.has_windows),
        'availability_url': activity.availability_url(),
        'new_pod_url': activity.new_url(),
    })
    return render(request, 'pods/list.html', context)


@login_required(login_url=LOGIN_URL)
def pod_new(request, slug=None, sprint_slug=None):
    """GET/POST ``.../pods/new`` -- start a pod of one."""
    activity = _scope(request, slug, sprint_slug)
    if not activity.is_participant(request.user):
        raise Http404('Pods not found')
    form = {'name': '', 'purpose': '', 'max_members': '', 'meeting_count': '', 'meeting_minutes': ''}
    error = error_field = ''
    if request.method == 'POST':
        form = {key: request.POST.get(key, '') for key in form}
        try:
            pod = svc.start_member_pod(request.user, activity, form)
        except svc.PodError as exc:
            error, error_field = exc.message, exc.field
        else:
            if has_windows(get_profile(request.user)):
                messages.success(request, 'Pod started.')
            else:
                messages.success(request, MSG_STARTED_NO_AVAILABILITY)
            return redirect(svc.pod_url(pod))
    context = pods_frame_context(activity, request.user)
    context.update(_settings_form_context(form, error, error_field))
    context['cancel_url'] = activity.list_url()
    return render(request, 'pods/new.html', context, status=400 if error else 200)


def _settings_form_context(form, error, error_field):
    return {
        'form': form,
        'error': error,
        'error_field': error_field,
        'default_max_members': pods_config.default_max_members(),
        'default_meeting_count': pods_config.default_meeting_count(),
        'default_meeting_minutes': pods_config.default_meeting_minutes(),
        'meeting_minute_options': pods_config.MEETING_MINUTE_OPTIONS,
    }


@login_required(login_url=LOGIN_URL)
def availability_edit(request, slug=None, sprint_slug=None):
    """GET/POST ``.../pods/availability`` -- my weekly windows (one profile
    shared by every pod activity)."""
    activity = _scope(request, slug, sprint_slug)
    profile = get_profile(request.user)
    back_url = _safe_next(request, activity.list_url())
    error = ''
    timezone_value = (
        profile.timezone if profile is not None
        else (request.user.preferred_timezone or '')
    )
    specs = None
    if request.method == 'POST':
        timezone_value = (request.POST.get('timezone') or '').strip()
        try:
            specs = parse_window_rows(request.POST)
            save_availability(request.user, timezone_value, specs)
        except AvailabilityError as exc:
            error = str(exc)
        else:
            messages.success(request, 'Availability saved.')
            # Without ``next`` the editor reloads, so the saved summary shows.
            return redirect(_safe_next(request, activity.availability_url()))
    context = pods_frame_context(activity, request.user)
    context.update({
        'profile': profile,
        'days': editor_days(profile, specs),
        'error': error,
        'timezone_value': timezone_value if is_valid_timezone(timezone_value) else '',
        'has_saved_timezone': bool(profile is not None or request.user.preferred_timezone),
        'timezone_label': get_timezone_label(timezone_value) if is_valid_timezone(timezone_value) else '',
        'timezone_options': build_timezone_options(),
        'freshness_line': freshness_line(profile),
        'summary': summary_rows(profile),
        'back_url': back_url,
        'next_value': request.POST.get('next') or request.GET.get('next') or '',
    })
    return render(request, 'pods/availability.html', context, status=400 if error else 200)


@login_required(login_url=LOGIN_URL)
def pod_detail(request, pod_id, slug=None, sprint_slug=None):
    """GET ``.../pods/<pod_id>`` -- the pod page."""
    pod = _pod_for_viewer(request, pod_id, slug, sprint_slug)
    activity = activity_for_pod(pod)
    context = pods_frame_context(activity, request.user)
    page = build_pod_page(pod, request.user)
    context.update(page)
    context.update({
        'pods_list_url': activity.list_url(),
        'availability_url': activity.availability_url(svc.pod_url(pod)),
        'pod_url': svc.pod_url(pod),
        'slack_error': request.session.pop(f'pod_slack_error_{pod.pk}', ''),
    })
    call_link_error = request.session.pop(f'pod_call_link_error_{pod.pk}', None)
    if call_link_error and page['show_private']:
        context['call_link_error'] = call_link_error.get('message', '')
        context['call_link_value'] = call_link_error.get('value', '')
    context['chat'] = _chat_context(request, pod, page)
    return render(request, 'pods/detail.html', context)


# --- Pod chat (issue #1920) ---------------------------------------------------

def _chat_session_key(pod):
    return f'pod_chat_error_{pod.pk}'


def _chat_context(request, pod, page):
    """The Chat section for members and staff while ``PODS_CHAT_ENABLED`` is on.

    Opening the page as a member marks the chat read.
    """
    stored = request.session.pop(_chat_session_key(pod), None)
    if not chat_svc.chat_enabled() or not (page['is_member'] or request.user.is_staff):
        return None
    chat = chat_svc.member_chat(
        pod, request.user, is_member=page['is_member'], viewer_zone=page['viewer_zone'],
    )
    if stored and page['is_member']:
        chat['error'] = stored.get('message', '')
        chat['draft'] = stored.get('body', '')
    # A link to the same URL plus ``#chat`` would only scroll, never reload,
    # so Refresh carries the load time to force a fresh page.
    pod_url = svc.pod_url(pod)
    joiner = '&' if '?' in pod_url else '?'
    chat['refresh_url'] = f'{pod_url}{joiner}refresh={int(time.time() * 1000)}#chat'
    if page['is_member']:
        chat_svc.mark_read(pod, request.user)
    return chat


def _add_unread_lines(rows, viewer):
    """``N new messages`` on the viewer's own pod rows (one query)."""
    if not chat_svc.chat_enabled():
        return
    member_pod_ids = [row['pod'].pk for row in rows if row['state'].key == STATE_MEMBER]
    counts = chat_svc.unread_counts(member_pod_ids, viewer)
    for row in rows:
        row['unread_line'] = chat_svc.unread_label(counts.get(row['pod'].pk, 0))


@login_required(login_url=LOGIN_URL)
def pod_edit(request, pod_id, slug=None, sprint_slug=None):
    """GET/POST ``.../pods/<pod_id>/edit`` -- owner or staff."""
    pod = _pod_for_viewer(request, pod_id, slug, sprint_slug)
    if not svc.can_manage(pod, request.user):
        return HttpResponseForbidden('Only the pod owner or staff can edit this pod.')
    fields = ('name', 'purpose', 'max_members', 'meeting_count', 'meeting_minutes', 'status')
    form = {
        'name': pod.name, 'purpose': pod.purpose, 'max_members': pod.max_members,
        'meeting_count': pod.meeting_count, 'meeting_minutes': pod.meeting_minutes,
        'status': pod.status,
    }
    error = error_field = ''
    if request.method == 'POST':
        form = {key: request.POST.get(key, '') for key in fields}
        if form['status'] not in ('open', 'closed'):
            form['status'] = pod.status if pod.status in ('open', 'closed') else 'open'
        try:
            svc.update_pod(pod, form, actor=request.user, staff=False)
        except svc.PodError as exc:
            error, error_field = exc.message, exc.field
        else:
            messages.success(request, 'Pod updated.')
            return redirect(svc.pod_url(pod))
    context = pods_frame_context(activity_for_pod(pod), request.user)
    context.update(_settings_form_context(form, error, error_field))
    context.update({'pod': pod, 'cancel_url': svc.pod_url(pod)})
    return render(request, 'pods/edit.html', context, status=400 if error else 200)


# --- Actions -----------------------------------------------------------------

def _redirect_with_error(request, pod, message):
    messages.error(request, message)
    return redirect(svc.pod_url(pod))


@require_POST
@login_required(login_url=LOGIN_URL)
def pod_request(request, pod_id, slug=None, sprint_slug=None):
    pod = _pod_for_viewer(request, pod_id, slug, sprint_slug)
    try:
        join_request = svc.request_to_join(pod, request.user, request.POST.get('message', ''))
    except svc.PodError as exc:
        return _redirect_with_error(request, pod, exc.message)
    if join_request.status == 'waitlisted':
        messages.success(request, 'You joined the waiting list.')
    else:
        messages.success(request, 'Request sent.')
    return redirect(svc.pod_url(pod))


@require_POST
@login_required(login_url=LOGIN_URL)
def pod_withdraw(request, pod_id, slug=None, sprint_slug=None):
    pod = _pod_for_viewer(request, pod_id, slug, sprint_slug)
    join_request = PodJoinRequest.objects.filter(
        pod=pod, user=request.user, status__in=OPEN_REQUEST_STATUSES,
    ).first()
    if join_request is None:
        return _redirect_with_error(request, pod, svc.MSG_REQUEST_NOT_OPEN)
    svc.withdraw_request(join_request, request.user)
    messages.success(request, 'Request withdrawn.')
    return redirect(svc.pod_url(pod))


@require_POST
@login_required(login_url=LOGIN_URL)
def pod_leave(request, pod_id, slug=None, sprint_slug=None):
    pod = _pod_for_viewer(request, pod_id, slug, sprint_slug)
    try:
        svc.remove_member(pod, request.user, actor=request.user)
    except svc.PodError as exc:
        return _redirect_with_error(request, pod, exc.message)
    messages.success(request, f'You left {pod.name}.')
    return redirect(svc.pods_list_url(pod))


def _decide(request, pod_id, request_id, decide, success_message, slug=None, sprint_slug=None):
    pod = _pod_for_viewer(request, pod_id, slug, sprint_slug)
    if not svc.can_manage(pod, request.user):
        return HttpResponseForbidden('Only the pod owner or staff can answer requests.')
    join_request = get_object_or_404(PodJoinRequest, pk=request_id, pod=pod)
    try:
        decide(join_request, request.user)
    except svc.PodError as exc:
        return _redirect_with_error(request, pod, exc.message)
    messages.success(request, success_message)
    return redirect(svc.pod_url(pod))


@require_POST
@login_required(login_url=LOGIN_URL)
def pod_request_approve(request, pod_id, request_id, slug=None, sprint_slug=None):
    return _decide(request, pod_id, request_id, svc.approve_request, 'Request approved.', slug, sprint_slug)


@require_POST
@login_required(login_url=LOGIN_URL)
def pod_request_decline(request, pod_id, request_id, slug=None, sprint_slug=None):
    return _decide(request, pod_id, request_id, svc.decline_request, 'Request declined.', slug, sprint_slug)


@require_POST
@login_required(login_url=LOGIN_URL)
def pod_slack(request, pod_id, slug=None, sprint_slug=None):
    """Set or clear the pod's Slack channel link (pod members at Main+)."""
    pod = _pod_for_viewer(request, pod_id, slug, sprint_slug)
    if not svc.is_member(pod, request.user) or not is_slack_eligible(request.user):
        return HttpResponseForbidden('Only pod members on Main or Premium can set the Slack link.')
    try:
        value = clean_channel_url(request.POST.get('slack_channel_url', ''))
    except ValueError as exc:
        request.session[f'pod_slack_error_{pod.pk}'] = str(exc)
        return redirect(f'{svc.pod_url(pod)}#slack')
    pod.slack_channel_url = value
    pod.save(update_fields=['slack_channel_url', 'updated_at'])
    messages.success(request, 'Slack channel saved.' if value else 'Slack channel link removed.')
    return redirect(f'{svc.pod_url(pod)}#slack')


@require_POST
@login_required(login_url=LOGIN_URL)
def pod_message_create(request, pod_id, slug=None, sprint_slug=None):
    """POST ``.../pods/<pod_id>/messages`` -- send a chat message."""
    pod = _pod_for_viewer(request, pod_id, slug, sprint_slug)
    if not chat_svc.chat_enabled():
        raise Http404('Pod chat is not available')
    chat_url = f'{svc.pod_url(pod)}#chat'
    raw_body = request.POST.get('body', '')
    try:
        chat_svc.post_message(pod, request.user, raw_body)
    except chat_svc.ChatError as exc:
        if exc.code not in chat_svc.DRAFT_CODES:
            return HttpResponseForbidden(chat_svc.MSG_NOT_A_MEMBER)
        # Keep the draft (capped) so the member can fix it after the redirect.
        request.session[_chat_session_key(pod)] = {'message': exc.message, 'body': raw_body[:DRAFT_MAX_CHARS]}
    return redirect(chat_url)
