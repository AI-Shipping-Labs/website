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

from plans.models import Plan, SprintEnrollment, Week


def create_plan_for_enrollment(*, sprint, user, enrolled_by):
    """Create a SprintEnrollment + empty Plan for ``user`` in ``sprint``.

    Idempotent. Returns ``(plan, enrollment, created_now)`` where
    ``created_now`` is True iff this call materialized the plan (the
    enrollment may already exist from a prior bulk import). Atomic:
    either both rows exist after return or neither was created.

    The Plan is created with ``status='draft'``,
    ``visibility='private'``, and N empty ``Week`` rows where
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
                status='draft',
                visibility='private',
            )
            for week_index in range(sprint.duration_weeks):
                Week.objects.create(
                    plan=plan,
                    week_number=week_index + 1,
                    position=week_index,
                    theme='',
                )
            return plan, enrollment, True
    except IntegrityError:
        # Race: another request created the plan between the
        # ``filter().first()`` check and our ``Plan.objects.create``.
        # Re-fetch and return the idempotent result.
        plan = Plan.objects.get(member=user, sprint=sprint)
        enrollment = SprintEnrollment.objects.get(sprint=sprint, user=user)
        return plan, enrollment, False
