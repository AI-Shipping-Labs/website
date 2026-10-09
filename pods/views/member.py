"""Member-facing pod pages under course Home (issue #1918).

Every URL lives under ``/courses/<slug>/home/pods``. A course must be listed
in ``PODS_COURSE_SLUGS`` or every page is a 404. Viewers need a
``CohortEnrollment`` in the pod's dated cohort plus course access; staff may
open any cohort's pages as an operator view. Anonymous visitors go to login.
Writes are session POSTs that redirect back with a Django message.
"""

from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts.services.timezones import build_timezone_options, get_timezone_label, is_valid_timezone
from content.models import Cohort, Course
from content.services.course_schedule import select_display_cohort
from pods.models import OPEN_REQUEST_STATUSES, POD_STATUS_ARCHIVED, Pod, PodJoinRequest
from pods.services import config as pods_config
from pods.services import membership as svc
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
from pods.services.people import can_view_cohort_pods, is_cohort_participant, is_slack_eligible
from pods.services.presentation import build_pod_page, build_pod_rows, pods_url
from pods.services.slack import clean_channel_url
from pods.services.tab import course_header_context

LOGIN_URL = '/accounts/login/'
PAGE_SIZE = 25

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


def _pod_for_viewer(request, course, pod_id):
    pod = get_object_or_404(
        Pod.objects.select_related('cohort__course', 'owner'),
        pk=pod_id, cohort__course=course,
    )
    if pod.status == POD_STATUS_ARCHIVED and not request.user.is_staff:
        raise Http404('Pod not found')
    if not can_view_cohort_pods(request.user, pod.cohort):
        raise Http404('Pod not found')
    return pod


def _safe_next(request, fallback):
    candidate = request.POST.get('next') or request.GET.get('next') or ''
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ):
        return candidate
    return fallback


def _with_query(url, params):
    params = [(key, value) for key, value in params if value]
    return f'{url}?{urlencode(params)}' if params else url


def availability_url(course, cohort, next_url=''):
    return _with_query(
        reverse('pod_availability', kwargs={'slug': course.slug}),
        [('cohort', cohort.external_key), ('next', next_url)],
    )


def new_pod_url(course, cohort):
    return _with_query(reverse('pod_new', kwargs={'slug': course.slug}), [('cohort', cohort.external_key)])


def _page_number(raw, total_pages):
    try:
        number = int(raw)
    except (TypeError, ValueError):
        number = 1
    return min(max(number, 1), total_pages)


# --- Pages -------------------------------------------------------------------

@login_required(login_url=LOGIN_URL)
def pods_list(request, slug):
    """GET ``/courses/<slug>/home/pods`` -- the Pods tab."""
    course = _enabled_course(slug)
    cohort = _viewer_cohort(request, course)
    rows, viewer_availability = build_pod_rows(cohort, request.user)
    total_pages = max((len(rows) + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    page_number = _page_number(request.GET.get('page'), total_pages)
    base = pods_url(course, cohort)
    joiner = '&' if '?' in base else '?'
    context = course_header_context(course, request.user, cohort)
    context.update({
        'rows': rows[(page_number - 1) * PAGE_SIZE:page_number * PAGE_SIZE],
        'page_number': page_number,
        'total_pages': total_pages,
        'prev_page_url': f'{base}{joiner}page={page_number - 1}' if page_number > 1 else '',
        'next_page_url': f'{base}{joiner}page={page_number + 1}' if page_number < total_pages else '',
        'can_start': is_cohort_participant(request.user, cohort),
        'viewer_has_availability': bool(viewer_availability and viewer_availability.has_windows),
        'availability_url': availability_url(course, cohort),
        'new_pod_url': new_pod_url(course, cohort),
    })
    return render(request, 'pods/list.html', context)


@login_required(login_url=LOGIN_URL)
def pod_new(request, slug):
    """GET/POST ``/courses/<slug>/home/pods/new`` -- start a pod of one."""
    course = _enabled_course(slug)
    cohort = _viewer_cohort(request, course)
    if not is_cohort_participant(request.user, cohort):
        raise Http404('Pods not found')
    form = {'name': '', 'purpose': '', 'max_members': '', 'meeting_count': '', 'meeting_minutes': ''}
    error = error_field = ''
    if request.method == 'POST':
        form = {key: request.POST.get(key, '') for key in form}
        try:
            pod = svc.start_member_pod(request.user, cohort, form)
        except svc.PodError as exc:
            error, error_field = exc.message, exc.field
        else:
            if has_windows(get_profile(request.user)):
                messages.success(request, 'Pod started.')
            else:
                messages.success(request, MSG_STARTED_NO_AVAILABILITY)
            return redirect(svc.pod_url(pod))
    context = course_header_context(course, request.user, cohort)
    context.update(_settings_form_context(form, error, error_field))
    context['cancel_url'] = pods_url(course, cohort)
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
def availability_edit(request, slug):
    """GET/POST ``/courses/<slug>/home/pods/availability`` -- my weekly windows."""
    course = _enabled_course(slug)
    cohort = _viewer_cohort(request, course)
    profile = get_profile(request.user)
    back_url = _safe_next(request, pods_url(course, cohort))
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
            return redirect(_safe_next(request, availability_url(course, cohort)))
    context = course_header_context(course, request.user, cohort)
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
def pod_detail(request, slug, pod_id):
    """GET ``/courses/<slug>/home/pods/<pod_id>`` -- the pod page."""
    course = _enabled_course(slug)
    pod = _pod_for_viewer(request, course, pod_id)
    context = course_header_context(course, request.user, pod.cohort)
    page = build_pod_page(pod, request.user)
    context.update(page)
    context.update({
        'pods_list_url': pods_url(course, pod.cohort),
        'availability_url': availability_url(course, pod.cohort, svc.pod_url(pod)),
        'pod_url': svc.pod_url(pod),
        'slack_error': request.session.pop(f'pod_slack_error_{pod.pk}', ''),
    })
    call_link_error = request.session.pop(f'pod_call_link_error_{pod.pk}', None)
    if call_link_error and page['show_private']:
        context['call_link_error'] = call_link_error.get('message', '')
        context['call_link_value'] = call_link_error.get('value', '')
    return render(request, 'pods/detail.html', context)


@login_required(login_url=LOGIN_URL)
def pod_edit(request, slug, pod_id):
    """GET/POST ``/courses/<slug>/home/pods/<pod_id>/edit`` -- owner or staff."""
    course = _enabled_course(slug)
    pod = _pod_for_viewer(request, course, pod_id)
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
    context = course_header_context(course, request.user, pod.cohort)
    context.update(_settings_form_context(form, error, error_field))
    context.update({'pod': pod, 'cancel_url': svc.pod_url(pod)})
    return render(request, 'pods/edit.html', context, status=400 if error else 200)


# --- Actions -----------------------------------------------------------------

def _redirect_with_error(request, pod, message):
    messages.error(request, message)
    return redirect(svc.pod_url(pod))


@require_POST
@login_required(login_url=LOGIN_URL)
def pod_request(request, slug, pod_id):
    course = _enabled_course(slug)
    pod = _pod_for_viewer(request, course, pod_id)
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
def pod_withdraw(request, slug, pod_id):
    course = _enabled_course(slug)
    pod = _pod_for_viewer(request, course, pod_id)
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
def pod_leave(request, slug, pod_id):
    course = _enabled_course(slug)
    pod = _pod_for_viewer(request, course, pod_id)
    try:
        svc.remove_member(pod, request.user, actor=request.user)
    except svc.PodError as exc:
        return _redirect_with_error(request, pod, exc.message)
    messages.success(request, f'You left {pod.name}.')
    return redirect(pods_url(course, pod.cohort))


def _decide(request, slug, pod_id, request_id, decide, success_message):
    course = _enabled_course(slug)
    pod = _pod_for_viewer(request, course, pod_id)
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
def pod_request_approve(request, slug, pod_id, request_id):
    return _decide(request, slug, pod_id, request_id, svc.approve_request, 'Request approved.')


@require_POST
@login_required(login_url=LOGIN_URL)
def pod_request_decline(request, slug, pod_id, request_id):
    return _decide(request, slug, pod_id, request_id, svc.decline_request, 'Request declined.')


@require_POST
@login_required(login_url=LOGIN_URL)
def pod_slack(request, slug, pod_id):
    """Set or clear the pod's Slack channel link (pod members at Main+)."""
    course = _enabled_course(slug)
    pod = _pod_for_viewer(request, course, pod_id)
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
