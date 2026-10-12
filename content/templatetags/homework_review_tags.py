"""Filters for untrusted student homework answers (Studio + Review & submit)."""

from django import template

from content.services.homework_review import render_student_answer_html

register = template.Library()

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
