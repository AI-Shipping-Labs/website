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

register = template.Library()


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
