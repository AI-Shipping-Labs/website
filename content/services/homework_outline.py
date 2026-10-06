"""Virtual homework step outline for navigation surfaces (issue #1794).

An activated stepped homework stays ONE ``Homework``/``Unit`` pair; this
module only derives the display outline (Introduction, one row per active
question, the review step) from the viewer's own cohort ``Homework`` rows.
Steps deep-link the existing reader stepper through the same canonical
``<unit url>/<homework_step>`` URL shape the stepper itself builds, with the
learner's selected owned ``cohort`` kept as an encoded query parameter. The
outline never creates rows and never carries answer content.
"""

from urllib.parse import urlencode, urlparse

from content.models.homework import Homework
from content.services.homework_step_reader import (
    LEARNING_IN_PUBLIC_KEY,
    question_key,
)

LEARNING_IN_PUBLIC_STEP_LABEL = 'Learning in Public'
INTRO_STEP_KEY = 'intro'
REVIEW_STEP_KEY = 'review'
INTRO_STEP_LABEL = 'Introduction'
REVIEW_STEP_LABEL = 'Review & submit'


def step_url(unit, step_key, cohort_key=''):
    """Canonical stepper URL for one homework step.

    Mirrors the stepper's own ``step_url_builder``
    (``f'{unit.get_absolute_url().rstrip("/")}/{step}'``), so a step link
    loads the existing reader/stepper. The cohort rides alongside
    ``homework_step`` as an encoded query value.
    """
    url = f"{unit.get_absolute_url().rstrip('/')}/{step_key}"
    if cohort_key:
        url += '?' + urlencode({'cohort': cohort_key})
    return url


def homework_outline_steps(unit, homework, cohort_key=''):
    """Ordered ``(title, url)`` display steps for an activated assignment.

    Count and order come from the resolved cohort homework's ``Question``
    rows (never hardcoded), using the same stable ``source_question_id``
    keys the stepper routes on. A configured learning-in-public cap adds
    its stepper stop between the questions and the review step, matching
    the reader sidebar's ``stepper.nav_steps``.
    """
    steps = [(INTRO_STEP_LABEL, step_url(unit, INTRO_STEP_KEY, cohort_key))]
    for index, question in enumerate(homework.questions.all(), start=1):
        steps.append((
            f'Question {index}',
            step_url(unit, question_key(question), cohort_key),
        ))
    if homework.learning_in_public_cap:
        steps.append((
            LEARNING_IN_PUBLIC_STEP_LABEL,
            step_url(unit, LEARNING_IN_PUBLIC_KEY, cohort_key),
        ))
    steps.append((
        REVIEW_STEP_LABEL,
        step_url(unit, REVIEW_STEP_KEY, cohort_key),
    ))
    return steps


def _current_step_href(unit, steps, current_path):
    """The outline step whose destination is the page being rendered.

    Steps match on path only, so the encoded ``cohort`` query never
    changes which step is current. The homework's canonical route (no
    step suffix) renders the stepper's intro page, so it marks the
    Introduction step. An unknown path (the stepper's safe fallback) —
    or an empty ``current_path`` — marks nothing.
    """
    if not current_path:
        return ''
    normalized = current_path.rstrip('/')
    for _, url in steps:
        if urlparse(url).path.rstrip('/') == normalized:
            return url
    if normalized == unit.get_absolute_url().rstrip('/'):
        return steps[0][1]
    return ''


def annotate_homework_outlines(modules, user, cohort, current_path=''):
    """Set ``unit.homework_outline_steps`` on the viewer's stepped homework.

    ``modules`` is the already-prefetched syllabus tree; units are annotated
    in place so the syllabus partials read the outline off the instances
    they already iterate. The outline appears only when the selected cohort
    is one the learner owns (mirroring ``resolve_homework_for_unit``'s
    enrollment gate), the cohort's ``Homework`` row has ``stepper_enabled``
    with at least one question, and the viewer can open that homework.
    Everyone else keeps the single ordinary homework row.

    ``current_path`` (the reader sidebar passes ``request.path``) also
    resolves ``unit.homework_outline_current_href`` — the step to mark
    current and open the group on; empty when the viewer is elsewhere.
    """
    homework_units = []
    for module in modules:
        for child in module.children.all():
            homework_units.extend(
                unit for unit in child.units.all()
                if unit.kind == 'homework' and unit.content_id
            )
        homework_units.extend(
            unit for unit in module.units.all()
            if unit.kind == 'homework' and unit.content_id
        )
    if not homework_units or cohort is None:
        return
    if not user.is_authenticated:
        return
    from content.models import CohortEnrollment
    if not user.is_staff and not CohortEnrollment.objects.filter(
        user=user, cohort=cohort,
    ).exists():
        return
    rows = Homework.objects.filter(
        cohort=cohort,
        content_id__in={unit.content_id for unit in homework_units},
        stepper_enabled=True,
    ).prefetch_related('questions')
    homework_by_content_id = {row.content_id: row for row in rows}
    cohort_key = cohort.external_key or ''
    for unit in homework_units:
        homework = homework_by_content_id.get(unit.content_id)
        if homework is None or not homework.questions.exists():
            continue
        steps = homework_outline_steps(unit, homework, cohort_key)
        unit.homework_outline_steps = steps
        unit.homework_outline_current_href = _current_step_href(
            unit, steps, current_path,
        )
