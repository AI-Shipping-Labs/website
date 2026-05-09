"""Row-kind classification for the cohort progress board (issue #461).

The cohort progress board renders one row per enrolled member of a
sprint. Each row is one of three kinds:

- ``cohort``: the member shared their plan with the cohort -- card is
  clickable and the plan body (focus, weeks, checkpoints) renders.
- ``private``: the member kept the plan private -- card is non-clickable
  and exposes ONLY the progress counts; no plan body, no week themes,
  no checkpoint descriptions.
- ``no_plan``: the member is enrolled but has not authored a plan yet.

The view module (:mod:`plans.views.cohort`) is forbidden from comparing
``plan.visibility`` to a literal string (the regression test in
``plans/tests/test_view_layer_no_visibility_literals.py`` scans the view
file). The classification therefore lives here, outside the view module.
"""

ROW_KIND_COHORT = 'cohort'
ROW_KIND_PRIVATE = 'private'
ROW_KIND_NO_PLAN = 'no_plan'

# Visibility values that map onto the ``cohort`` row kind. Any other
# active visibility value falls through to ``private``. ``public`` is
# reserved for a future issue and not in the active enum yet.
_COHORT_VISIBILITIES = frozenset({'cohort'})


def classify_plan_row_kind(plan):
    """Map a :class:`plans.models.Plan` instance to its row kind.

    A plan with ``visibility`` in :data:`_COHORT_VISIBILITIES` renders as
    a clickable cohort card; anything else renders as a counts-only
    private card. The function does not branch on owner identity --
    self-row treatment is handled separately via the ``is_self`` flag.
    """
    if plan.visibility in _COHORT_VISIBILITIES:
        return ROW_KIND_COHORT
    return ROW_KIND_PRIVATE


def build_progress_rows(*, plans, no_plan_members, viewer):
    """Build the unified ``progress_rows`` list for the board template.

    ``plans`` is an iterable of :class:`plans.models.Plan` rows
    annotated with ``progress_total`` and ``progress_done`` (the
    ``cohort_progress_rows`` queryset method does this). ``no_plan_members``
    is an iterable of :class:`accounts.models.User` -- one per enrolled
    member of the sprint who has NOT authored a plan yet.

    Returns a list of dicts with keys: ``kind``, ``member``, ``plan``,
    ``progress_done``, ``progress_total``, ``is_self``. Sort order:

    1. The viewer's own row first, regardless of progress or plan state.
    2. Other plan rows: ``progress_done`` desc, ``progress_total`` desc,
       ``member.email`` asc as a deterministic tiebreaker.
    3. Other ``no_plan`` rows pinned to the bottom, then
       ``member.email`` asc.

    The viewer's own row is still included on the same list as everyone
    else, but is pinned first so members can find their own record
    immediately.
    """
    plan_rows = []
    for plan in plans:
        plan_rows.append({
            'kind': classify_plan_row_kind(plan),
            'member': plan.member,
            'plan': plan,
            'progress_done': plan.progress_done,
            'progress_total': plan.progress_total,
            'is_self': plan.member_id == getattr(viewer, 'id', None),
        })

    no_plan_rows = [
        {
            'kind': ROW_KIND_NO_PLAN,
            'member': member,
            'plan': None,
            'progress_done': 0,
            'progress_total': 0,
            'is_self': member.id == getattr(viewer, 'id', None),
        }
        for member in no_plan_members
    ]

    rows = plan_rows + no_plan_rows
    rows.sort(
        key=lambda row: (
            not row['is_self'],
            row['kind'] == ROW_KIND_NO_PLAN,
            -row['progress_done'],
            -row['progress_total'],
            row['member'].email,
        ),
    )

    return rows
