"""
Template tags and filters for content access control.

Usage in templates:
    {% load access_tags %}

    {# Check if user can access content #}
    {% can_access_content user article as has_access %}
    {% if has_access %}
        {{ article.content_html|safe }}
    {% endif %}

    {# Check if content is gated (requires upgrade) #}
    {% is_gated article as gated %}

    {# Get the tier name required for content #}
    {{ article.required_level|required_tier_name }}
"""

from django import template

from content.access import (
    can_access,
    get_required_tier_label,
    get_required_tier_name,
)

register = template.Library()


@register.simple_tag
def can_access_content(user, content):
    """Check if a user can access a content object.

    Usage:
        {% can_access_content user article as has_access %}
    """
    return can_access(user, content)


@register.simple_tag
def is_gated(content):
    """Check if content requires a tier above free.

    Usage:
        {% is_gated article as gated %}
    """
    return content.required_level > 0


@register.filter
def required_tier_name(required_level):
    """Return the tier name for a required_level integer.

    Usage:
        {{ article.required_level|required_tier_name }}
    """
    return get_required_tier_name(required_level)


@register.filter
def required_tier_label(required_level):
    """Return the public-facing access label for a required_level integer.

    Issue #481: prefer this over ``required_tier_name`` on public surfaces
    (badges, paywall headings) so the copy reads "Basic or above" instead
    of the legacy "Basic+" shorthand and "Premium" without a misleading
    "or above" suffix.

    Usage:
        {{ event.required_level|required_tier_label }}
    """
    return get_required_tier_label(required_level)


@register.filter
def dict_get(dictionary, key):
    """Look up a key in a dictionary. Returns None if missing or not a dict.

    Templates can't subscript dicts directly (``dict[key]`` is not valid
    template syntax), so we expose a small filter for the common case of
    ``{value_for_id|dict_get:obj.id}``.

    Usage:
        {{ completed_count_by_module|dict_get:module.id }}
    """
    if isinstance(dictionary, dict):
        return dictionary.get(key)
    return None
