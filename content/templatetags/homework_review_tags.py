"""Filters for untrusted student homework answers (Studio + Review & submit)."""

from django import template
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.utils.html import format_html, format_html_join

from content.services.homework_review import render_student_answer_html

register = template.Library()

_HTTP_URL = URLValidator(schemes=['http', 'https'])

# community-base stepper question types whose review value is a resolved
# option label (or comma-joined labels), never student-authored text.
_OPTION_LABEL_TYPES = frozenset({'choice', 'checkbox'})


@register.simple_tag
def homework_review_row_is_option(row, assignment):
    """True when a Review & submit row shows option labels, not free text.

    Option labels stay plaintext (#1944 spec); only free-text answers go
    through the markdown pipeline. Unknown keys fall back to free text,
    which is still sanitized.
    """
    key = row.get('key') if isinstance(row, dict) else None
    for question in getattr(assignment, 'questions', None) or ():
        if question.key == key:
            return question.type in _OPTION_LABEL_TYPES
    return False


@register.filter(is_safe=True)
def homework_answer_html(value):
    """Render a free-text homework answer as sanitized HTML.

    Multiple-choice / checkbox review rows pass already-resolved option
    labels; a bare label becomes a paragraph, which is still readable as
    plaintext. Empty values stay empty so the caller can show ``Not answered``.
    """
    if not value:
        return ''
    if not isinstance(value, str):
        value = str(value)
    return render_student_answer_html(value)


@register.filter(is_safe=True)
def homework_public_links_html(value):
    """Render the newline-separated Learning in Public answer as a URL list.

    Issue #1828: Review shows each saved link on its own list item instead of
    one run-together paragraph. Draft text has not been validated yet, so
    only a valid http(s) URL becomes a link; anything else stays escaped text
    for the learner to fix before submitting.
    """
    links = [line.strip() for line in str(value or '').splitlines() if line.strip()]
    if not links:
        return ''
    items = format_html_join(
        '', '<li>{}</li>',
        ((_public_link_item(link),) for link in links),
    )
    return format_html('<ul data-testid="homework-review-public-links">{}</ul>', items)


def _public_link_item(link):
    try:
        _HTTP_URL(link)
    except ValidationError:
        return link
    return format_html(
        '<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>', link, link,
    )
