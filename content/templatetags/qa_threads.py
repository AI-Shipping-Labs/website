"""Template helpers for binding a page's Q&A thread to the current view.

Issue #1897: homework stepper pages mount the comment thread UUID that
belongs to the current step instead of the whole unit's ``content_id``.
"""

from django import template

register = template.Library()


@register.filter
def qa_content_id(step_content_ids, step):
    """Return the thread UUID mounted for ``step`` from the view's mapping.

    ``step_content_ids`` is the ``homework_step_qa_content_ids`` mapping the
    course unit view builds (step slug -> UUID string, ``intro`` mapped to
    the unit's own ``content_id``). Unknown steps resolve to ``''`` so the
    template falls back to the unit thread, preserving today's behaviour
    whenever a stepper page renders without a step mapping.
    """
    if not isinstance(step_content_ids, dict) or not step:
        return ''
    return step_content_ids.get(step, '')
