"""Bind homework stepper pages to their Q&A comment threads (issue #1897).

On a stepper homework, the Q&A thread is bound to the current stepper page
instead of the homework unit as a whole: each question step, the optional
``learning-in-public`` step, and ``review`` own one thread; ``intro`` keeps
mounting the unit's own ``content_id`` thread, exactly like the non-stepper
all-questions page and every lesson unit.

Thread identity is a deterministic ``uuid5`` over the pair (unit's stable
content identity, public step slug), so the same unit and step keep the same
thread across re-syncs and across cohorts, and ``?cohort=`` never forks it.
The browser comments API stays UUID-keyed; pages just mount the UUID that
belongs to the current step.
"""

import uuid

from content.models.homework import HomeworkStepThread

# Namespace for derived step-thread UUIDs. Fixed so every environment and
# every re-sync derives the identical UUID for the same (unit, step) pair.
HOMEWORK_STEP_THREAD_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL, 'https://aishippinglabs.com/comments/homework-step-threads',
)

INTRO_STEP = 'intro'
REVIEW_STEP = 'review'

# Sidebar titles the homework stepper renders for the fixed steps
# (``community_base.homework_steps.views._render`` nav_steps).
REVIEW_SIDEBAR_TITLE = 'Review & submit'
INTRO_SIDEBAR_TITLE = 'Introduction'
LEARNING_IN_PUBLIC_SIDEBAR_TITLE = 'Learning in Public'


def step_thread_content_id(unit_content_id, step_slug):
    """Return the deterministic thread UUID for one (unit, step slug) pair."""
    return uuid.uuid5(
        HOMEWORK_STEP_THREAD_NAMESPACE, f'{unit_content_id}:{step_slug}',
    )


def mounted_content_id(unit_content_id, step_slug):
    """Return the UUID the given step page mounts.

    ``intro`` mounts the unit thread (existing unit-thread comments stay
    visible there after the stepper is enabled); every other step mounts its
    derived step thread.
    """
    if step_slug == INTRO_STEP:
        return unit_content_id
    return step_thread_content_id(unit_content_id, step_slug)


def step_slugs(homework):
    """Return the public step slugs that own their own thread.

    Mirrors the stepper's valid steps (``_is_valid_homework_route_step``):
    every question's public key plus ``review``, plus ``learning-in-public``
    when the homework has a learning-in-public cap. ``intro`` is excluded --
    it mounts the unit thread and owns no separate identity.
    """
    from content.services.homework_step_reader import (  # noqa: PLC0415
        LEARNING_IN_PUBLIC_KEY,
        question_key,
    )

    slugs = [question_key(question) for question in homework.questions.all()]
    if homework.learning_in_public_cap:
        slugs.append(LEARNING_IN_PUBLIC_KEY)
    slugs.append(REVIEW_STEP)
    return slugs


def ensure_homework_step_threads(unit, homework):
    """Persist and return the thread UUID each stepper page mounts.

    One row per step slug keeps step threads resolvable for notifications,
    orphan detection, and the operator API -- including steps whose question
    was later removed from source (their comments stay stored). Returns a
    mapping of every valid step slug (``intro`` included, mapped to the
    unit's own ``content_id``) to its mounted UUID string.
    """
    unit_content_id = unit.content_id
    if not unit_content_id:
        return {}
    mounted = {}
    slugs = step_slugs(homework)
    existing = {
        thread.step_slug: str(thread.content_id)
        for thread in HomeworkStepThread.objects.filter(
            unit_content_id=unit_content_id, step_slug__in=slugs,
        )
    }
    for slug in slugs:
        mounted_uuid = existing.get(slug)
        if mounted_uuid is None:
            thread, _ = HomeworkStepThread.objects.get_or_create(
                unit_content_id=unit_content_id,
                step_slug=slug,
                defaults={
                    'content_id': step_thread_content_id(unit_content_id, slug),
                },
            )
            mounted_uuid = str(thread.content_id)
        mounted[slug] = mounted_uuid
    mounted[INTRO_STEP] = str(unit_content_id)
    return mounted


def unit_has_stepper_homework(unit):
    """Return whether any cohort's homework for this unit uses the stepper.

    Stepper state is authored per unit and synced to every cohort's
    ``Homework`` row, so any stepper-enabled row with questions means the
    unit renders stepper pages whose unit thread is mounted on ``intro``.
    """
    from content.models.homework import Homework  # noqa: PLC0415

    return Homework.objects.filter(
        content_id=unit.content_id,
        stepper_enabled=True,
        questions__isnull=False,
    ).exists()


def step_page_url(unit, step_slug):
    """Return the canonical step path for one stepper page."""
    return f'{unit.get_absolute_url().rstrip("/")}/{step_slug}'


def homework_step_sidebar_title(unit, step_slug, *, homework=None):
    """Return the step title the homework stepper sidebar renders.

    ``intro`` / ``review`` use their fixed labels; a question step is
    ``Question N`` by its authored position, matching
    ``community_base.homework_steps`` nav labels. Falls back to the slug
    itself when the unit's homework no longer defines the step. Pass a
    pre-fetched ``homework`` to reuse one row across several lookups.
    """
    if step_slug == INTRO_STEP:
        return INTRO_SIDEBAR_TITLE
    if step_slug == REVIEW_STEP:
        return REVIEW_SIDEBAR_TITLE
    from content.services.homework_step_reader import (  # noqa: PLC0415
        LEARNING_IN_PUBLIC_KEY,
        question_key,
    )

    if homework is None:
        homework = _unit_homework(unit)
    questions = homework.questions.all() if homework else []
    for index, question in enumerate(questions, start=1):
        if question_key(question) == step_slug:
            return f'Question {index}'
    if step_slug == LEARNING_IN_PUBLIC_KEY:
        return LEARNING_IN_PUBLIC_SIDEBAR_TITLE
    return step_slug


def _unit_homework(unit):
    """Return one homework row for the unit, for sidebar-title lookups."""
    from content.models.homework import Homework  # noqa: PLC0415

    return (
        Homework.objects.filter(content_id=unit.content_id)
        .prefetch_related('questions')
        .first()
    )
