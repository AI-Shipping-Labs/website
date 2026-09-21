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

from content.services.course_inline import inline_unit_for, inline_units_and_topics

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
        return format_html('<i data-lucide="{}" class="h-4 w-4 opacity-40"></i>', 'circle')
    return format_html(
        '<i data-lucide="{}" class="h-4 w-4 text-muted-foreground" role="img" aria-label="{}"></i>',
        unit_kind_icon(kind),
        UNIT_KIND_LABELS.get(kind, 'Lesson'),
    )
