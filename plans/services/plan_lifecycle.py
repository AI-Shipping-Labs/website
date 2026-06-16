"""Service helpers for the plans app (issue #444).

The plan-creation flow is shared by two surfaces:

- ``studio_plan_create`` — the existing standalone "New plan" form at
  ``/studio/plans/new`` (member + sprint pickers).
- ``studio_sprint_add_member`` — the new "Add member" button on the
  sprint detail page (sprint locked from the URL, member picker only).

Both surfaces must produce the same artefacts: a ``SprintEnrollment``
row (so membership is authoritative per #443) and an empty ``Plan``
with one ``Week`` per ``sprint.duration_weeks``. Extracting one helper
keeps the artefact shape consistent and prevents the "next time we
add a third surface, copy-paste it" drift the orchestrator flagged.
"""

from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.utils.activation import mark_activated
from plans.models import (
    Checkpoint,
    Deliverable,
    NextStep,
    Plan,
    SprintEnrollment,
    Week,
)
from questionnaires.models import Response
from questionnaires.services import build_response_questions


def create_plan_for_enrollment(*, sprint, user, enrolled_by):
    """Create a SprintEnrollment + empty Plan for ``user`` in ``sprint``.

    Idempotent. Returns ``(plan, enrollment, created_now)`` where
    ``created_now`` is True iff this call materialized the plan (the
    enrollment may already exist from a prior bulk import). Atomic:
    either both rows exist after return or neither was created.

    The Plan is created with ``visibility='private'`` and N empty
    ``Week`` rows where
    N == ``sprint.duration_weeks``. Each Week has
    ``week_number=N``, ``position=N-1``, ``theme=''``, zero
    checkpoints. No resources/deliverables/next-steps/notes are
    seeded — the editor's existing empty-state copy from #434 covers
    this. No stub themes (regresses the empty-state UX).

    Three idempotency cases:
    - both exist already -> return existing rows, ``created_now=False``.
    - enrollment exists but no plan (the bulk-enroll-without-plan case
      from #443) -> create the plan and return both,
      ``created_now=True``.
    - plan exists but no enrollment (legacy data from before #443)
      -> create the enrollment with ``enrolled_by`` set and return
      both, ``created_now=False`` (the plan was not materialized by
      this call).
    """
    try:
        with transaction.atomic():
            existing_plan = Plan.objects.filter(
                member=user, sprint=sprint,
            ).first()
            if existing_plan is not None:
                # Plan already exists. Ensure enrollment exists so the
                # invariant from #443 holds, then return the existing
                # rows. The plan's signal would have created the
                # enrollment already on a normal create path; this
                # ``get_or_create`` is the legacy/race fallback.
                enrollment, _ = SprintEnrollment.objects.get_or_create(
                    sprint=sprint,
                    user=user,
                    defaults={'enrolled_by': enrolled_by},
                )
                return existing_plan, enrollment, False

            # No plan yet. Either there is an existing enrollment
            # (bulk-imported, no plan yet) or neither row exists.
            enrollment, _ = SprintEnrollment.objects.get_or_create(
                sprint=sprint,
                user=user,
                defaults={'enrolled_by': enrolled_by},
            )

            plan = Plan.objects.create(
                member=user,
                sprint=sprint,
                visibility='private',
            )
            for week_index in range(sprint.duration_weeks):
                Week.objects.create(
                    plan=plan,
                    week_number=week_index + 1,
                    position=week_index,
                    theme='',
                )
            # Issue #768: creating a sprint plan is a real platform
            # action. Flip ``account_activated`` if not already set.
            # Idempotent — only the first plan creation writes the row.
            mark_activated(user)
            return plan, enrollment, True
    except IntegrityError:
        # Race: another request created the plan between the
        # ``filter().first()`` check and our ``Plan.objects.create``.
        # Re-fetch and return the idempotent result.
        plan = Plan.objects.get(member=user, sprint=sprint)
        enrollment = SprintEnrollment.objects.get(sprint=sprint, user=user)
        return plan, enrollment, False


def distribute_sprint_feedback(feedback_request, *, actor=None):
    """Distribute a feedback questionnaire to every enrolled member (issue #803).

    For each :class:`~plans.models.SprintEnrollment` in
    ``feedback_request.sprint``, ensure a ``draft``
    :class:`~questionnaires.models.Response` exists for the
    ``(questionnaire, member)`` pair and that its question set is
    materialized via
    :func:`~questionnaires.services.build_response_questions`.

    Idempotent. Re-running never creates duplicate ``Response`` rows (the
    ``(questionnaire, respondent)`` unique constraint from #800 guards
    this) nor duplicate ``ResponseQuestion`` rows
    (``build_response_questions`` is itself idempotent). A member enrolled
    AFTER the first distribution is picked up on a subsequent run -- they
    get their response + materialized questions while existing responses
    are left untouched.

    ``distributed_at`` is stamped on ``feedback_request`` the first time
    only; re-runs leave it as-is.

    Pure ORM -- no HTTP, no AI, no email. Returns a small summary dict
    ``{created, existing, total}`` for the Studio success message.
    """
    questionnaire = feedback_request.questionnaire
    enrollments = SprintEnrollment.objects.filter(
        sprint=feedback_request.sprint,
    ).select_related('user')

    created = 0
    existing = 0
    for enrollment in enrollments:
        response, was_created = Response.objects.get_or_create(
            questionnaire=questionnaire,
            respondent=enrollment.user,
            defaults={'status': 'draft'},
        )
        if was_created:
            created += 1
        else:
            existing += 1
        # Idempotent per #800: a no-op when the response is already
        # materialized, so late enrollees still get their questions.
        build_response_questions(response)

    if feedback_request.distributed_at is None:
        feedback_request.distributed_at = timezone.now()
        feedback_request.save(update_fields=['distributed_at', 'updated_at'])

    return {'created': created, 'existing': existing, 'total': created + existing}


def _dedupe_key(description):
    """Normalize a task description for case-insensitive dedupe.

    Carry-over idempotency keys on a trimmed, lower-cased description so
    re-running the action never duplicates an item the member already has
    in the destination plan. Markdown / casing differences that are pure
    whitespace or letter-case are treated as the same task.
    """
    return (description or '').strip().lower()


def find_carry_over_source_plan(*, destination_plan):
    """Return the member's most-recent prior plan, or ``None`` (issue #808).

    The carry-over source is the destination member's own ``Plan`` in some
    OTHER sprint whose ``sprint.start_date`` is the latest date strictly
    earlier than the destination sprint's ``start_date``. Ties on
    ``start_date`` break toward the higher ``sprint.id`` (the more recently
    created sprint). Only the member's OWN plans are ever a source.

    Returns ``None`` when the member has no earlier plan.
    """
    return (
        Plan.objects.filter(member=destination_plan.member)
        .exclude(pk=destination_plan.pk)
        .filter(sprint__start_date__lt=destination_plan.sprint.start_date)
        .select_related('sprint')
        .order_by('-sprint__start_date', '-sprint__id')
        .first()
    )


def count_total_unfinished(*, source_plan):
    """Total unfinished tasks on a plan, ignoring any destination (issue #808).

    Counts all unfinished (``done_at IS NULL``) ``Checkpoint``,
    ``Deliverable`` and ``NextStep`` rows on ``source_plan``. Used to
    decide whether the carry-over panel is shown at all: a source plan with
    zero unfinished tasks hides the panel, while one whose unfinished items
    are merely all-already-copied shows the "all caught up" state instead.
    """
    checkpoints = Checkpoint.objects.filter(
        week__plan=source_plan, done_at__isnull=True,
    ).count()
    deliverables = source_plan.deliverables.filter(done_at__isnull=True).count()
    next_steps = source_plan.next_steps.filter(done_at__isnull=True).count()
    return checkpoints + deliverables + next_steps


def count_unfinished_carry_over_items(*, source_plan, destination_plan):
    """Count source items still needing carry-over (issue #808).

    Counts unfinished (``done_at IS NULL``) ``Checkpoint`` rows across all
    weeks plus plan-level unfinished ``Deliverable`` and ``NextStep`` rows
    on ``source_plan`` that are NOT already present in ``destination_plan``
    under the same dedupe rule used by :func:`carry_over_unfinished_tasks`
    (trimmed, case-insensitive ``description``; checkpoints scoped to the
    destination week they would land in).

    This is the "N tasks available to carry over" number the panel shows.
    It returns the count of rows a carry-over run would actually create, so
    once everything has been copied it returns 0 and the panel can switch
    to the "all caught up" state.
    """
    dest_weeks = list(destination_plan.weeks.all())
    weeks_by_number = {week.week_number: week for week in dest_weeks}
    last_week = max(
        dest_weeks, key=lambda w: w.week_number,
    ) if dest_weeks else None

    existing_checkpoints = {}
    for week in dest_weeks:
        existing_checkpoints[week.pk] = {
            _dedupe_key(cp.description) for cp in week.checkpoints.all()
        }
    existing_deliverables = {
        _dedupe_key(d.description) for d in destination_plan.deliverables.all()
    }
    existing_next_steps = {
        _dedupe_key(s.description) for s in destination_plan.next_steps.all()
    }

    remaining = 0
    if last_week is not None:
        source_checkpoints = (
            Checkpoint.objects.filter(
                week__plan=source_plan, done_at__isnull=True,
            )
            .select_related('week')
            .order_by('week__week_number', 'position', 'id')
        )
        for checkpoint in source_checkpoints:
            target_week = weeks_by_number.get(
                checkpoint.week.week_number, last_week,
            )
            key = _dedupe_key(checkpoint.description)
            bucket = existing_checkpoints.setdefault(target_week.pk, set())
            if key in bucket:
                continue
            bucket.add(key)
            remaining += 1

    for deliverable in source_plan.deliverables.filter(done_at__isnull=True):
        key = _dedupe_key(deliverable.description)
        if key in existing_deliverables:
            continue
        existing_deliverables.add(key)
        remaining += 1

    for step in source_plan.next_steps.filter(done_at__isnull=True):
        key = _dedupe_key(step.description)
        if key in existing_next_steps:
            continue
        existing_next_steps.add(key)
        remaining += 1

    return remaining


def carry_over_unfinished_tasks(*, source_plan, destination_plan):
    """Copy unfinished tasks from one plan into another (issue #808).

    Copies only rows whose ``done_at IS NULL`` — finished tasks stay on the
    source plan. Three task types are carried:

    - ``Checkpoint`` (per-week): mapped into the destination plan's ``Week``
      with the same ``week_number``; if the destination has no such week
      (shorter sprint) the checkpoint lands in the destination's last week.
    - ``Deliverable`` (plan-level).
    - ``NextStep`` (plan-level).

    ``description`` and ``position`` are copied; ``done_at`` is reset to
    ``NULL`` on every copy regardless of the source value.

    Idempotent: a destination item with a matching trimmed, case-insensitive
    ``description`` of the same type is treated as already present and is not
    duplicated. For checkpoints the match is scoped to the destination week
    the item would land in. Re-running therefore copies only the items not
    already present and a run with nothing new is a zero-row no-op.

    Nothing else is copied (``Resource``, ``WeekNote``, ``goal``, summary
    fields, visibility, comments, interview notes).

    Runs inside a single ``transaction.atomic()`` block so a partial copy
    cannot occur. Returns the integer count of rows actually created.
    """
    copied = 0
    with transaction.atomic():
        # Destination weeks keyed by ``week_number`` for the week mapping,
        # plus the last week (highest ``week_number``) as the overflow
        # bucket for a shorter destination sprint.
        dest_weeks = list(destination_plan.weeks.all())
        weeks_by_number = {week.week_number: week for week in dest_weeks}
        last_week = None
        if dest_weeks:
            last_week = max(dest_weeks, key=lambda w: w.week_number)

        # Existing destination dedupe sets. Checkpoints are scoped per
        # destination week id; deliverables / next steps are plan-level.
        existing_checkpoints = {}
        for week in dest_weeks:
            existing_checkpoints[week.pk] = {
                _dedupe_key(cp.description)
                for cp in week.checkpoints.all()
            }
        existing_deliverables = {
            _dedupe_key(d.description)
            for d in destination_plan.deliverables.all()
        }
        existing_next_steps = {
            _dedupe_key(s.description)
            for s in destination_plan.next_steps.all()
        }

        if last_week is not None:
            source_checkpoints = (
                Checkpoint.objects.filter(
                    week__plan=source_plan, done_at__isnull=True,
                )
                .select_related('week')
                .order_by('week__week_number', 'position', 'id')
            )
            for checkpoint in source_checkpoints:
                target_week = weeks_by_number.get(
                    checkpoint.week.week_number, last_week,
                )
                key = _dedupe_key(checkpoint.description)
                bucket = existing_checkpoints.setdefault(target_week.pk, set())
                if key in bucket:
                    continue
                Checkpoint.objects.create(
                    week=target_week,
                    description=checkpoint.description,
                    position=checkpoint.position,
                    done_at=None,
                )
                bucket.add(key)
                copied += 1

        source_deliverables = source_plan.deliverables.filter(
            done_at__isnull=True,
        ).order_by('position', 'id')
        for deliverable in source_deliverables:
            key = _dedupe_key(deliverable.description)
            if key in existing_deliverables:
                continue
            Deliverable.objects.create(
                plan=destination_plan,
                description=deliverable.description,
                position=deliverable.position,
                done_at=None,
            )
            existing_deliverables.add(key)
            copied += 1

        source_next_steps = source_plan.next_steps.filter(
            done_at__isnull=True,
        ).order_by('position', 'id')
        for step in source_next_steps:
            key = _dedupe_key(step.description)
            if key in existing_next_steps:
                continue
            NextStep.objects.create(
                plan=destination_plan,
                kind=step.kind,
                description=step.description,
                position=step.position,
                done_at=None,
            )
            existing_next_steps.add(key)
            copied += 1

    return copied
