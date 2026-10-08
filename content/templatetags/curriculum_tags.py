"""Bonus-content grouping filters for curriculum templates (issue #1674).

Bonus modules/units must be visually grouped or separated from required
content rather than silently interleaved (owner's explicit requirement),
while the underlying navigation/reading order stays untouched. These
filters let a template render required siblings first, then bonus
siblings, from the SAME prefetched list/queryset with no extra query —
``.all()`` on an already-prefetched related manager returns from cache,
and these filters just partition that cached Python list.
"""

from django import template
from django.utils.html import format_html

from content.services.course_inline import (
    inline_unit_for,
    inline_units_and_topics,
    inline_units_for,
)

register = template.Library()


@register.filter
def required_only(items):
    """Return the non-bonus items from ``items``, order preserved."""
    return [item for item in items if not getattr(item, 'is_bonus', False)]


@register.filter
def bonus_only(items):
    """Return the bonus items from ``items``, order preserved."""
    return [item for item in items if getattr(item, 'is_bonus', False)]


@register.filter
def leaf_unit_count(children):
    """Count units below a parent module from its prefetched child modules."""
    return sum(len(child.units.all()) for child in children)


@register.filter
def topic_course_modules(children, course_slug):
    """Topic modules after presentation-only wrappers are removed."""
    return inline_units_and_topics(children, course_slug)[1]


@register.filter
def inline_course_unit(module, course_slug):
    """Return a one-page topic's unit for ordered syllabus rendering."""
    return inline_unit_for(module, course_slug)


@register.filter
def inline_course_units(module, course_slug):
    """Return lesson rows shown directly under a Buildcamp week."""
    return inline_units_for(module, course_slug)


UNIT_KIND_ICONS = {
    'lesson': 'book-open',
    'event': 'calendar',
    'homework': 'clipboard-list',
    'checklist_item': 'list-checks',
}
UNIT_KIND_LABELS = {
    'event': 'Event',
    'homework': 'Homework',
    'checklist_item': 'Checklist item',
}


@register.filter
def unit_kind_icon(kind):
    """Return the established Lucide icon for a course unit kind."""
    return UNIT_KIND_ICONS.get(kind, 'book-open')


@register.filter
def unit_nav_marker_html(kind):
    """Use a type icon in reader navigation while preserving its row scale."""
    if kind == 'lesson':
        return format_html('<i data-lucide="{}" class="h-4 w-4 text-muted-foreground" aria-hidden="true"></i>', 'file-text')
    return format_html(
        '<i data-lucide="{}" class="h-4 w-4 text-muted-foreground" role="img" aria-label="{}"></i>',
        unit_kind_icon(kind),
        UNIT_KIND_LABELS.get(kind, 'Lesson'),
    )


@register.filter
def unit_nav_marker_kind(unit, completed_unit_ids):
    """Return the `_list_row.html` marker kind for a reader nav unit row.

    A completed unit always shows the completion tick, including the
    currently selected row; everything else keeps its type icon
    (``marker_kind="custom"`` + ``unit_nav_marker_html``).
    """
    if completed_unit_ids and unit.pk in completed_unit_ids:
        return 'check'
    return 'custom'


@register.filter
def homework_step_nav_marker_html(title):
    """Use a type icon; the visible link text supplies the accessible label."""
    normalized_title = (title or '').strip().casefold()
    if normalized_title == 'introduction':
        icon = 'book-open'
    elif normalized_title == 'review & submit':
        icon = 'clipboard-check'
    else:
        icon = 'help-circle'
    return format_html(
        '<i data-lucide="{}" class="h-4 w-4 text-muted-foreground" aria-hidden="true"></i>',
        icon,
    )


def _has_review_answer(answer):
    if isinstance(answer, str):
        return bool(answer.strip())
    return bool(answer)


@register.filter
def homework_nav_steps(stepper):
    """Return ``(title, url, current, answered)`` for each stepper nav step.

    Issue #1924: a question step is "answered" exactly when the Review &
    submit rows (``stepper.review_display_rows``) show a non-blank answer
    for it -- the saved draft while open, the accepted snapshot when closed
    or scored with a submission. Nav steps and review rows are both built
    by the same ``_step_url`` call, so they match by URL. Introduction and
    Review & submit have no review row and are never answered. Derived from
    data the stepper render already holds: no queries.
    """
    if not stepper:
        return []
    answered_urls = {
        row.get('url')
        for row in stepper.get('review_display_rows') or ()
        if _has_review_answer(row.get('answer'))
    }
    return [
        (title, url, current, url in answered_urls)
        for title, url, current in stepper.get('nav_steps') or ()
    ]
