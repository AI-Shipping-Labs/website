"""Template tag for the staff Open in Studio button."""

from django import template

from content.services.studio_open import studio_open_url as resolve_studio_open_url

register = template.Library()


@register.simple_tag(takes_context=True)
def studio_open_url(context, obj, section=''):
    """Resolve a Studio href. Missing ``section`` is the empty string."""
    if section is None:
        section = ''
    return resolve_studio_open_url(context.get('request'), obj, section)
