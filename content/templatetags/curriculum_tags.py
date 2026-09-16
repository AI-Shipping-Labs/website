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

register = template.Library()


@register.filter
def required_only(items):
    """Return the non-bonus items from ``items``, order preserved."""
    return [item for item in items if not getattr(item, 'is_bonus', False)]


@register.filter
def bonus_only(items):
    """Return the bonus items from ``items``, order preserved."""
    return [item for item in items if getattr(item, 'is_bonus', False)]
