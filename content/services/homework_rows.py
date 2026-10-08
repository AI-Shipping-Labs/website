"""Question count and learner status for homework navigation rows (#1916).

Homework and capstone rows render as ordinary unit rows in the syllabus
list and the reader sidebar. This module annotates the already-prefetched
syllabus tree in place so those rows can show two pieces of meta:

- ``unit.homework_question_count``: the number of ``Question`` rows on the
  ``Homework`` resolved for the unit in the page's cohort. Not sensitive,
  so it is set for any viewer once a cohort resolves; it stays unset when
  no ``Homework`` row resolves or the homework has no questions.
- ``unit.homework_row_state``: the shared
  ``community_base.homework_steps.state.LearnerHomeworkState``. Set only
  for an authenticated learner whose selected cohort is one they own (or
  staff), never for a public or staff cohort preview. The label comes
  from the shared model; this site never maps states to text itself.

Everything is batch-loaded: the homework rows with their questions, the
learner's submissions with their answers, and the learner's drafts are each
read once per page, so the query count does not grow with the number of
homework units.
"""

from community_base.homework_steps.models import HomeworkDraft
from community_base.homework_steps.state import calculate_homework_state

from content.models import CohortEnrollment
from content.models.homework import Homework, Submission
from content.services.homework_step_reader import (
    assignment_key,
    build_state_assignment,
)

UNIT_KIND_HOMEWORK = 'homework'


def _homework_units(modules):
    """Every homework-kind unit with a ``content_id`` in the syllabus tree."""
    units = []
    for module in modules:
        for child in module.children.all():
            units.extend(
                unit for unit in child.units.all()
                if unit.kind == UNIT_KIND_HOMEWORK and unit.content_id
            )
        units.extend(
            unit for unit in module.units.all()
            if unit.kind == UNIT_KIND_HOMEWORK and unit.content_id
        )
    return units


def _owns_cohort(user, cohort):
    if not getattr(user, 'is_authenticated', False):
        return False
    if user.is_staff:
        return True
    return CohortEnrollment.objects.filter(user=user, cohort=cohort).exists()


def _learner_states(user, homework_rows):
    """``{homework.pk: LearnerHomeworkState}`` from batch-loaded rows."""
    submissions = {}
    for submission in (
        Submission.objects.filter(student=user, homework__in=homework_rows)
        .prefetch_related('answers')
    ):
        submissions.setdefault(submission.homework_id, submission)
    drafts = {
        draft.assignment_key: draft
        for draft in HomeworkDraft.objects.filter(
            user=user,
            assignment_key__in=[assignment_key(row) for row in homework_rows],
        ).only('assignment_key', 'answers', 'final_fields')
    }
    states = {}
    for homework in homework_rows:
        try:
            assignment = build_state_assignment(
                homework, list(homework.questions.all()),
                submissions.get(homework.pk),
            )
        except (KeyError, ValueError):
            # Malformed source questions (unknown type, duplicate option
            # labels) also break the stepper; the row simply omits status.
            continue
        draft = drafts.get(assignment.key)
        states[homework.pk] = calculate_homework_state(
            assignment,
            draft_answers=draft.answers if draft else {},
            draft_final_fields=draft.final_fields if draft else {},
            draft_exists=draft is not None,
        )
    return states


def annotate_homework_rows(modules, user, cohort, *, include_status):
    """Set question count and learner status on the tree's homework units.

    ``cohort`` is the cohort the page already resolves for homework (the
    owned cohort when the viewer has one, otherwise the display cohort).
    ``include_status`` is false for public and staff cohort previews; even
    when true, status is computed only when ``user`` owns ``cohort`` (an
    enrollment, or staff).
    """
    if cohort is None:
        return
    units = _homework_units(modules)
    if not units:
        return
    homework_rows = list(
        Homework.objects.filter(
            cohort=cohort,
            content_id__in={unit.content_id for unit in units},
        ).prefetch_related('questions')
    )
    if not homework_rows:
        return
    for homework in homework_rows:
        # ``is_self_paced`` reads the cohort; reuse the instance in hand.
        homework.cohort = cohort
    homework_by_content_id = {row.content_id: row for row in homework_rows}
    states = {}
    if include_status and _owns_cohort(user, cohort):
        states = _learner_states(user, homework_rows)
    for unit in units:
        homework = homework_by_content_id.get(unit.content_id)
        if homework is None:
            continue
        question_count = len(homework.questions.all())
        if question_count:
            unit.homework_question_count = question_count
        state = states.get(homework.pk)
        if state is not None:
            unit.homework_row_state = state
