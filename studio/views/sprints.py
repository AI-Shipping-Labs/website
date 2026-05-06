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

from datetime import datetime

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify

from content.access import LEVEL_PREMIUM
from content.access import VISIBILITY_CHOICES as TIER_LEVEL_CHOICES
from plans.models import PLAN_STATUS_CHOICES, SPRINT_STATUS_CHOICES, Plan, Sprint
from plans.services import create_plan_for_enrollment
from studio.decorators import staff_required

User = get_user_model()

# The set of tier levels accepted by the form. Mirror the values in
# ``content.access.VISIBILITY_CHOICES`` so the dropdown stays consistent
# with the rest of the gating surface.
_VALID_TIER_LEVELS = {value for value, _label in TIER_LEVEL_CHOICES}


def _parse_min_tier_level(raw):
    """Parse the ``min_tier_level`` form field. ``(value, error)``."""
    if raw in (None, ''):
        return LEVEL_PREMIUM, ''
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, 'Min tier level must be a whole number.'
    if value not in _VALID_TIER_LEVELS:
        return None, 'Min tier level must be one of 0, 10, 20, 30.'
    return value, ''


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
        'min_tier_level': (request.POST.get('min_tier_level') or '').strip(),
    }


def _form_data_from_sprint(sprint):
    return {
        'name': sprint.name,
        'slug': sprint.slug,
        'start_date': sprint.start_date.isoformat() if sprint.start_date else '',
        'duration_weeks': str(sprint.duration_weeks),
        'status': sprint.status,
        'min_tier_level': str(sprint.min_tier_level),
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
                'min_tier_level': str(LEVEL_PREMIUM),
            },
        )

    form_data = _form_data_from_post(request)

    name = form_data['name']
    raw_slug = form_data['slug']
    start_date, date_error = _parse_start_date(form_data['start_date'])
    duration, duration_error = _parse_duration_weeks(form_data['duration_weeks'])
    status_value = _normalize_status(form_data['status'])
    min_tier_level, tier_error = _parse_min_tier_level(form_data['min_tier_level'])

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
    enrollment_count = sprint.enrollments.count()
    return render(request, 'studio/sprints/detail.html', {
        'sprint': sprint,
        'plans': plans,
        'enrollment_count': enrollment_count,
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
    min_tier_level, tier_error = _parse_min_tier_level(form_data['min_tier_level'])

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
    sprint.save()

    messages.success(request, f'Sprint "{sprint.name}" updated.')
    return redirect('studio_sprint_detail', sprint_id=sprint.pk)


@staff_required
def sprint_add_member(request, sprint_id):
    """Form: pick a member and one-click enroll + create their plan.

    Issue #444. The sprint is locked from the URL; the member picker
    is the same single-select widget the standalone create-plan form
    uses (``templates/studio/plans/form.html``). On a valid POST we
    delegate to :func:`plans.services.create_plan_for_enrollment`,
    which is shared with ``studio_plan_create`` so the empty-plan
    artefact (one Week per ``sprint.duration_weeks``, theme blank,
    zero checkpoints) stays consistent across surfaces.

    Idempotent. Re-submitting the same ``(sprint, user)`` pair never
    duplicates rows: we redirect back to the existing plan editor with
    a ``messages.info`` flash containing ``Already enrolled``.
    """
    sprint = get_object_or_404(Sprint, pk=sprint_id)
    members = User.objects.order_by('email')

    if request.method != 'POST':
        return render(request, 'studio/plans/form.html', {
            'plan': None,
            'form_action': 'add_member',
            'form_action_url': request.path,
            'form_data': {
                'member': '',
                'sprint': str(sprint.pk),
                'status': 'draft',
            },
            'sprint': sprint,
            'members': members,
            'plan_status_choices': PLAN_STATUS_CHOICES,
            'error': '',
        })

    raw_member = (request.POST.get('member') or '').strip()
    form_data = {
        'member': raw_member,
        'sprint': str(sprint.pk),
        'status': 'draft',
    }

    def _render_with_error(error, status=400):
        return render(request, 'studio/plans/form.html', {
            'plan': None,
            'form_action': 'add_member',
            'form_action_url': request.path,
            'form_data': form_data,
            'sprint': sprint,
            'members': members,
            'plan_status_choices': PLAN_STATUS_CHOICES,
            'error': error,
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
        messages.success(
            request,
            f'Plan created for {member.email} in "{sprint.name}".',
        )
    else:
        messages.info(
            request,
            f'Already enrolled — opening existing plan for {member.email}.',
        )

    return redirect('studio_plan_edit', plan_id=plan.pk)
