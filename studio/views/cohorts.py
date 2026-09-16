"""Studio views for course cohort management (issue #1660).

``Cohort`` was Django-admin-inline only. This gives operators a dedicated
list + edit surface under the course sub-resource URL family, mirroring
the course-scoped nesting ``studio/views/enrollments.py`` already
established (``studio/courses/<course_id>/cohorts/...``).

Cohort creation happens from an inline form on the list page (same shape
as the enrollments list's "Enroll a user" form) so operators don't need a
separate create page; every field including the new ``event_series`` link
is editable both at creation time and later from the edit page.
"""

import datetime

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from content.models import Cohort, Course
from events.models import EventSeries
from studio.decorators import staff_required
from studio.utils import studio_pagination_context


def _parse_date(raw_value):
    raw_value = (raw_value or '').strip()
    if not raw_value:
        return None
    try:
        return datetime.date.fromisoformat(raw_value)
    except ValueError:
        return None


def _resolve_event_series(raw_id):
    raw_id = (raw_id or '').strip()
    if not raw_id:
        return None
    try:
        series_id = int(raw_id)
    except ValueError:
        return None
    return EventSeries.objects.filter(pk=series_id).first()


@staff_required
def cohort_list(request, course_id):
    """List cohorts for a single course (Studio List Page Baseline)."""
    course = get_object_or_404(Course, pk=course_id)
    search = (request.GET.get('q') or '').strip()

    cohorts = course.cohorts.select_related('event_series').order_by('-start_date')
    if search:
        cohorts = cohorts.filter(name__icontains=search)

    pager = studio_pagination_context(request, cohorts)
    return render(request, 'studio/courses/cohorts_list.html', {
        'course': course,
        'cohorts': pager['page'].object_list,
        'search': search,
        'event_series_options': EventSeries.objects.order_by('name'),
        'cohorts_subtitle': (
            f'Manage time-bound cohorts for "{course.title}", including '
            'the office-hours series a cohort links to.'
        ),
        **pager,
    })


@staff_required
@require_POST
def cohort_create(request, course_id):
    """Create a cohort for this course, including an optional series link.

    Issue #1674: a ``mode`` selector. ``mode='self_paced'`` hides/ignores
    the date inputs and event series/max participants — ``Cohort.clean()``
    (run via ``full_clean()``) is the source of truth for the invariant;
    a mismatched submission surfaces as a Studio message, not a 500 or a
    silently-ignored field.
    """
    course = get_object_or_404(Course, pk=course_id)
    name = request.POST.get('name', '').strip()
    mode = request.POST.get('mode', 'cohort').strip()
    if mode == 'self_paced':
        cohort = Cohort(course=course, name=name, mode='self_paced')
    else:
        start_date = _parse_date(request.POST.get('start_date', ''))
        end_date = _parse_date(request.POST.get('end_date', ''))
        cohort = Cohort(
            course=course,
            name=name,
            mode='cohort',
            start_date=start_date,
            end_date=end_date,
            event_series=_resolve_event_series(
                request.POST.get('event_series_id', ''),
            ),
        )

    if not name:
        messages.error(request, 'Name is required.')
        return redirect('studio_course_cohort_list', course_id=course.pk)

    try:
        cohort.full_clean()
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
        return redirect('studio_course_cohort_list', course_id=course.pk)

    cohort.save()
    messages.success(request, f'Created cohort "{name}".')
    return redirect('studio_course_cohort_list', course_id=course.pk)


@staff_required
def cohort_edit(request, course_id, cohort_id):
    """Edit an existing cohort, including setting/clearing the series link."""
    course = get_object_or_404(Course, pk=course_id)
    cohort = get_object_or_404(Cohort, pk=cohort_id, course=course)

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        mode = request.POST.get('mode', 'cohort').strip()
        if not name:
            messages.error(request, 'Name is required.')
            return redirect(
                'studio_course_cohort_edit',
                course_id=course.pk, cohort_id=cohort.pk,
            )

        cohort.name = name
        cohort.mode = mode
        cohort.is_active = request.POST.get('is_active') == 'on'

        if mode == 'self_paced':
            cohort.start_date = None
            cohort.end_date = None
            cohort.max_participants = None
            cohort.event_series = None
        else:
            cohort.start_date = _parse_date(request.POST.get('start_date', ''))
            cohort.end_date = _parse_date(request.POST.get('end_date', ''))
            max_participants_raw = request.POST.get('max_participants', '').strip()
            if max_participants_raw:
                try:
                    cohort.max_participants = int(max_participants_raw)
                except ValueError:
                    messages.error(request, 'Max participants must be a number.')
                    return redirect(
                        'studio_course_cohort_edit',
                        course_id=course.pk, cohort_id=cohort.pk,
                    )
            else:
                cohort.max_participants = None
            cohort.event_series = _resolve_event_series(
                request.POST.get('event_series_id', ''),
            )

        try:
            cohort.full_clean()
        except ValidationError as exc:
            messages.error(request, '; '.join(exc.messages))
            return redirect(
                'studio_course_cohort_edit',
                course_id=course.pk, cohort_id=cohort.pk,
            )

        cohort.save()
        messages.success(request, f'Updated "{cohort.name}".')
        return redirect(
            'studio_course_cohort_edit',
            course_id=course.pk, cohort_id=cohort.pk,
        )

    return render(request, 'studio/courses/cohort_form.html', {
        'course': course,
        'cohort': cohort,
        'event_series_options': EventSeries.objects.order_by('name'),
    })
