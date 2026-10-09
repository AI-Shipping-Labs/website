"""Template helpers for binding a page's Q&A thread to the current view.

Issue #1897: homework stepper pages mount the comment thread UUID that
belongs to the current step instead of the whole unit's ``content_id``.
Issue #1925: only ``intro`` and question steps carry a live thread;
``review`` shows the unit thread as a read-only archive and
``learning-in-public`` shows no Q&A.
"""

from django import template

from content.services.homework_step_threads import (
    LEARNING_IN_PUBLIC_STEP,
    REVIEW_STEP,
)

QA_MODE_LIVE = 'live'
QA_MODE_ARCHIVE = 'archive'
QA_MODE_NONE = 'none'

register = template.Library()


@register.filter
def qa_content_id(step_content_ids, step):
    """Return the thread UUID mounted for ``step`` from the view's mapping.

    ``step_content_ids`` is the ``homework_step_qa_content_ids`` mapping the
    course unit view builds (live-Q&A step slug -> UUID string). Unknown
    steps resolve to ``''`` so the template falls back to the unit thread
    whenever a stepper page renders without a step mapping.
    """
    if not isinstance(step_content_ids, dict) or not step:
        return ''
    return step_content_ids.get(step, '')


@register.filter
def homework_step_qa_mode(step):
    """Return how a homework stepper page shows Q&A (issue #1925).

    ``'archive'`` on ``review`` (unit thread, read-only, only when it has
    comments), ``'none'`` on ``learning-in-public``, and ``'live'`` on
    ``intro`` and every question step.
    """
    if step == REVIEW_STEP:
        return QA_MODE_ARCHIVE
    if step == LEARNING_IN_PUBLIC_STEP:
        return QA_MODE_NONE
    return QA_MODE_LIVE
