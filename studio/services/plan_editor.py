"""Pure-Python serializer for the drag-and-drop plan editor (issue #434).

The Studio editor hydrates from a JSON blob inlined into the page so the
client doesn't need a second round trip on first paint. The JSON shape
is the same as ``GET /api/plans/<id>/`` from #433; when that endpoint
lands the editor's autosave layer hits ``/api/...`` for every
subsequent change. Until then, this module is the single source of
truth for the bootstrap payload and is also used by tests as the
reference shape.

When #433 lands, this module's ``serialize_plan_detail`` should be
replaced by an import from ``api.serializers.plans`` (or a thin call
through to it). Until then, every change to the API contract should be
reflected here so the editor stays compatible.

``build_plan_editor_context`` (issue #444) is the single source of
truth for the full template context the editor template needs. Both
the staff Studio editor view and the member-facing
``/account/plan/<id>/edit/`` view call it. It mints (or reuses) a
per-user API token under a parameterizable name so the staff token
(``studio-plan-editor``) and member token (``member-plan-editor``)
are distinct rows that staff can audit and revoke independently.
"""

import json

from accounts.models import Token
from plans.models import PLAN_STATUS_CHOICES


def _serialize_checkpoint(checkpoint):
    return {
        'id': checkpoint.pk,
        'week_id': checkpoint.week_id,
        'description': checkpoint.description,
        'position': checkpoint.position,
        'done_at': checkpoint.done_at.isoformat() if checkpoint.done_at else None,
    }


def _serialize_week(week):
    """Return the week dict with its checkpoints inlined.

    Checkpoints are sorted by ``position`` then ``id`` so two
    checkpoints with the same position render in a stable order. The
    drag-drop editor relies on this ordering matching the order the
    API would return -- otherwise the optimistic UI would diverge from
    the server state on first paint.
    """
    checkpoints = sorted(
        week.checkpoints.all(),
        key=lambda c: (c.position, c.pk),
    )
    return {
        'id': week.pk,
        'plan_id': week.plan_id,
        'week_number': week.week_number,
        'theme': week.theme,
        'position': week.position,
        'checkpoints': [_serialize_checkpoint(c) for c in checkpoints],
    }


def _serialize_resource(resource):
    return {
        'id': resource.pk,
        'title': resource.title,
        'url': resource.url,
        'note': resource.note,
        'position': resource.position,
    }


def _serialize_deliverable(deliverable):
    return {
        'id': deliverable.pk,
        'description': deliverable.description,
        'position': deliverable.position,
        'done_at': (
            deliverable.done_at.isoformat() if deliverable.done_at else None
        ),
    }


def _serialize_next_step(next_step):
    return {
        'id': next_step.pk,
        'assignee_label': next_step.assignee_label,
        'description': next_step.description,
        'position': next_step.position,
        'done_at': (
            next_step.done_at.isoformat() if next_step.done_at else None
        ),
    }


def serialize_plan_detail(plan):
    """Return the nested plan-detail dict matching #433's contract.

    Caller is expected to have prefetched ``weeks__checkpoints``,
    ``resources``, ``deliverables``, and ``next_steps`` on the plan.
    The function does NOT include interview notes -- the editor view
    composes those separately so it can split them by visibility (the
    API endpoint exposes them under a different URL).
    """
    weeks = sorted(
        plan.weeks.all(),
        key=lambda w: (w.position, w.week_number),
    )
    resources = sorted(
        plan.resources.all(),
        key=lambda r: (r.position, r.pk),
    )
    deliverables = sorted(
        plan.deliverables.all(),
        key=lambda d: (d.position, d.pk),
    )
    next_steps = sorted(
        plan.next_steps.all(),
        key=lambda n: (n.position, n.pk),
    )

    return {
        'id': plan.pk,
        'sprint': plan.sprint.slug if plan.sprint_id else None,
        'sprint_name': plan.sprint.name if plan.sprint_id else None,
        'duration_weeks': (
            plan.sprint.duration_weeks if plan.sprint_id else None
        ),
        'user_email': plan.member.email,
        'user_id': plan.member_id,
        'status': plan.status,
        'summary': {
            'current_situation': plan.summary_current_situation,
            'goal': plan.summary_goal,
            'main_gap': plan.summary_main_gap,
            'weekly_hours': plan.summary_weekly_hours,
            'why_this_plan': plan.summary_why_this_plan,
        },
        'focus': {
            'main': plan.focus_main,
            'supporting': list(plan.focus_supporting or []),
        },
        'accountability': plan.accountability,
        'weeks': [_serialize_week(w) for w in weeks],
        'resources': [_serialize_resource(r) for r in resources],
        'deliverables': [_serialize_deliverable(d) for d in deliverables],
        'next_steps': [_serialize_next_step(n) for n in next_steps],
        'shared_at': plan.shared_at.isoformat() if plan.shared_at else None,
        'created_at': plan.created_at.isoformat() if plan.created_at else None,
        'updated_at': plan.updated_at.isoformat() if plan.updated_at else None,
    }


def _get_or_create_editor_token(user, name):
    """Return a Token bound to ``user`` under the reserved ``name``.

    ``name`` is a reserved label (e.g. ``studio-plan-editor`` for the
    Studio path, ``member-plan-editor`` for the member self-edit path).
    Re-using the same name across sessions means we get-or-create at
    most one token per (user, name) for this UI -- never accumulate
    one per page load. ``Token.save()`` populates ``key`` on first
    save when blank, so a single ``get_or_create`` is enough.

    Tokens minted here live alongside any operator-issued tokens from
    the Studio token UI (#431). Naming them under reserved labels
    means revoking one does not also kill ad-hoc operator tokens.
    """
    token, _ = Token.objects.get_or_create(user=user, name=name)
    return token


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


def build_plan_editor_context(plan, *, viewer, token_name):
    """Return the full template context the plan editor template needs.

    Issue #444. Single source of truth shared by ``studio_plan_edit``
    (staff Studio path) and ``member_plan_edit`` (member-facing path).
    Both call ``render(request, ..., build_plan_editor_context(plan,
    viewer=request.user, token_name='studio-plan-editor' or
    'member-plan-editor'))``.

    Caller is expected to have prefetched ``weeks__checkpoints``,
    ``resources``, ``deliverables``, ``next_steps``, and
    ``interview_notes`` on the plan.

    The visibility filter for interview notes lives at the queryset
    layer in #433; this function only mirrors that bucketing into the
    bootstrap payload's ``interview_notes`` field. A non-staff token
    used against the same shape via the API still cannot read internal
    notes -- the API queryset gate enforces that.
    """
    plan_payload = serialize_plan_detail(plan)
    token = _get_or_create_editor_token(viewer, token_name)

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

    weeks_count = len(plan_payload['weeks'])
    checkpoints_count = sum(len(w['checkpoints']) for w in plan_payload['weeks'])

    return {
        'plan': plan,
        'plan_json': json.dumps(plan_payload),
        'api_token': token.key,
        'api_base': '/api/',
        'plan_status_choices': PLAN_STATUS_CHOICES,
        'weeks_count': weeks_count,
        'checkpoints_count': checkpoints_count,
    }
