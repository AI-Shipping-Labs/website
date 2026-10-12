"""Studio (and future API) homework review: authored Question N order and titles.

Display-only. Public homework numbers steps from ``qN-...`` identities and
``## Question N`` markdown headings; Studio must list answers in that same
order with ``Question N: {short name} ({score} point/points)`` headings.
"""

import re

from django.utils.safestring import mark_safe

from content.models import Unit
from content.models.homework import QuestionType
from content.services.homework_step_sections import (
    QUESTION_ID,
    question_heading_names,
)
from content.utils.linkify import linkify_urls
from content.utils.markdown import render_markdown, sanitize_html

_ALREADY_NUMBERED = re.compile(
    r'^Question\s+(\d+)(?:[.:-]|\s|$)', re.IGNORECASE,
)
_FREE_TEXT_TYPES = {QuestionType.FREE_FORM, QuestionType.FREE_FORM_LONG}


def render_student_answer_html(text):
    """Render untrusted student free-text with the homework-prompt pipeline.

    Mermaid and event widgets stay off: those run on authored course copy,
    not on a student's answer. ``<script>`` is stripped by ``sanitize_html``.
    """
    if not (text or '').strip():
        return ''
    return mark_safe(sanitize_html(linkify_urls(render_markdown(
        text,
        include_mermaid=False,
        include_event_widget=False,
    ))))


def is_presentable_answer(question, answer):
    """True when this answer is a free-text slide with non-empty text."""
    if answer is None or question is None:
        return False
    if question.question_type not in _FREE_TEXT_TYPES:
        return False
    return bool((answer.answer_text or '').strip())


def authored_question_number(question):
    """Public 1-based number from ``qN-...``, or None when the id has no N."""
    match = QUESTION_ID.fullmatch((question.source_question_id or '').strip())
    if match is None:
        return None
    return int(match.group(1))


def sort_authored_questions(questions):
    """Return questions in public Question N order, then pk for unnumbered rows."""

    def key(question):
        number = authored_question_number(question)
        if number is None:
            return (1, question.pk, question.pk)
        return (0, number, question.pk)

    return sorted(questions, key=key)


def points_label(score):
    """``1 point`` vs ``N points``, including ``0 points``."""
    return '1 point' if score == 1 else f'{score} points'


def format_review_heading(number, short_name, score):
    """Build ``Question N: {short name} ({score} point/points)``.

    Do not prefix twice when ``short_name`` already starts with ``Question N``.
    """
    name = (short_name or '').strip()
    points = points_label(score)
    already = _ALREADY_NUMBERED.match(name)
    if already is not None and int(already.group(1)) == number:
        return f'{name} ({points})'
    if not name:
        return f'Question {number} ({points})'
    return f'Question {number}: {name} ({points})'


def unit_homework_markdown(homework):
    """Earliest same-course unit markdown for ``homework.content_id``."""
    content_id = homework.content_id
    if content_id is None:
        return ''
    units = list(
        Unit.objects
        .filter(source_content_id=content_id)
        .select_related('module', 'module__parent')
    )
    course_id = getattr(getattr(homework, 'cohort', None), 'course_id', None)
    if course_id is not None:
        units = [unit for unit in units if unit.module.course_id == course_id]
    if not units:
        return ''
    return min(units, key=_unit_syllabus_position).homework or ''


def review_questions(homework, markdown=None):
    """Authored-order question rows with Studio headings and supporting copy."""
    questions = sort_authored_questions(list(homework.questions.all()))
    if markdown is None:
        markdown = unit_homework_markdown(homework)
    names = question_heading_names(markdown)
    rows = []
    for index, question in enumerate(questions, start=1):
        number = authored_question_number(question) or index
        stored = (question.text or '').strip()
        short_name = names.get(number) or stored
        rows.append({
            'question': question,
            'number': number,
            'short_name': short_name,
            'heading': format_review_heading(
                number, short_name, question.scores_for_correct_answer,
            ),
            'supporting_text': stored if stored != short_name else '',
        })
    return rows


def bind_review_rows(question_rows, submission):
    """Attach this submission's answers onto authored-order question rows."""
    answers = {
        answer.question_id: answer for answer in submission.answers.all()
    }
    rows = []
    for item in question_rows:
        question = item['question']
        answer = answers.get(question.pk)
        unanswered = answer is None
        presentable = is_presentable_answer(question, answer)
        if unanswered:
            answer_html = ''
        elif question.question_type in _FREE_TEXT_TYPES:
            answer_html = render_student_answer_html(answer.answer_text or '')
        else:
            answer_html = ''
        rows.append({
            **item,
            'answer': answer,
            'unanswered': unanswered,
            'presentable': presentable,
            'answer_html': answer_html,
        })
    return rows


def submission_review_items(homework, submissions):
    """Build Studio ``submission_items`` with authored-order ``review_rows``.

    A homework with questions still yields a row per question when the
    student answered none. An empty submissions list stays empty so the
    page can keep its fresh empty state.
    """
    submissions = list(submissions)
    if not submissions:
        return []
    questions = review_questions(homework)
    return [
        {
            'submission': submission,
            'review_rows': bind_review_rows(questions, submission),
        }
        for submission in submissions
    ]


def _unit_syllabus_position(unit):
    module = unit.module
    parent = module.parent
    if parent is None:
        return (module.sort_order, module.pk, 0, 0, 0, unit.sort_order, unit.pk)
    return (
        parent.sort_order, parent.pk, 1, module.sort_order, module.pk,
        unit.sort_order, unit.pk,
    )
