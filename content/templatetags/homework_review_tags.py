"""Filters for untrusted student homework answers (Studio + Review & submit)."""

from django import template

from content.services.homework_review import render_student_answer_html

register = template.Library()


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
