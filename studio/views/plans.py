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
from django.http import (
    HttpResponse,
    HttpResponsePermanentRedirect,
    HttpResponseRedirect,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.views.decorators.http import require_POST

from crm.services.member_profile import build_member_profile_context
from crm.services.slack_updates import threads_for_plan
from notifications.services.notification_service import NotificationService
from plans.markdown_export import (
    markdown_filename_for_plan,
    render_plan_markdown_export,
)
from plans.models import (
    FirstSprintPlanDraft,
    InterviewNote,
    NextSprintPlanDraft,
    Plan,
    Sprint,
)
from plans.services import (
    FirstSprintDraftSourceMissing,
    MoveUnfinishedItemsError,
    annotate_plan_progress,
    apply_first_sprint_draft,
    carry_over_unfinished_tasks,
    create_plan_for_enrollment,
    draft_first_sprint_plan,
    draft_next_sprint_plan,
    eligible_move_target_sprints,
    find_carry_over_source_plan,
    move_unfinished_items_to_sprint,
    send_plan_ready_email_for_plan,
    unfinished_plan_item_counts,
)
from studio.decorators import staff_required
from studio.services.plan_editor import build_plan_editor_context
from studio.services.plan_lifecycle_actions import (
    build_plan_lifecycle_action_context,
    lifecycle_return_url,
)
from studio.utils import studio_pagination_context
from studio.views.impersonate import impersonate_as

logger = logging.getLogger(__name__)

User = get_user_model()

# Legacy reserved label kept out of the operator-token UI. The editor no
# longer mints or renders a reusable API token; browser saves use session+CSRF.
EDITOR_TOKEN_NAME = 'studio-plan-editor'


class HttpResponseTemporaryRedirect(HttpResponseRedirect):
    """RFC 7231 section 6.4.7: 307 preserves method and body."""

    status_code = 307


@staff_required
def plan_list(request):
    """Table of plans. Filters: ?sprint=, ?member=, ?q=."""
    sprint_filter = (request.GET.get('sprint') or '').strip()
    member_filter = (request.GET.get('member') or '').strip()
    search = (request.GET.get('q') or '').strip()

    plans = annotate_plan_progress(
        Plan.objects.select_related('member', 'sprint'),
    ).order_by('-created_at')

    sprint_filter_id = None
    if sprint_filter.isdigit():
        sprint_filter_id = int(sprint_filter)
        plans = plans.filter(sprint_id=sprint_filter_id)

    member_filter_id = None
    if member_filter.isdigit():
        member_filter_id = int(member_filter)
        plans = plans.filter(member_id=member_filter_id)

    if search:
        plans = plans.filter(
            Q(member__email__icontains=search)
            | Q(member__first_name__icontains=search)
            | Q(member__last_name__icontains=search)
        )
    pager = studio_pagination_context(request, plans)

    return render(request, 'studio/plans/list.html', {
        'plans': pager['page'].object_list,
        'sprints': Sprint.objects.order_by('-start_date'),
        'sprint_filter_id': sprint_filter_id,
        'member_filter_id': member_filter_id,
        'user_search_url': reverse('studio_user_search'),
        'prefill_member_display': _picker_prefill_display(member_filter),
        'search': search,
        **pager,
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


def _ready_email_sprint_name(sprint_id_str):
    if not sprint_id_str or not sprint_id_str.isdigit():
        return 'selected sprint'
    sprint = Sprint.objects.filter(pk=int(sprint_id_str)).only('name').first()
    if sprint is None:
        return 'selected sprint'
    return sprint.name


def _send_ready_email_and_flash(request, plan):
    result = send_plan_ready_email_for_plan(plan, actor=request.user)
    if result['sent']:
        messages.success(
            request,
            f'Plan created for {plan.member.email} in "{plan.sprint.name}" '
            'and plan-ready email sent.',
        )
    elif result['failed']:
        messages.warning(
            request,
            f'Plan created for {plan.member.email} in "{plan.sprint.name}", '
            'but the plan-ready email failed. The member can be retried from '
            'the sprint plan-ready email panel.',
        )
    else:
        messages.info(
            request,
            f'Plan created for {plan.member.email} in "{plan.sprint.name}".',
        )
    return result


@staff_required
def plan_create(request):
    """Form: pick member, pick sprint.

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
        member_profile = None
        raw_user = (request.GET.get('user') or '').strip()
        raw_sprint = (request.GET.get('sprint') or '').strip()
        if raw_user.isdigit():
            member = User.objects.filter(pk=int(raw_user)).first()
            if member is not None:
                prefill_member = raw_user
                # Read-only "Member profile" side panel (issue #883): when
                # the form is pre-filled for a specific member, assemble
                # their onboarding answers + CRM persona/summary/next-steps
                # + recent internal notes so staff start from what the
                # member told us instead of a blank form. Reuses the #871
                # answer-flattening helper; never mutates anything.
                member_profile = build_member_profile_context(member)
        if raw_sprint.isdigit() and Sprint.objects.filter(pk=int(raw_sprint)).exists():
            prefill_sprint = raw_sprint
        return render(request, 'studio/plans/form.html', {
            'plan': None,
            'form_action': 'create',
            'form_data': {
                'member': prefill_member,
                'sprint': prefill_sprint,
                'send_ready_email': True,
            },
            'sprints': sprints,
            'user_search_url': user_search_url,
            'picker_extra_query': _picker_extra_query_for(prefill_sprint),
            'prefill_member_display': _picker_prefill_display(prefill_member),
            'member_profile': member_profile,
            'ready_email_sprint_name': _ready_email_sprint_name(prefill_sprint),
            'error': '',
            'primary_label': 'Create plan',
        })

    form_data = {
        'member': (request.POST.get('member') or '').strip(),
        'sprint': (request.POST.get('sprint') or '').strip(),
        'send_ready_email': request.POST.get('send_ready_email') == 'on',
    }

    def _render_with_error(error, status=400):
        return render(request, 'studio/plans/form.html', {
            'plan': None,
            'form_action': 'create',
            'form_data': form_data,
            'sprints': sprints,
            'user_search_url': user_search_url,
            'picker_extra_query': _picker_extra_query_for(form_data['sprint']),
            'prefill_member_display': _picker_prefill_display(form_data['member']),
            'ready_email_sprint_name': _ready_email_sprint_name(form_data['sprint']),
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

    if form_data['send_ready_email']:
        _send_ready_email_and_flash(request, plan)
    else:
        messages.success(
            request,
            f'Plan created for {member.email} in "{sprint.name}". '
            'Plan-ready email not sent.',
        )
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
    pre_sprint_actions = plan.pre_sprint_actions.order_by('position', 'id')
    next_steps = plan.next_step_actions.order_by('position', 'id')

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
    move_counts = unfinished_plan_item_counts(source_plan=plan)
    move_target_sprints = []
    if move_counts['total']:
        move_target_sprints = list(eligible_move_target_sprints(source_plan=plan))

    return render(request, 'studio/plans/detail.html', {
        'plan': plan,
        'detail_user': plan.member,
        'current_plan': plan,
        'weeks': weeks,
        'resources': resources,
        'deliverables': deliverables,
        'pre_sprint_actions': pre_sprint_actions,
        'next_steps': next_steps,
        'internal_notes': internal_notes,
        'external_notes': external_notes,
        'move_unfinished_counts': move_counts,
        'move_target_sprints': move_target_sprints,
        **build_plan_lifecycle_action_context(plan),
        # Read-only #plan-sprints Slack ingest linked to this plan (#889).
        'slack_threads': threads_for_plan(plan),
    })


@staff_required
@require_POST
def plan_visibility_update(request, plan_id):
    """Staff-only Studio visibility switch for private/cohort plans."""
    plan = get_object_or_404(Plan.objects.select_related('member'), pk=plan_id)
    visibility = (request.POST.get('visibility') or '').strip()
    if visibility not in {'private', 'cohort'}:
        messages.error(request, 'Pick a valid visibility: private or cohort.')
        next_url = request.POST.get('next')
        if next_url:
            return redirect(next_url)
        return redirect('studio_plan_detail', plan_id=plan.pk)
    plan.visibility = visibility
    plan.save(update_fields=['visibility', 'updated_at'])
    messages.success(
        request,
        f'Plan visibility updated to {plan.get_visibility_display()}.',
    )
    next_url = request.POST.get('next')
    if next_url:
        return redirect(next_url)
    return redirect('studio_plan_detail', plan_id=plan.pk)


@staff_required
def plan_markdown_download(request, plan_id):
    """Staff-only Markdown attachment using the member-safe exporter."""
    plan = get_object_or_404(
        Plan.objects
        .select_related('member', 'sprint')
        .prefetch_related(
            'weeks__checkpoints',
            'weeks__notes__author',
            'resources',
            'deliverables',
            'next_steps',
        ),
        pk=plan_id,
    )
    response = HttpResponse(
        render_plan_markdown_export(plan),
        content_type='text/markdown; charset=utf-8',
    )
    response['Content-Disposition'] = (
        f'attachment; filename="{markdown_filename_for_plan(plan)}"'
    )
    return response


@staff_required
@require_POST
def plan_view_as_member(request, plan_id):
    """Impersonate the plan owner and open their member-facing workspace."""
    plan = get_object_or_404(
        Plan.objects.select_related('member', 'sprint'),
        pk=plan_id,
    )
    actor_id = request.user.pk
    impersonate_as(request, plan.member)
    logger.info(
        'Staff user %s viewed plan %s as member %s',
        actor_id,
        plan.pk,
        plan.member_id,
    )
    return redirect(
        reverse(
            'my_plan_detail',
            kwargs={
                'sprint_slug': plan.sprint.slug,
                'plan_id': plan.pk,
            },
        ),
    )


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
    editor view via ``build_plan_editor_context``. The legacy token-name
    argument is retained only for call-site compatibility.
    """
    plan = get_object_or_404(
        Plan.objects
        .select_related('member', 'sprint')
        .prefetch_related(
            'weeks__checkpoints',
            'weeks__notes__author',
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
    context.update(build_plan_lifecycle_action_context(plan))
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


@staff_required
@require_POST
def plan_carry_over(request, plan_id):
    """Staff trigger: carry the member's unfinished tasks forward (issue #808).

    Calls the same ``plans.services`` carry-over logic the member uses,
    with the same source-selection rule (the member's own most-recent
    prior plan) and the same idempotency. Reports the copied count as a
    Studio flash and stays on the Studio plan detail page.
    """
    plan = get_object_or_404(
        Plan.objects.select_related('member', 'sprint'),
        pk=plan_id,
    )
    source_plan = find_carry_over_source_plan(destination_plan=plan)
    if source_plan is None:
        messages.info(
            request,
            f'{plan.member.email} has no previous sprint plan to carry '
            'tasks over from.',
        )
        return redirect(lifecycle_return_url(request, plan))

    copied = carry_over_unfinished_tasks(
        source_plan=source_plan,
        destination_plan=plan,
    )
    if copied:
        messages.success(
            request,
            f'Carried over {copied} task{"" if copied == 1 else "s"} from '
            f'"{source_plan.sprint.name}" into {plan.member.email}\'s plan.',
        )
    else:
        messages.info(
            request,
            f'No new tasks to carry over from "{source_plan.sprint.name}" '
            f'for {plan.member.email} (already up to date).',
        )
    return redirect(lifecycle_return_url(request, plan))


@staff_required
def plan_move_unfinished(request, plan_id):
    """Staff confirmation + POST for moving unfinished items to a later sprint."""
    plan = get_object_or_404(
        Plan.objects.select_related('member', 'sprint'),
        pk=plan_id,
    )
    counts = unfinished_plan_item_counts(source_plan=plan)
    if counts['total'] == 0:
        messages.info(
            request,
            f'{plan.member.email} has no unfinished plan items to move.',
        )
        return redirect('studio_plan_detail', plan_id=plan.pk)

    target_sprints = list(eligible_move_target_sprints(source_plan=plan))
    if not target_sprints:
        messages.info(request, 'No later sprint available.')
        return redirect('studio_plan_detail', plan_id=plan.pk)

    selected_slug = (
        request.POST.get('target_sprint_slug')
        if request.method == 'POST'
        else request.GET.get('target_sprint_slug')
    )
    selected = None
    invalid_target = False
    if selected_slug:
        selected = next(
            (sprint for sprint in target_sprints if sprint.slug == selected_slug),
            None,
        )
        if selected is None:
            messages.error(request, 'Pick a valid later sprint.')
            invalid_target = True
            selected = target_sprints[0]
    else:
        if request.method == 'POST':
            messages.error(request, 'Pick a valid later sprint.')
            invalid_target = True
        selected = target_sprints[0]

    if request.method != 'POST' or invalid_target:
        return render(request, 'studio/plans/move_unfinished.html', {
            'plan': plan,
            'counts': counts,
            'target_sprints': target_sprints,
            'selected_target': selected,
        })

    try:
        summary = move_unfinished_items_to_sprint(
            source_plan=plan,
            target_sprint=selected,
            actor=request.user,
        )
    except MoveUnfinishedItemsError as exc:
        messages.error(request, exc.message)
        return redirect('studio_plan_detail', plan_id=plan.pk)

    target_url = reverse(
        'studio_plan_detail',
        kwargs={'plan_id': summary['target_plan_id']},
    )
    total = summary['moved']['total']
    messages.success(
        request,
        format_html(
            'Moved {} unfinished item{} to "{}" target plan '
            '<a href="{}" class="underline">#{}</a>.',
            total,
            '' if total == 1 else 's',
            selected.name,
            target_url,
            summary['target_plan_id'],
        ),
    )
    return redirect('studio_plan_detail', plan_id=plan.pk)


@staff_required
@require_POST
def plan_draft_next_sprint(request, plan_id):
    """Staff trigger: carry-over + AI draft of the next-sprint plan (#891).

    Runs the single shared ``draft_next_sprint_plan`` service path (also
    behind the plans API). Carry-over always runs first; the AI draft only
    runs when the LLM service is on, lands in a ``NextSprintPlanDraft`` row
    held ASIDE from the plan (never auto-written into the plan's fields),
    and is reviewable in the editor.

    Degrades gracefully: LLM off => carry-over only, no draft row; LLM
    failure => carry-over stands, no partial draft row. Redirects to the
    editor so staff can review the draft panel and copy what they want.
    """
    plan = get_object_or_404(
        Plan.objects
        .select_related('member', 'sprint')
        .prefetch_related(
            'weeks__checkpoints',
            'weeks__notes__author',
            'deliverables',
            'next_steps',
        ),
        pk=plan_id,
    )

    outcome = draft_next_sprint_plan(destination_plan=plan, actor=request.user)

    carried = outcome['carried_over']
    source_plan = outcome['source_plan']
    carry_phrase = (
        f'Carried over {carried} task{"" if carried == 1 else "s"}'
        if carried
        else (
            'No new tasks to carry over (already up to date)'
            if source_plan is not None
            else 'No previous plan to carry over from'
        )
    )

    if not outcome['llm_enabled']:
        messages.info(
            request,
            f'{carry_phrase}. AI draft was skipped because AI is off — '
            'configure an LLM provider in Settings > AI to draft a plan.',
        )
    elif outcome['draft_error']:
        messages.error(
            request,
            f'{carry_phrase}. The AI draft failed — the LLM request errored. '
            'Carry-over succeeded; try the draft again.',
        )
    else:
        updates = outcome['update_count']
        messages.success(
            request,
            f'{carry_phrase} and drafted a next-sprint plan from {updates} '
            f'recent #plan-sprints update{"" if updates == 1 else "s"} — '
            'review and copy it into the plan below.',
        )

    return redirect('studio_plan_edit', plan_id=plan.pk)


@staff_required
@require_POST
def plan_draft_next_sprint_dismiss(request, plan_id):
    """Delete the current AI next-sprint draft and return to the editor (#891).

    The draft is advisory; once staff have copied what they want they
    dismiss it. Idempotent — a no-op when no draft exists.
    """
    plan = get_object_or_404(Plan, pk=plan_id)
    NextSprintPlanDraft.objects.filter(plan=plan).delete()
    messages.info(request, 'Dismissed the AI next-sprint draft.')
    return redirect('studio_plan_edit', plan_id=plan.pk)


@staff_required
@require_POST
def plan_draft_first_sprint(request, plan_id):
    """Staff trigger: create/regenerate the held-aside first-plan draft."""
    plan = get_object_or_404(
        Plan.objects.select_related('member', 'sprint'),
        pk=plan_id,
    )
    try:
        outcome = draft_first_sprint_plan(plan=plan, actor=request.user)
    except FirstSprintDraftSourceMissing:
        logger.exception('Failed to start first-sprint draft for plan %s', plan.pk)
        messages.error(
            request,
            'AI draft could not run because submitted onboarding was not found.',
        )
        return redirect('studio_plan_edit', plan_id=plan.pk)

    if not outcome['llm_enabled']:
        messages.info(
            request,
            'AI draft was skipped because AI is off. Hand-author and share '
            'the plan manually.',
        )
    elif outcome['draft_error']:
        messages.error(
            request,
            'The AI draft failed. The plan is still private and unshared.',
        )
    else:
        messages.success(
            request,
            'Drafted a first sprint plan for staff review.',
        )
    return redirect('studio_plan_edit', plan_id=plan.pk)


@staff_required
@require_POST
def plan_draft_first_sprint_apply(request, plan_id):
    """Apply the current first-sprint draft without sharing the plan."""
    plan = get_object_or_404(Plan, pk=plan_id)
    draft = FirstSprintPlanDraft.objects.filter(plan=plan).first()
    if draft is None:
        messages.info(request, 'There is no first-sprint draft to apply.')
        return redirect('studio_plan_edit', plan_id=plan.pk)
    apply_first_sprint_draft(draft=draft, actor=request.user)
    messages.success(
        request,
        'Applied the first-sprint draft. Review edits, then share when ready.',
    )
    return redirect('studio_plan_edit', plan_id=plan.pk)


@staff_required
@require_POST
def plan_draft_first_sprint_dismiss(request, plan_id):
    """Delete the current first-sprint draft and leave live plan rows alone."""
    plan = get_object_or_404(Plan, pk=plan_id)
    FirstSprintPlanDraft.objects.filter(plan=plan).delete()
    messages.info(request, 'Dismissed the first-sprint draft.')
    return redirect('studio_plan_edit', plan_id=plan.pk)
