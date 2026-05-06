"""Studio views for managing personal sprint plans (issue #432).

This issue intentionally does NOT scaffold separate CRUD pages for the
``Week``, ``Checkpoint``, ``Resource``, ``Deliverable``, or ``NextStep``
child rows. Those rows are managed by the drag-and-drop editor in #434.
Plan detail in this issue renders them read-only.

Interview-note visibility is enforced at the queryset layer
(:meth:`plans.models.InterviewNoteQuerySet.visible_to`). The plan detail
page splits the page into an "Internal notes (staff only)" section and an
"External notes (shareable with member)" section, each scoped to that
plan, so a staff member glancing at the page understands the visibility
before reading.
"""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from plans.models import (
    KIND_CHOICES,
    PLAN_STATUS_CHOICES,
    VISIBILITY_CHOICES,
    InterviewNote,
    Plan,
    Sprint,
)
from studio.decorators import staff_required

User = get_user_model()


def _normalize_plan_status(raw):
    valid = {choice[0] for choice in PLAN_STATUS_CHOICES}
    if raw in valid:
        return raw
    return 'draft'


def _normalize_visibility(raw):
    valid = {choice[0] for choice in VISIBILITY_CHOICES}
    if raw in valid:
        return raw
    return 'internal'


def _normalize_kind(raw):
    valid = {choice[0] for choice in KIND_CHOICES}
    if raw in valid:
        return raw
    return 'general'


@staff_required
def plan_list(request):
    """Table of plans. Filters: ?sprint=, ?member=, ?status=, ?q=."""
    sprint_filter = (request.GET.get('sprint') or '').strip()
    member_filter = (request.GET.get('member') or '').strip()
    status_filter = (request.GET.get('status') or '').strip()
    search = (request.GET.get('q') or '').strip()

    plans = Plan.objects.select_related('member', 'sprint').order_by('-created_at')

    sprint_filter_id = None
    if sprint_filter.isdigit():
        sprint_filter_id = int(sprint_filter)
        plans = plans.filter(sprint_id=sprint_filter_id)

    member_filter_id = None
    if member_filter.isdigit():
        member_filter_id = int(member_filter)
        plans = plans.filter(member_id=member_filter_id)

    if status_filter:
        plans = plans.filter(status=status_filter)

    if search:
        plans = plans.filter(
            Q(member__email__icontains=search)
            | Q(member__first_name__icontains=search)
            | Q(member__last_name__icontains=search)
        )

    return render(request, 'studio/plans/list.html', {
        'plans': plans,
        'sprints': Sprint.objects.order_by('-start_date'),
        'sprint_filter_id': sprint_filter_id,
        'member_filter_id': member_filter_id,
        'status_filter': status_filter,
        'search': search,
        'plan_status_choices': PLAN_STATUS_CHOICES,
    })


@staff_required
def plan_create(request):
    """Form: pick member, pick sprint. ``status`` defaults to draft."""
    sprints = Sprint.objects.order_by('-start_date')
    members = User.objects.order_by('email')

    if request.method != 'POST':
        return render(request, 'studio/plans/form.html', {
            'plan': None,
            'form_action': 'create',
            'form_data': {
                'member': '',
                'sprint': '',
                'status': 'draft',
            },
            'sprints': sprints,
            'members': members,
            'plan_status_choices': PLAN_STATUS_CHOICES,
            'error': '',
        })

    form_data = {
        'member': (request.POST.get('member') or '').strip(),
        'sprint': (request.POST.get('sprint') or '').strip(),
        'status': (request.POST.get('status') or '').strip(),
    }

    def _render_with_error(error, status=400):
        return render(request, 'studio/plans/form.html', {
            'plan': None,
            'form_action': 'create',
            'form_data': form_data,
            'sprints': sprints,
            'members': members,
            'plan_status_choices': PLAN_STATUS_CHOICES,
            'error': error,
        }, status=status)

    if not form_data['member'].isdigit():
        return _render_with_error('Pick a member.')
    if not form_data['sprint'].isdigit():
        return _render_with_error('Pick a sprint.')

    member = User.objects.filter(pk=int(form_data['member'])).first()
    sprint = Sprint.objects.filter(pk=int(form_data['sprint'])).first()
    if member is None:
        return _render_with_error('Selected member does not exist.')
    if sprint is None:
        return _render_with_error('Selected sprint does not exist.')

    status_value = _normalize_plan_status(form_data['status'])

    if Plan.objects.filter(member=member, sprint=sprint).exists():
        return _render_with_error(
            f'A plan already exists for {member.email} in sprint "{sprint.name}".',
        )

    try:
        with transaction.atomic():
            plan = Plan.objects.create(
                member=member,
                sprint=sprint,
                status=status_value,
            )
    except IntegrityError:
        # Defence against a race between the .exists() check and the create.
        return _render_with_error(
            f'A plan already exists for {member.email} in sprint "{sprint.name}".',
        )

    messages.success(request, f'Plan created for {member.email} in "{sprint.name}".')
    return redirect('studio_plan_detail', plan_id=plan.pk)


@staff_required
def plan_detail(request, plan_id):
    """Read-mostly detail with internal/external notes split.

    Both note sections are scoped to the plan. The "Internal" heading
    contains the words "staff only" so the visibility is unmistakable.
    """
    plan = get_object_or_404(
        Plan.objects.select_related('member', 'sprint'),
        pk=plan_id,
    )
    weeks = (
        plan.weeks
        .prefetch_related('checkpoints')
        .order_by('position', 'week_number')
    )
    resources = plan.resources.order_by('position', 'id')
    deliverables = plan.deliverables.order_by('position', 'id')
    next_steps = plan.next_steps.order_by('position', 'id')

    # Visibility-aware queryset, scoped to this plan. Staff see both
    # blocks, but the template renders them as separate sections so
    # operators can tell at a glance which notes are shareable.
    internal_notes = (
        InterviewNote.objects
        .internal()
        .filter(plan=plan)
        .order_by('-created_at')
    )
    external_notes = (
        InterviewNote.objects
        .external()
        .filter(plan=plan)
        .order_by('-created_at')
    )

    return render(request, 'studio/plans/detail.html', {
        'plan': plan,
        'weeks': weeks,
        'resources': resources,
        'deliverables': deliverables,
        'next_steps': next_steps,
        'internal_notes': internal_notes,
        'external_notes': external_notes,
    })


@staff_required
def plan_edit(request, plan_id):
    """Edit form for the Summary fields, focus, accountability, status,
    persona, and the ``shared_at`` toggle.
    """
    plan = get_object_or_404(Plan, pk=plan_id)

    if request.method != 'POST':
        return render(request, 'studio/plans/edit.html', {
            'plan': plan,
            'plan_status_choices': PLAN_STATUS_CHOICES,
            'form_data': {
                'summary_current_situation': plan.summary_current_situation,
                'summary_goal': plan.summary_goal,
                'summary_main_gap': plan.summary_main_gap,
                'summary_weekly_hours': plan.summary_weekly_hours,
                'summary_why_this_plan': plan.summary_why_this_plan,
                'focus_main': plan.focus_main,
                'focus_supporting': '\n'.join(plan.focus_supporting or []),
                'accountability': plan.accountability,
                'assigned_persona': plan.assigned_persona,
                'status': plan.status,
                'shared': bool(plan.shared_at),
            },
            'error': '',
        })

    plan.summary_current_situation = request.POST.get('summary_current_situation', '').strip()
    plan.summary_goal = request.POST.get('summary_goal', '').strip()
    plan.summary_main_gap = request.POST.get('summary_main_gap', '').strip()
    plan.summary_weekly_hours = request.POST.get('summary_weekly_hours', '').strip()
    plan.summary_why_this_plan = request.POST.get('summary_why_this_plan', '').strip()
    plan.focus_main = request.POST.get('focus_main', '').strip()
    raw_supporting = request.POST.get('focus_supporting', '')
    plan.focus_supporting = [
        line.strip() for line in raw_supporting.splitlines() if line.strip()
    ]
    plan.accountability = request.POST.get('accountability', '').strip()
    plan.assigned_persona = request.POST.get('assigned_persona', '').strip()
    plan.status = _normalize_plan_status(request.POST.get('status', ''))

    # ``shared_at`` is a real timestamp distinct from the ``shared``
    # status. Only flip it when the operator toggles the checkbox.
    is_shared = request.POST.get('shared') == 'on'
    if is_shared and plan.shared_at is None:
        plan.shared_at = timezone.now()
    elif not is_shared and plan.shared_at is not None:
        plan.shared_at = None

    plan.save()
    messages.success(request, 'Plan updated.')
    return redirect('studio_plan_detail', plan_id=plan.pk)


@staff_required
def interview_note_create(request, plan_id):
    """Form: pick kind, visibility, write body. Default visibility=internal."""
    plan = get_object_or_404(Plan, pk=plan_id)

    if request.method != 'POST':
        return render(request, 'studio/plans/note_form.html', {
            'plan': plan,
            'note': None,
            'form_action': 'create',
            'form_data': {
                'kind': 'general',
                # Defaults to internal -- safer fallback for staff capture.
                'visibility': 'internal',
                'body': '',
            },
            'kind_choices': KIND_CHOICES,
            'visibility_choices': VISIBILITY_CHOICES,
            'error': '',
        })

    form_data = {
        'kind': _normalize_kind(request.POST.get('kind', '')),
        'visibility': _normalize_visibility(request.POST.get('visibility', '')),
        'body': (request.POST.get('body') or '').strip(),
    }

    if not form_data['body']:
        return render(request, 'studio/plans/note_form.html', {
            'plan': plan,
            'note': None,
            'form_action': 'create',
            'form_data': form_data,
            'kind_choices': KIND_CHOICES,
            'visibility_choices': VISIBILITY_CHOICES,
            'error': 'Note body is required.',
        }, status=400)

    InterviewNote.objects.create(
        plan=plan,
        member=plan.member,
        kind=form_data['kind'],
        visibility=form_data['visibility'],
        body=form_data['body'],
        created_by=request.user if request.user.is_authenticated else None,
    )
    messages.success(request, 'Interview note added.')
    return redirect('studio_plan_detail', plan_id=plan.pk)


@staff_required
def interview_note_edit(request, plan_id, note_id):
    """Edit existing note."""
    plan = get_object_or_404(Plan, pk=plan_id)
    note = get_object_or_404(InterviewNote, pk=note_id, plan=plan)

    if request.method != 'POST':
        return render(request, 'studio/plans/note_form.html', {
            'plan': plan,
            'note': note,
            'form_action': 'edit',
            'form_data': {
                'kind': note.kind,
                'visibility': note.visibility,
                'body': note.body,
            },
            'kind_choices': KIND_CHOICES,
            'visibility_choices': VISIBILITY_CHOICES,
            'error': '',
        })

    form_data = {
        'kind': _normalize_kind(request.POST.get('kind', '')),
        'visibility': _normalize_visibility(request.POST.get('visibility', '')),
        'body': (request.POST.get('body') or '').strip(),
    }

    if not form_data['body']:
        return render(request, 'studio/plans/note_form.html', {
            'plan': plan,
            'note': note,
            'form_action': 'edit',
            'form_data': form_data,
            'kind_choices': KIND_CHOICES,
            'visibility_choices': VISIBILITY_CHOICES,
            'error': 'Note body is required.',
        }, status=400)

    note.kind = form_data['kind']
    note.visibility = form_data['visibility']
    note.body = form_data['body']
    note.save()
    messages.success(request, 'Interview note updated.')
    return redirect('studio_plan_detail', plan_id=plan.pk)


@staff_required
@require_POST
def interview_note_delete(request, plan_id, note_id):
    """POST-only delete with confirmation (confirmation handled in template)."""
    plan = get_object_or_404(Plan, pk=plan_id)
    note = get_object_or_404(InterviewNote, pk=note_id, plan=plan)
    note.delete()
    messages.success(request, 'Interview note deleted.')
    return redirect('studio_plan_detail', plan_id=plan.pk)
