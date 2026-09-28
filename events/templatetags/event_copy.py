from django import template

from events.services.public_copy import strip_internal_description_notes

register = template.Library()


@register.filter
def strip_internal_notes(value):
    return strip_internal_description_notes(value or '')
