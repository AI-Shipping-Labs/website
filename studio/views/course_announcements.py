"""Studio authoring for course announcements (issue #1942)."""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from content.models import Course
from content.models.course_announcement import CourseAnnouncement
from content.services.course_announcements import (
    apply_publish_transition,
    publish_confirm_message,
    save_announcement,
)
from studio.decorators import staff_required
from studio.utils import studio_pagination_context

MSG_DRAFT = 'Announcement saved as a draft.'
MSG_PUBLISHED = 'Announcement published. Emails are being sent to enrolled learners.'
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


def _form_values(request, announcement=None):
    if request.method == 'POST':
        return {
            'title': (request.POST.get('title') or '').strip(),
            'body': request.POST.get('body') or '',
        }
    if announcement is None:
        return {'title': '', 'body': ''}
    return {'title': announcement.title, 'body': announcement.body}


def _validate(values):
    errors = {}
    if not values['title']:
        errors['title'] = 'Title is required.'
    elif len(values['title']) > 200:
        errors['title'] = 'Title must be 200 characters or fewer.'
    return errors


@staff_required
def announcement_list(request, course_id):
    course = _course(course_id)
    status = (request.GET.get('status') or '').strip()
    rows = CourseAnnouncement.objects.filter(course=course).select_related(
        'author',
    ).order_by('-created_at')
    if status in {
        CourseAnnouncement.STATUS_DRAFT,
        CourseAnnouncement.STATUS_PUBLISHED,
    }:
        rows = rows.filter(status=status)
    pager = studio_pagination_context(request, rows)
    return render(request, 'studio/courses/announcements_list.html', {
        'course': course,
        'status': status,
        'page': pager['page'],
        **pager,
        'publish_confirm': publish_confirm_message(course),
    })


@staff_required
def announcement_create(request, course_id):
    course = _course(course_id)
    values = _form_values(request)
    errors = {}
    if request.method == 'POST':
        errors = _validate(values)
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
            )
            if queued:
                messages.success(request, MSG_PUBLISHED)
            else:
                messages.success(request, MSG_DRAFT)
            return redirect(
                'studio_course_announcement_list', course_id=course.pk,
            )
    return render(request, 'studio/courses/announcement_form.html', {
        'course': course,
        'announcement': None,
        'values': values,
        'errors': errors,
        'publish_confirm': publish_confirm_message(course),
        'is_create': True,
    })


@staff_required
def announcement_edit(request, course_id, announcement_id):
    course = _course(course_id)
    announcement = _announcement(course, announcement_id)
    values = _form_values(request, announcement)
    errors = {}
    if request.method == 'POST':
        if request.POST.get('unpublish'):
            announcement.status = CourseAnnouncement.STATUS_DRAFT
            announcement.save(update_fields=['status', 'updated_at'])
            messages.success(request, MSG_UPDATED)
            return redirect(
                'studio_course_announcement_list', course_id=course.pk,
            )
        errors = _validate(values)
        if not errors:
            queued = save_announcement(
                announcement,
                title=values['title'],
                body=values['body'],
                status=announcement.status,
                author=request.user,
            )
            if queued:
                messages.success(request, MSG_PUBLISHED)
            else:
                messages.success(request, MSG_UPDATED)
            return redirect(
                'studio_course_announcement_list', course_id=course.pk,
            )
    return render(request, 'studio/courses/announcement_form.html', {
        'course': course,
        'announcement': announcement,
        'values': values,
        'errors': errors,
        'publish_confirm': publish_confirm_message(course),
        'is_create': False,
    })


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
    previous_status = announcement.status
    announcement.title = title
    announcement.body = body
    queued = apply_publish_transition(
        announcement, previous_status=previous_status,
    )
    if queued:
        messages.success(request, MSG_PUBLISHED)
    else:
        messages.success(request, MSG_UPDATED)
    return redirect('studio_course_announcement_list', course_id=course.pk)
