"""Studio views for managing sprints (issue #432).

A sprint is a rolling cohort window. Plans (one per member per sprint)
hang off a sprint. Weeks per plan are bounded by ``sprint.duration_weeks``
in practice but not enforced at the DB layer.

All views are staff-only. Anonymous users are redirected to the login
page; authenticated non-staff users get a 403. See
``studio/decorators.py``.
"""

from datetime import datetime

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify

from plans.models import SPRINT_STATUS_CHOICES, Plan, Sprint
from studio.decorators import staff_required


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
        'error': error,
    }
    return render(request, 'studio/sprints/form.html', context, status=status)


def _form_data_from_post(request):
    return {
        'name': (request.POST.get('name') or '').strip(),
        'slug': (request.POST.get('slug') or '').strip(),
        'start_date': (request.POST.get('start_date') or '').strip(),
        'duration_weeks': (request.POST.get('duration_weeks') or '').strip(),
        'status': (request.POST.get('status') or '').strip(),
    }


def _form_data_from_sprint(sprint):
    return {
        'name': sprint.name,
        'slug': sprint.slug,
        'start_date': sprint.start_date.isoformat() if sprint.start_date else '',
        'duration_weeks': str(sprint.duration_weeks),
        'status': sprint.status,
    }


@staff_required
def sprint_list(request):
    """Table of sprints with status badge, start date, duration, plan count."""
    sprints = (
        Sprint.objects
        .annotate(plan_count=Count('plans'))
        .order_by('-start_date')
    )
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
            },
        )

    form_data = _form_data_from_post(request)

    name = form_data['name']
    raw_slug = form_data['slug']
    start_date, date_error = _parse_start_date(form_data['start_date'])
    duration, duration_error = _parse_duration_weeks(form_data['duration_weeks'])
    status_value = _normalize_status(form_data['status'])

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
    )
    messages.success(request, f'Sprint "{sprint.name}" created.')
    return redirect('studio_sprint_detail', sprint_id=sprint.pk)


@staff_required
def sprint_detail(request, sprint_id):
    """Sprint metadata + list of plans in that sprint."""
    sprint = get_object_or_404(Sprint, pk=sprint_id)
    plans = (
        Plan.objects.filter(sprint=sprint)
        .select_related('member')
        .order_by('-created_at')
    )
    return render(request, 'studio/sprints/detail.html', {
        'sprint': sprint,
        'plans': plans,
    })


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
    sprint.save()

    messages.success(request, f'Sprint "{sprint.name}" updated.')
    return redirect('studio_sprint_detail', sprint_id=sprint.pk)
