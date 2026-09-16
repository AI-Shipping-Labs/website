"""Auto-scoring for homework answers -- issue #1683, tranche 1.

Ported from ``community_base.coursework.answer_checks`` (unchanged logic,
donor names kept: multiple-choice/checkboxes compare 1-based option
indices; typed free-form answers are checked per ``answer_type``).
"""

from content.models.homework import AnswerType, QuestionType


def is_answer_correct(question, answer_text):
    """Return whether ``answer_text`` matches ``question.correct_answer``.

    ``answer_text`` is the raw stored/submitted string (comma-separated
    1-based indices for checkboxes, a plain value otherwise). An empty
    answer is never correct.
    """
    if answer_text is None or not answer_text.strip():
        return False

    answer_text = answer_text.strip()
    correct_answer = (question.correct_answer or '').strip()

    if question.question_type == QuestionType.MULTIPLE_CHOICE:
        return answer_text == correct_answer

    if question.question_type == QuestionType.CHECKBOXES:
        given = {v.strip() for v in answer_text.split(',') if v.strip()}
        expected = {v.strip() for v in correct_answer.split(',') if v.strip()}
        return bool(expected) and given == expected

    # FREE_FORM / FREE_FORM_LONG -- checked per answer_type.
    if question.answer_type == AnswerType.ANY:
        return bool(answer_text)
    if question.answer_type == AnswerType.FLOAT:
        try:
            return float(answer_text) == float(correct_answer)
        except (TypeError, ValueError):
            return False
    if question.answer_type == AnswerType.INTEGER:
        try:
            return int(answer_text) == int(correct_answer)
        except (TypeError, ValueError):
            return False
    if question.answer_type == AnswerType.EXACT_STRING:
        return answer_text == correct_answer
    if question.answer_type == AnswerType.CONTAINS_STRING:
        return correct_answer.lower() in answer_text.lower()

    return False


def score_answer(question, answer_text):
    """Return the score earned for one answer: ``scores_for_correct_answer`` or 0."""
    return question.scores_for_correct_answer if is_answer_correct(question, answer_text) else 0
