"""Parse, save, and auto-score homework submissions -- issue #1683, tranche 1.

The absolute requirement this module protects: a submitted answer is never
lost and never silently rejected. ``save_submission`` always
get-or-creates a single ``Submission`` row per ``(homework, student)`` and
replaces the full answer set on every save -- never partially, never
raising for a blank question.
"""

from django.db import transaction
from django.utils import timezone

from content.models.cohort import CohortEnrollment
from content.models.homework import Answer, QuestionType, Submission
from content.services.homework_scoring import is_answer_correct


def parse_submission_post(post_data, homework):
    """Build ``{question_id: answer_text}`` from a submitted ``request.POST``.

    Checkbox answers are collected via ``getlist`` and stored as a
    numerically-sorted comma-separated string of 1-based option indices, so
    storage/comparison never depends on the order options were clicked.
    Blank/unanswered questions are simply absent from the returned dict --
    there is no "answer every question" validation.
    """
    answers_by_question_id = {}
    for question in homework.questions.all():
        field_name = f'answer_{question.pk}'
        if question.question_type == QuestionType.CHECKBOXES:
            values = post_data.getlist(f'{field_name}[]')
            cleaned = {v.strip() for v in values if v.strip()}
            if not cleaned:
                continue
            answer_text = ','.join(sorted(cleaned, key=lambda v: (len(v), v)))
        else:
            answer_text = post_data.get(field_name, '').strip()
            if not answer_text:
                continue
        answers_by_question_id[question.pk] = answer_text
    return answers_by_question_id


@transaction.atomic
def save_submission(
    homework, user, *, homework_link, answers_by_question_id,
    learning_in_public_links=None, time_spent_lectures=None,
    time_spent_homework=None,
):
    """Create or update ``user``'s ``Submission`` for ``homework``.

    Auto-creates the ``CohortEnrollment`` so a missing separate "enroll"
    step never blocks a submission. Callers must confirm
    ``homework.is_accepting_submissions`` before calling this -- it does not
    re-check the deadline itself.
    """
    enrollment, _ = CohortEnrollment.objects.get_or_create(
        user=user, cohort=homework.cohort,
    )

    submission, _created = Submission.objects.get_or_create(
        homework=homework, student=user,
        defaults={'enrollment': enrollment},
    )
    submission.enrollment = enrollment
    submission.homework_link = homework_link or None
    submission.learning_in_public_links = learning_in_public_links or []
    submission.time_spent_lectures = time_spent_lectures
    submission.time_spent_homework = time_spent_homework
    submission.submitted_at = timezone.now()
    submission.save(update_fields=[
        'enrollment', 'homework_link', 'learning_in_public_links',
        'time_spent_lectures', 'time_spent_homework', 'submitted_at',
    ])

    questions_by_id = {q.pk: q for q in homework.questions.all()}
    answered_question_ids = set()
    total_score = 0

    for question_id, answer_text in answers_by_question_id.items():
        question = questions_by_id.get(question_id)
        if question is None or not answer_text:
            continue
        correct = is_answer_correct(question, answer_text)
        Answer.objects.update_or_create(
            submission=submission, question=question,
            defaults={'answer_text': answer_text, 'is_correct': correct},
        )
        answered_question_ids.add(question_id)
        if correct:
            total_score += question.scores_for_correct_answer

    # Full answer set replaced each time (issue #1683 "Submission
    # lifecycle"): a question the student blanked out on this edit loses
    # its previously-saved Answer row.
    submission.answers.exclude(question_id__in=answered_question_ids).delete()

    submission.questions_score = total_score
    submission.total_score = total_score
    submission.save(update_fields=['questions_score', 'total_score'])
    return submission
