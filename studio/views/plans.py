"""Studio views for managing personal sprint plans (issues #432, #434).

The form-based CRUD pages from #432 (list / new / detail / note) live
unchanged here. ``plan_edit`` was replaced in #434 with a thin
client-side shell that renders the drag-and-drop authoring UI; all
writes from that page go through the JSON API in #433. The view is
intentionally GET-only -- there is no ``request.POST`` handling, no
``Save`` button, and no parallel reorder endpoint inside Studio.

Issue #444 extracted the editor body into ``_editor_body.html`` and the
context-build into ``studio.services.plan_editor.build_plan_editor_context``.
Member-facing plan workspaces now use sprint-scoped routes and do not
include this Studio editor surface.

Interview-note visibility is enforced at the queryset layer
(:meth:`plans.models.InterviewNoteQuerySet.visible_to`). The plan detail
page splits the page into an "Internal notes (staff only)" section and an
"External notes (shareable with member)" section scoped to the member, so
a staff member glancing at the page understands the visibility before
reading.
"""

import logging
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.http import HttpResponsePermanentRedirect, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from notifications.services.notification_service import NotificationService
from plans.models import (
    PLAN_STATUS_CHOICES,
    InterviewNote,
    Plan,
    Sprint,
)
from plans.services import create_plan_for_enrollment
from studio.decorators import staff_required
from studio.services.plan_editor import build_plan_editor_context

logger = logging.getLogger(__name__)

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


def _picker_prefill_display(member_id_str):
    """Return the picker's visible-input prefill text for a user pk.

    The picker include's hidden ``<input name="member">`` is seeded
    by the inline script in ``form.html`` from ``form_data.member``
    (the user's pk as a string). The visible search ``<input>`` also
    needs something to render; the autocomplete endpoint never runs on
    first paint, so the view resolves the display name once
    server-side. Falls back to the email when first/last name are blank.

    Returns ``''`` for an unset / invalid / stale id so the template
    skips the seed-script branch entirely.
    """
    if not member_id_str or not member_id_str.isdigit():
        return ''
    user = User.objects.filter(pk=int(member_id_str)).first()
    if user is None:
        return ''
    full = (user.get_full_name() or '').strip()
    return full or user.email


def _picker_extra_query_for(sprint_id_str):
    """Build the picker ``extra_query`` string for a sprint pk.

    Returns ``'sprint=<slug>'`` when the id resolves to a real sprint,
    empty string otherwise. The picker include passes this string
    through to every search request, which lights up the sprint-context
    badges (``In this sprint``, ``Has plan in sprint``).
    """
    if not sprint_id_str or not sprint_id_str.isdigit():
        return ''
    sprint = Sprint.objects.filter(pk=int(sprint_id_str)).only('slug').first()
    if sprint is None:
        return ''
    return urlencode({'sprint': sprint.slug})


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

    Issue #735 swapped the inline member ``<select>`` for the reusable
    people picker include from #720. Three extra context keys are
    passed to the template: ``user_search_url`` (the JSON endpoint the
    picker queries), ``picker_extra_query`` (a URL-encoded
    ``sprint=<slug>`` when a sprint is in scope, so the suggestion rows
    show the sprint-context badges), and ``prefill_member_display`` (the
    display name to seed the picker's visible input with on the #719
    bell-notification entry path).
    """
    sprints = Sprint.objects.order_by('-start_date')
    user_search_url = reverse('studio_user_search')

    if request.method != 'POST':
        # Optional pre-fill via ``?user=<pk>&sprint=<pk>`` so the
        # plan_request bell-notification (issue #719) lands the operator
        # on a form with both selects already chosen. Invalid or missing
        # ids silently fall through to an empty form -- never raise.
        prefill_member = ''
        prefill_sprint = ''
        raw_user = (request.GET.get('user') or '').strip()
        raw_sprint = (request.GET.get('sprint') or '').strip()
        if raw_user.isdigit() and User.objects.filter(pk=int(raw_user)).exists():
            prefill_member = raw_user
        if raw_sprint.isdigit() and Sprint.objects.filter(pk=int(raw_sprint)).exists():
            prefill_sprint = raw_sprint
        return render(request, 'studio/plans/form.html', {
            'plan': None,
            'form_action': 'create',
            'form_data': {
                'member': prefill_member,
                'sprint': prefill_sprint,
                'status': 'draft',
            },
            'sprints': sprints,
            'plan_status_choices': PLAN_STATUS_CHOICES,
            'user_search_url': user_search_url,
            'picker_extra_query': _picker_extra_query_for(prefill_sprint),
            'prefill_member_display': _picker_prefill_display(prefill_member),
            'error': '',
            'primary_label': 'Create plan',
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
            'plan_status_choices': PLAN_STATUS_CHOICES,
            'user_search_url': user_search_url,
            'picker_extra_query': _picker_extra_query_for(form_data['sprint']),
            'prefill_member_display': _picker_prefill_display(form_data['member']),
            'error': error,
            'primary_label': 'Create plan',
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


@staff_required
@require_POST
def plan_share(request, plan_id):
    """Share / re-share a sprint plan with the member (issue #732).

    Stamps ``Plan.shared_at`` to ``timezone.now()`` and fires the
    ``plan_shared`` bell + transactional email via
    :meth:`NotificationService.create_plan_shared`.

    Re-share is allowed by design — every POST creates a fresh
    notification and a fresh email log. The template wraps the
    re-share button in a JS ``confirm()`` prompt so a stray click
    does not surprise-notify the member.

    The save is the source of truth: even if the notification helper
    raises (unlikely — it already swallows SES exceptions), the
    ``shared_at`` save has already committed.
    """
    plan = get_object_or_404(
        Plan.objects.select_related('member', 'sprint'),
        pk=plan_id,
    )
    was_already_shared = plan.shared_at is not None
    plan.mark_shared()

    try:
        NotificationService.create_plan_shared(plan)
    except Exception:
        logger.exception(
            'Failed to fire plan_shared notification for plan %s',
            plan.pk,
        )

    if was_already_shared:
        messages.success(
            request, f'Re-shared plan with {plan.member.email}.'
        )
    else:
        messages.success(
            request, f'Plan shared with {plan.member.email}.'
        )

    return redirect('studio_plan_edit', plan_id=plan.pk)
