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

from content.access import can_access, get_required_tier_name

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
