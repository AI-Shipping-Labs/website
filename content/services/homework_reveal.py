"""When a learner may see their homework results, and what those results are.

One owner for the AISL reveal policy (issue #1696 phase 1), mirroring
``community_base.coursework.homework_reveal`` on the local ``content``
homework tables:

- A self-paced cohort's homework is scored by ``save_submission`` on
  submit, so its results are revealed as soon as the learner has
  submitted, and the learner cannot submit again.
- A dated cohort's homework reveals nothing until an operator moves it to
  ``HomeworkState.SCORED``.

Correctness comes from ``Answer.is_correct``, which ``save_submission``
sets on every save. Nothing here is ever computed for a learner without a
submission, so a draft never exposes the answer key.
"""

from community_base.homework_steps.types import QuestionResult

from content.models.homework import HomeworkState, QuestionType

CHOICE_TYPES = (QuestionType.MULTIPLE_CHOICE, QuestionType.CHECKBOXES)


def results_revealed(homework, submission):
    """True when ``submission``'s per-question results may be shown."""
    if submission is None:
        return False
    if homework.is_self_paced:
        return True
    return homework.state == HomeworkState.SCORED


def locked_after_submit(homework, submission):
    """A self-paced learner who has seen the answers cannot submit again."""
    return submission is not None and homework.is_self_paced


def correct_answer_text(question):
    """Display text of a question's correct answer.

    Choice questions store 1-based option indices (comma-separated for
    checkboxes); the display text is the matching option labels. Typed
    questions store the plain expected value.
    """
    if question.question_type not in CHOICE_TYPES:
        return question.correct_answer.strip()
    labels = question.options_list
    correct = []
    for item in question.correct_answer.split(','):
        try:
            position = int(item.strip())
        except ValueError:
            continue
        if 1 <= position <= len(labels):
            correct.append(labels[position - 1])
    return ', '.join(correct)


def question_results(homework, submission):
    """Per-question results keyed by question id, or ``None`` when hidden."""
    if not results_revealed(homework, submission):
        return None
    correct_by_question = {
        answer.question_id: answer.is_correct
        for answer in submission.answers.all()
    }
    return {
        question.pk: QuestionResult(
            correct=correct_by_question.get(question.pk, False),
            correct_answer=correct_answer_text(question),
        )
        for question in homework.questions.all()
    }
