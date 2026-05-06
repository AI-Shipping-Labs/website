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
"""


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
