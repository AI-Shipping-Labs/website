"""Studio authoring for course announcements (issue #1942).

Each announcement targets one cohort of the course; the cohort can change
only while the announcement is a draft, and publishing requires one.
"""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from content.models import Course
from content.models.cohort import Cohort
from content.models.course_announcement import CourseAnnouncement
from content.services.course_announcements import (
    apply_publish_transition,
    publish_confirm_message,
    save_announcement,
)
from content.services.course_cohorts import ordered_course_cohorts
from studio.decorators import staff_required
from studio.utils import studio_pagination_context

MSG_DRAFT = 'Announcement saved as a draft.'
MSG_PUBLISHED = (
    'Announcement published. Emails are being sent to enrolled learners '
    'of the cohort.'
)
MSG_COHORT_REQUIRED = 'Choose the cohort this announcement is for.'
MSG_UPDATED = 'Announcement updated.'


def _course(course_id):
    return get_object_or_404(Course, pk=course_id)


def _announcement(course, announcement_id):
    return get_object_or_404(
        CourseAnnouncement, pk=announcement_id, course=course,
    )


def _wants_publish(request):
    if request.POST.get('publish'):
        return True
    return request.GET.get('publish') == '1'


def _default_cohort_id(cohorts):
    """Preselect the first current-or-upcoming dated cohort, if any."""
    if cohorts and cohorts[0].mode == 'cohort':
        return str(cohorts[0].pk)
    return ''


def _form_values(request, announcement=None, cohorts=()):
    if request.method == 'POST':
        cohort_id = (request.POST.get('cohort_id') or '').strip()
        if announcement is not None and (
            announcement.status == CourseAnnouncement.STATUS_PUBLISHED
        ):
            # The select is disabled once published; keep the stored cohort.
            cohort_id = str(announcement.cohort_id or '')
        return {
            'title': (request.POST.get('title') or '').strip(),
            'body': request.POST.get('body') or '',
            'cohort_id': cohort_id,
        }
    if announcement is None:
        return {
            'title': '',
            'body': '',
            'cohort_id': _default_cohort_id(cohorts),
        }
    return {
        'title': announcement.title,
        'body': announcement.body,
        'cohort_id': str(announcement.cohort_id or ''),
    }


def _resolve_cohort(course, raw_cohort_id):
    """Return ``course``'s cohort for a posted ``cohort_id``, or None."""
    try:
        cohort_id = int(raw_cohort_id)
    except (TypeError, ValueError):
        return None
    return Cohort.objects.filter(pk=cohort_id, course=course).first()


def _validate(values, course):
    errors = {}
    if not values['title']:
        errors['title'] = 'Title is required.'
    elif len(values['title']) > 200:
        errors['title'] = 'Title must be 200 characters or fewer.'
    cohort = _resolve_cohort(course, values['cohort_id'])
    if cohort is None:
        errors['cohort'] = MSG_COHORT_REQUIRED
    return errors, cohort


def _form_context(course, announcement, values, errors, cohorts, *, is_create):
    return {
        'course': course,
        'announcement': announcement,
        'values': values,
        'errors': errors,
        'cohorts': cohorts,
        'cohort_locked': (
            announcement is not None
            and announcement.status == CourseAnnouncement.STATUS_PUBLISHED
        ),
        'publish_confirm': publish_confirm_message(
            announcement.cohort if announcement is not None else None,
        ),
        'is_create': is_create,
    }


@staff_required
def announcement_list(request, course_id):
    course = _course(course_id)
    status = (request.GET.get('status') or '').strip()
    rows = CourseAnnouncement.objects.filter(course=course).select_related(
        'author', 'cohort',
    ).order_by('-created_at')
    if status in {
        CourseAnnouncement.STATUS_DRAFT,
        CourseAnnouncement.STATUS_PUBLISHED,
    }:
        rows = rows.filter(status=status)
    pager = studio_pagination_context(request, rows)
    for announcement in pager['page'].object_list:
        if announcement.status == CourseAnnouncement.STATUS_DRAFT:
            announcement.publish_confirm = publish_confirm_message(
                announcement.cohort,
            )
    return render(request, 'studio/courses/announcements_list.html', {
        'course': course,
        'status': status,
        'page': pager['page'],
        **pager,
    })


@staff_required
def announcement_create(request, course_id):
    course = _course(course_id)
    cohorts = ordered_course_cohorts(course)
    values = _form_values(request, cohorts=cohorts)
    errors = {}
    if request.method == 'POST':
        errors, cohort = _validate(values, course)
        if not errors:
            announcement = CourseAnnouncement(course=course)
            publish = _wants_publish(request)
            queued = save_announcement(
                announcement,
                title=values['title'],
                body=values['body'],
                status=(
                    CourseAnnouncement.STATUS_PUBLISHED
                    if publish
                    else CourseAnnouncement.STATUS_DRAFT
                ),
                author=request.user,
                cohort=cohort,
            )
            if queued:
                messages.success(request, MSG_PUBLISHED)
            else:
                messages.success(request, MSG_DRAFT)
            return redirect(
                'studio_course_announcement_list', course_id=course.pk,
            )
    return render(
        request, 'studio/courses/announcement_form.html',
        _form_context(course, None, values, errors, cohorts, is_create=True),
    )


@staff_required
def announcement_edit(request, course_id, announcement_id):
    course = _course(course_id)
    announcement = _announcement(course, announcement_id)
    cohorts = ordered_course_cohorts(course)
    values = _form_values(request, announcement, cohorts)
    errors = {}
    if request.method == 'POST':
        if request.POST.get('unpublish'):
            announcement.status = CourseAnnouncement.STATUS_DRAFT
            announcement.save(update_fields=['status', 'updated_at'])
            messages.success(request, MSG_UPDATED)
            return redirect(
                'studio_course_announcement_list', course_id=course.pk,
            )
        errors, cohort = _validate(values, course)
        if not errors:
            queued = save_announcement(
                announcement,
                title=values['title'],
                body=values['body'],
                status=announcement.status,
                author=request.user,
                cohort=cohort,
            )
            if queued:
                messages.success(request, MSG_PUBLISHED)
            else:
                messages.success(request, MSG_UPDATED)
            return redirect(
                'studio_course_announcement_list', course_id=course.pk,
            )
    return render(
        request, 'studio/courses/announcement_form.html',
        _form_context(
            course, announcement, values, errors, cohorts, is_create=False,
        ),
    )


@staff_required
@require_POST
def announcement_publish(request, course_id, announcement_id):
    course = _course(course_id)
    announcement = _announcement(course, announcement_id)
    title = (request.POST.get('title') or announcement.title).strip()
    body = request.POST.get('body')
    if body is None:
        body = announcement.body
    if not title:
        messages.error(request, 'Title is required.')
        return redirect(
            'studio_course_announcement_edit',
            course_id=course.pk,
            announcement_id=announcement.pk,
        )
    if len(title) > 200:
        messages.error(request, 'Title must be 200 characters or fewer.')
        return redirect(
            'studio_course_announcement_edit',
            course_id=course.pk,
            announcement_id=announcement.pk,
        )
    cohort = announcement.cohort
    raw_cohort_id = request.POST.get('cohort_id')
    if raw_cohort_id and announcement.status == CourseAnnouncement.STATUS_DRAFT:
        cohort = _resolve_cohort(course, raw_cohort_id)
    if cohort is None:
        messages.error(request, MSG_COHORT_REQUIRED)
        return redirect(
            'studio_course_announcement_edit',
            course_id=course.pk,
            announcement_id=announcement.pk,
        )
    previous_status = announcement.status
    announcement.title = title
    announcement.body = body
    announcement.cohort = cohort
    queued = apply_publish_transition(
        announcement, previous_status=previous_status,
    )
    if queued:
        messages.success(request, MSG_PUBLISHED)
    else:
        messages.success(request, MSG_UPDATED)
    return redirect('studio_course_announcement_list', course_id=course.pk)
