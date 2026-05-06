"""Studio views for managing personal sprint plans (issues #432, #434).

The form-based CRUD pages from #432 (list / new / detail / note) live
unchanged here. ``plan_edit`` was replaced in #434 with a thin
client-side shell that renders the drag-and-drop authoring UI; all
writes from that page go through the JSON API in #433. The view is
intentionally GET-only -- there is no ``request.POST`` handling, no
``Save`` button, and no parallel reorder endpoint inside Studio.

Interview-note visibility is enforced at the queryset layer
(:meth:`plans.models.InterviewNoteQuerySet.visible_to`). The plan detail
page splits the page into an "Internal notes (staff only)" section and an
"External notes (shareable with member)" section, each scoped to that
plan, so a staff member glancing at the page understands the visibility
before reading.
"""

import json

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import Token
from plans.models import (
    KIND_CHOICES,
    PLAN_STATUS_CHOICES,
    VISIBILITY_CHOICES,
    InterviewNote,
    Plan,
    Sprint,
)
from studio.decorators import staff_required
from studio.services.plan_editor import serialize_plan_detail

User = get_user_model()

# Stable name attached to the API token issued to a staff user when they
# open the drag-and-drop plan editor. Re-using the same name across
# sessions means we get-or-create at most one token per staff user for
# this UI -- never accumulate one per page load.
EDITOR_TOKEN_NAME = 'studio-plan-editor'


def _get_or_create_editor_token(user):
    """Return a Token bound to ``user`` for the drag-drop editor.

    Tokens minted by the Studio token UI (#431) live under operator-
    chosen names. The editor mints its own token under a reserved name
    so revoking it from the API-tokens page does not also kill ad-hoc
    operator tokens. ``Token.save()`` populates ``key`` on first save
    when blank, so a single ``get_or_create`` is enough.
    """
    token, _ = Token.objects.get_or_create(
        user=user,
        name=EDITOR_TOKEN_NAME,
    )
    return token


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
    """Drag-and-drop plan editor (issue #434).

    Thin server-rendered shell. The page bootstraps the plan as JSON
    matching the ``GET /api/plans/<id>/`` detail shape from #433 so the
    editor hydrates without a second round trip; every subsequent write
    (text edits, drags, toggles, add/delete) goes through the JSON API.
    There is no ``request.POST`` handling here -- if you find yourself
    wanting to add one, route through the API instead.
    """
    plan = get_object_or_404(
        Plan.objects
        .select_related('member', 'sprint')
        .prefetch_related(
            'weeks__checkpoints',
            'resources',
            'deliverables',
            'next_steps',
            'interview_notes',
        ),
        pk=plan_id,
    )

    plan_payload = serialize_plan_detail(plan)
    token = _get_or_create_editor_token(request.user)

    # Internal/external interview notes are split into two lists in the
    # bootstrap payload so the editor can render the two visibility tabs
    # without additional filtering. The visibility filter MUST be
    # applied here so a non-staff token used against the same shape
    # could not see internal notes -- mirrors #433's queryset gate.
    notes = list(plan.interview_notes.all().order_by('-created_at'))
    internal_notes = [
        _serialize_interview_note(n) for n in notes if n.visibility == 'internal'
    ]
    external_notes = [
        _serialize_interview_note(n) for n in notes if n.visibility == 'external'
    ]
    plan_payload['interview_notes'] = {
        'internal': internal_notes,
        'external': external_notes,
    }

    # Activity counts -- "X checkpoints across Y weeks" line below the grid.
    weeks_count = len(plan_payload['weeks'])
    checkpoints_count = sum(len(w['checkpoints']) for w in plan_payload['weeks'])

    return render(request, 'studio/plans/edit.html', {
        'plan': plan,
        'plan_json': json.dumps(plan_payload),
        'api_token': token.key,
        'api_base': '/api/',
        'plan_status_choices': PLAN_STATUS_CHOICES,
        'weeks_count': weeks_count,
        'checkpoints_count': checkpoints_count,
    })


def _serialize_interview_note(note):
    """Match the API note shape; used by the editor bootstrap payload."""
    return {
        'id': note.pk,
        'visibility': note.visibility,
        'kind': note.kind,
        'body': note.body,
        'created_at': note.created_at.isoformat() if note.created_at else None,
        'updated_at': note.updated_at.isoformat() if note.updated_at else None,
    }


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
