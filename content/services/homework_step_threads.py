"""Bind homework stepper pages to their Q&A comment threads.

On a stepper homework, the Q&A thread is bound to the current stepper page
instead of the homework unit as a whole (issue #1897). Issue #1925 narrowed
which pages carry Q&A:

- ``intro`` and every question step own one live thread (composer on).
- ``learning-in-public`` renders no Q&A at all.
- ``review`` renders no live thread; it shows the unit's own ``content_id``
  thread read-only, as the ``Earlier homework discussion`` archive. That
  unit thread holds the pre-#1897 whole-homework comments, the comments
  posted on ``intro`` while it still mounted the unit thread, and (moved by
  ``content`` migration ``0086``) the comments once posted on the
  ``review`` / ``learning-in-public`` step threads.

The non-stepper all-questions page and every lesson unit keep mounting the
unit thread with its composer.

Thread identity is a deterministic ``uuid5`` over the pair (unit's stable
content identity, public step slug), so the same unit and step keep the same
thread across re-syncs and across cohorts, and ``?cohort=`` never forks it.
The browser comments API stays UUID-keyed; pages just mount the UUID that
belongs to the current step.
"""

import uuid

from comments.models import Comment
from content.models.homework import HomeworkStepThread

# Namespace for derived step-thread UUIDs. Fixed so every environment and
# every re-sync derives the identical UUID for the same (unit, step) pair.
HOMEWORK_STEP_THREAD_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL, 'https://aishippinglabs.com/comments/homework-step-threads',
)

INTRO_STEP = 'intro'
REVIEW_STEP = 'review'
LEARNING_IN_PUBLIC_STEP = 'learning-in-public'

# Steps that used to own a live thread (issue #1897) and no longer do
# (issue #1925). Their stored rows stay registered owners; their comments
# were moved onto the unit thread by a data migration.
RETIRED_THREAD_STEPS = (REVIEW_STEP, LEARNING_IN_PUBLIC_STEP)

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
    """Return the UUID whose comments the given step page shows, or None.

    ``review`` shows the unit thread (read-only archive),
    ``learning-in-public`` shows nothing (``None``), and ``intro`` plus every
    question step show their own derived step thread.
    """
    if step_slug == REVIEW_STEP:
        return unit_content_id
    if step_slug == LEARNING_IN_PUBLIC_STEP:
        return None
    return step_thread_content_id(unit_content_id, step_slug)


def step_slugs(homework):
    """Return the public step slugs that own a live thread.

    ``intro`` plus every question's public key, in stepper order. ``review``
    and ``learning-in-public`` carry no live Q&A (issue #1925).
    """
    from content.services.homework_step_reader import (  # noqa: PLC0415
        question_key,
    )

    return [INTRO_STEP] + [
        question_key(question) for question in homework.questions.all()
    ]


def ensure_homework_step_threads(unit, homework):
    """Persist and return the thread UUID each live-Q&A stepper page mounts.

    One row per step slug keeps step threads resolvable for notifications,
    orphan detection, and the operator API -- including steps whose question
    was later removed from source (their comments stay stored). Rows are
    only created for ``intro`` and question steps; existing ``review`` /
    ``learning-in-public`` rows are left in place. Returns a mapping of each
    live-Q&A step slug to its mounted UUID string.
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
    return mounted


def unit_has_stepper_homework(unit):
    """Return whether any cohort's homework for this unit uses the stepper.

    Stepper state is authored per unit and synced to every cohort's
    ``Homework`` row, so any stepper-enabled row with questions means the
    unit renders stepper pages whose unit thread is shown on ``review`` as
    the read-only archive.
    """
    from content.models.homework import Homework  # noqa: PLC0415

    return Homework.objects.filter(
        content_id=unit.content_id,
        stepper_enabled=True,
        questions__isnull=False,
    ).exists()


def archived_unit_comment_count(unit_content_id):
    """Count the visible top-level comments of a unit's own thread.

    The Review & submit step shows the unit thread as the read-only
    ``Earlier homework discussion (N)`` archive only when N > 0. Matches the
    browser list endpoint: hidden comments and replies are not counted.
    One indexed ``COUNT`` query.
    """
    if not unit_content_id:
        return 0
    return Comment.objects.filter(
        content_id=unit_content_id,
        parent__isnull=True,
        hidden_at__isnull=True,
    ).count()


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
