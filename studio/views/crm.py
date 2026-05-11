"""Studio CRM views (issue #560).

The CRM surface is the canonical, staff-only place where relationship
context lives: plans, member notes, experiments, persona, summary, next
steps. Member-facing surfaces must NOT render any field on
:class:`CRMRecord` or any :class:`CRMExperiment`.

The CRM is opt-in: a user is in the CRM iff a :class:`CRMRecord` row
exists for them. Staff create the row via the ``Track in CRM`` button on
``/studio/users/<id>/``.
"""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.models import TierOverride
from crm.models import (
    EXPERIMENT_STATUS_CHOICES,
    STATUS_CHOICES,
    CRMExperiment,
    CRMRecord,
)
from plans.models import InterviewNote, Plan
from studio.decorators import staff_required

User = get_user_model()

CRM_LIST_PAGE_SIZE = 50
FILTER_ALL = 'all'
FILTER_ACTIVE = 'active'
FILTER_ARCHIVED = 'archived'
VALID_LIST_FILTERS = {FILTER_ALL, FILTER_ACTIVE, FILTER_ARCHIVED}
DEFAULT_LIST_FILTER = FILTER_ACTIVE


def _normalize_list_filter(raw):
    if raw in VALID_LIST_FILTERS:
        return raw
    return DEFAULT_LIST_FILTER


def _active_tier_name(user):
    """Return the user's effective tier label, including any override.

    Mirrors the helper in ``studio.views.users`` so the CRM list shows
    the same tier label as the user list (override-aware).
    """
    from django.utils import timezone

    base_name = user.tier.name if user.tier_id else 'Free'
    override = (
        TierOverride.objects
        .filter(
            user_id=user.pk,
            is_active=True,
            expires_at__gt=timezone.now(),
        )
        .select_related('override_tier')
        .order_by('-created_at')
        .first()
    )
    if override is None:
        return base_name
    base_level = user.tier.level if user.tier_id else 0
    if override.override_tier.level <= base_level:
        return base_name
    return f'{override.override_tier.name} (override)'


@staff_required
@require_POST
def crm_track(request, user_id):
    """Create-or-redirect-to a CRM record for the given user (idempotent).

    POST-only. If a record already exists for ``user_id`` the response
    redirects to that record without creating a duplicate. Otherwise a
    new record is created with ``created_by=request.user`` and the
    response redirects to the new record.
    """
    user = get_object_or_404(User, pk=user_id)
    existing = CRMRecord.objects.filter(user=user).first()
    if existing is not None:
        return redirect('studio_crm_detail', crm_id=existing.pk)
    record = CRMRecord.objects.create(user=user, created_by=request.user)
    messages.success(request, 'Now tracking this user in the CRM.')
    return redirect('studio_crm_detail', crm_id=record.pk)


@staff_required
def crm_list(request):
    """List all CRM records.

    Filter chips: ``all`` / ``active`` (default) / ``archived``.
    Search: case-insensitive substring on ``user.email`` and ``persona``.
    Pagination: 50 per page (mirrors the user list).
    """
    active_filter = _normalize_list_filter(request.GET.get('filter', ''))
    search = (request.GET.get('q', '') or '').strip()

    records_qs = (
        CRMRecord.objects
        .select_related('user', 'user__tier')
        .annotate(
            plans_count=Count('user__plans', distinct=True),
            notes_count=Count('user__interview_notes', distinct=True),
        )
        .order_by('-updated_at')
    )

    if active_filter == FILTER_ACTIVE:
        records_qs = records_qs.filter(status='active')
    elif active_filter == FILTER_ARCHIVED:
        records_qs = records_qs.filter(status='archived')

    if search:
        records_qs = records_qs.filter(
            Q(user__email__icontains=search) | Q(persona__icontains=search)
        )

    paginator = Paginator(records_qs, CRM_LIST_PAGE_SIZE)
    try:
        page_number = int(request.GET.get('page', '1'))
    except (TypeError, ValueError):
        page_number = 1
    if page_number < 1:
        page_number = 1
    if page_number > paginator.num_pages:
        page_number = paginator.num_pages
    page = paginator.page(page_number)

    rows = []
    for record in page.object_list:
        rows.append({
            'pk': record.pk,
            'user_pk': record.user.pk,
            'email': record.user.email,
            'full_name': f'{record.user.first_name} {record.user.last_name}'.strip(),
            'tier_name': _active_tier_name(record.user),
            'persona': record.persona,
            'status': record.status,
            'status_display': record.get_status_display(),
            'updated_at': record.updated_at,
            'plans_count': record.plans_count,
            'notes_count': record.notes_count,
        })

    counts = {
        'all': CRMRecord.objects.count(),
        'active': CRMRecord.objects.filter(status='active').count(),
        'archived': CRMRecord.objects.filter(status='archived').count(),
    }

    return render(request, 'studio/crm/list.html', {
        'rows': rows,
        'paginator': paginator,
        'page': page,
        'active_filter': active_filter,
        'search': search,
        'counts': counts,
        'filter_all': FILTER_ALL,
        'filter_active': FILTER_ACTIVE,
        'filter_archived': FILTER_ARCHIVED,
    })


def _get_record(crm_id):
    return get_object_or_404(
        CRMRecord.objects.select_related('user', 'user__tier'),
        pk=crm_id,
    )


def _record_detail_context(record):
    """Build the shared context for the CRM detail page."""
    note_queryset = (
        InterviewNote.objects
        .filter(member=record.user)
        .select_related('plan__sprint', 'created_by')
        .order_by('-created_at')
    )
    member_plans = list(
        Plan.objects
        .filter(member=record.user)
        .select_related('sprint')
        .order_by('-sprint__start_date', '-created_at')
    )
    experiments = list(record.experiments.all().order_by('-created_at'))

    return {
        'record': record,
        'detail_user': record.user,
        'tier_name': _active_tier_name(record.user),
        'member_plans': member_plans,
        'internal_notes': note_queryset.internal(),
        'external_notes': note_queryset.external(),
        # ``current_plan`` is consumed by the reused
        # ``_member_notes.html`` partial when the staff "Add member
        # note" button needs a plan prefill. The CRM record is
        # member-scoped, not plan-scoped, so we pass ``None``.
        'current_plan': None,
        'experiments': experiments,
        'django_admin_url': (
            f'/admin/accounts/user/{record.user.pk}/change/'
        ),
        'experiment_status_choices': EXPERIMENT_STATUS_CHOICES,
        'record_status_choices': STATUS_CHOICES,
    }


@staff_required
def crm_detail(request, crm_id):
    """Render the CRM record detail page (header + 5 sections)."""
    record = _get_record(crm_id)
    return render(
        request,
        'studio/crm/detail.html',
        _record_detail_context(record),
    )


@staff_required
@require_POST
def crm_edit(request, crm_id):
    """Save the snapshot card fields (persona, summary, next_steps)."""
    record = _get_record(crm_id)
    record.persona = (request.POST.get('persona') or '').strip()[:120]
    record.summary = (request.POST.get('summary') or '').strip()
    record.next_steps = (request.POST.get('next_steps') or '').strip()
    record.save()
    messages.success(request, 'CRM record updated.')
    return redirect('studio_crm_detail', crm_id=record.pk)


@staff_required
@require_POST
def crm_archive(request, crm_id):
    """Set the record's status to ``archived``."""
    record = _get_record(crm_id)
    record.status = 'archived'
    record.save(update_fields=['status', 'updated_at'])
    messages.info(request, 'CRM record archived.')
    return redirect('studio_crm_detail', crm_id=record.pk)


@staff_required
@require_POST
def crm_reactivate(request, crm_id):
    """Set the record's status back to ``active``."""
    record = _get_record(crm_id)
    record.status = 'active'
    record.save(update_fields=['status', 'updated_at'])
    messages.success(request, 'CRM record reactivated.')
    return redirect('studio_crm_detail', crm_id=record.pk)


# ---------------------------------------------------------------------------
# Experiments CRUD
# ---------------------------------------------------------------------------


def _normalize_experiment_status(raw, default='running'):
    valid = {choice[0] for choice in EXPERIMENT_STATUS_CHOICES}
    if raw in valid:
        return raw
    return default


@staff_required
@require_POST
def crm_experiment_create(request, crm_id):
    """Create an experiment on the CRM record."""
    record = _get_record(crm_id)
    title = (request.POST.get('title') or '').strip()[:200]
    if not title:
        messages.error(request, 'Experiment title is required.')
        return redirect('studio_crm_detail', crm_id=record.pk)

    CRMExperiment.objects.create(
        crm_record=record,
        title=title,
        hypothesis=(request.POST.get('hypothesis') or '').strip(),
        result=(request.POST.get('result') or '').strip(),
        status=_normalize_experiment_status(
            request.POST.get('status', ''),
        ),
    )
    messages.success(request, 'Experiment added.')
    return redirect('studio_crm_detail', crm_id=record.pk)


@staff_required
def crm_experiment_edit(request, crm_id, exp_id):
    """Edit an experiment. GET renders the form, POST saves."""
    record = _get_record(crm_id)
    experiment = get_object_or_404(
        CRMExperiment, pk=exp_id, crm_record=record,
    )
    if request.method != 'POST':
        return render(request, 'studio/crm/experiment_form.html', {
            'record': record,
            'experiment': experiment,
            'form_action': reverse(
                'studio_crm_experiment_edit',
                kwargs={'crm_id': record.pk, 'exp_id': experiment.pk},
            ),
            'experiment_status_choices': EXPERIMENT_STATUS_CHOICES,
        })

    title = (request.POST.get('title') or '').strip()[:200]
    if not title:
        messages.error(request, 'Experiment title is required.')
        return redirect(
            'studio_crm_experiment_edit',
            crm_id=record.pk, exp_id=experiment.pk,
        )

    experiment.title = title
    experiment.hypothesis = (request.POST.get('hypothesis') or '').strip()
    experiment.result = (request.POST.get('result') or '').strip()
    experiment.status = _normalize_experiment_status(
        request.POST.get('status', ''),
        default=experiment.status,
    )
    experiment.save()
    messages.success(request, 'Experiment updated.')
    return redirect('studio_crm_detail', crm_id=record.pk)


@staff_required
@require_POST
def crm_experiment_delete(request, crm_id, exp_id):
    """Delete an experiment."""
    record = _get_record(crm_id)
    experiment = get_object_or_404(
        CRMExperiment, pk=exp_id, crm_record=record,
    )
    experiment.delete()
    messages.success(request, 'Experiment deleted.')
    return redirect('studio_crm_detail', crm_id=record.pk)
