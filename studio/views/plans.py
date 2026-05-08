"""Studio views for managing personal sprint plans (issues #432, #434, #444).

The form-based CRUD pages from #432 (list / new / detail / note) live
unchanged here. ``plan_edit`` was replaced in #434 with a thin
client-side shell that renders the drag-and-drop authoring UI; all
writes from that page go through the JSON API in #433. The view is
intentionally GET-only -- there is no ``request.POST`` handling, no
``Save`` button, and no parallel reorder endpoint inside Studio.

Issue #444 extracts the editor body into ``_editor_body.html`` and the
context-build into ``studio.services.plan_editor.build_plan_editor_context``
so the member-facing ``/account/plan/<id>/edit/`` view can include the
SAME partial rather than introducing a parallel editor surface. The
token name parameter (``studio-plan-editor`` here, ``member-plan-editor``
in the member view) is the only difference.

Interview-note visibility is enforced at the queryset layer
(:meth:`plans.models.InterviewNoteQuerySet.visible_to`). The plan detail
page splits the page into an "Internal notes (staff only)" section and an
"External notes (shareable with member)" section scoped to the member, so
a staff member glancing at the page understands the visibility before
reading.
"""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.http import HttpResponsePermanentRedirect, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from plans.models import (
    PLAN_STATUS_CHOICES,
    InterviewNote,
    Plan,
    Sprint,
)
from plans.services import create_plan_for_enrollment
from studio.decorators import staff_required
from studio.services.plan_editor import build_plan_editor_context

User = get_user_model()

# Stable name attached to the API token issued to a staff user when they
# open the drag-and-drop plan editor. Re-using the same name across
# sessions means we get-or-create at most one token per staff user for
# this UI -- never accumulate one per page load.
EDITOR_TOKEN_NAME = 'studio-plan-editor'


def _normalize_plan_status(raw):
    valid = {choice[0] for choice in PLAN_STATUS_CHOICES}
    if raw in valid:
        return raw
    return 'draft'


class HttpResponseTemporaryRedirect(HttpResponseRedirect):
    """RFC 7231 section 6.4.7: 307 preserves method and body."""

    status_code = 307


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
    """Form: pick member, pick sprint. ``status`` defaults to draft.

    The plan-creation path goes through
    :func:`plans.services.create_plan_for_enrollment` (issue #444) so
    the empty plan + enrollment artefacts match exactly what the new
    ``Add member`` button on the sprint detail page produces. This
    view kept its own duplicate-detection branch so the dedicated
    ``A plan already exists`` error message still fires (the sprint-
    detail flow surfaces idempotency as a flash message instead).
    """
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

    if Plan.objects.filter(member=member, sprint=sprint).exists():
        return _render_with_error(
            f'A plan already exists for {member.email} in sprint "{sprint.name}".',
        )

    plan, _enrollment, _created = create_plan_for_enrollment(
        sprint=sprint,
        user=member,
        enrolled_by=request.user,
    )

    messages.success(request, f'Plan created for {member.email} in "{sprint.name}".')
    return redirect('studio_plan_detail', plan_id=plan.pk)


@staff_required
def plan_detail(request, plan_id):
    """Read-mostly detail with member-level internal/external notes split.

    Both note sections are scoped to the member. The "Internal" heading
    contains the words "staff only" so the visibility is unmistakable.
    """
    plan = get_object_or_404(
        Plan.objects.select_related('member', 'sprint'),
        pk=plan_id,
    )
    weeks = (
        plan.weeks
        .prefetch_related('checkpoints', 'notes__author')
        .order_by('position', 'week_number')
    )
    resources = plan.resources.order_by('position', 'id')
    deliverables = plan.deliverables.order_by('position', 'id')
    next_steps = plan.next_steps.order_by('position', 'id')

    # Visibility-aware queryset, scoped to this member. Staff see both
    # blocks, but the template renders them as separate sections so
    # operators can tell at a glance which notes are shareable.
    note_queryset = (
        InterviewNote.objects
        .filter(member=plan.member)
        .select_related('plan__sprint', 'created_by')
        .order_by('-created_at')
    )
    internal_notes = note_queryset.internal()
    external_notes = note_queryset.external()

    return render(request, 'studio/plans/detail.html', {
        'plan': plan,
        'detail_user': plan.member,
        'current_plan': plan,
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

    Issue #444: the context-build is shared with the member-facing
    editor view via ``build_plan_editor_context``. The token name
    ``studio-plan-editor`` is the staff label; the member view uses
    ``member-plan-editor``.
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

    context = build_plan_editor_context(
        plan,
        viewer=request.user,
        token_name=EDITOR_TOKEN_NAME,
    )
    return render(request, 'studio/plans/edit.html', context)


@staff_required
def interview_note_create(request, plan_id):
    """Legacy plan-scoped note form -> member-scoped note form."""
    plan = get_object_or_404(Plan.objects.select_related('member'), pk=plan_id)
    target = (
        reverse('studio_member_note_create', kwargs={'user_id': plan.member_id})
        + f'?plan_id={plan.pk}'
    )
    return HttpResponsePermanentRedirect(target)


@staff_required
def interview_note_edit(request, plan_id, note_id):
    """Legacy plan-scoped note edit -> member-scoped note edit."""
    note = get_object_or_404(InterviewNote, pk=note_id)
    target = reverse(
        'studio_member_note_edit',
        kwargs={'user_id': note.member_id, 'note_id': note.pk},
    )
    return HttpResponsePermanentRedirect(target)


@staff_required
def interview_note_delete(request, plan_id, note_id):
    """Legacy plan-scoped note delete -> member-scoped note delete."""
    note = get_object_or_404(InterviewNote, pk=note_id)
    target = reverse(
        'studio_member_note_delete',
        kwargs={'user_id': note.member_id, 'note_id': note.pk},
    )
    return HttpResponseTemporaryRedirect(target)
