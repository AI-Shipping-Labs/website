"""Studio read-only views for homework and submissions -- issue #1683, tranche 1.

Distinguishes "an operator can see submissions exist and read them"
(required so the "never lost" promise is operationally verifiable by a
human during the live cohort) from "an operator can score, re-score, or
export" (the explicitly deferred operator surface -- not built here).
"""

from django.db.models import Count
from django.shortcuts import get_object_or_404, render

from content.models import Course
from content.models.homework import Homework, HomeworkState, Submission
from studio.decorators import staff_required

# (studio_status_badge key, friendly label) per HomeworkState. Green is
# reserved for the live-positive "still accepting submissions" state;
# CLOSED and SCORED are both finished/past states, so they stay neutral
# (design-system: "finished/past/expired states are neutral").
_STATE_BADGE = {
    HomeworkState.OPEN: ('published', 'Open'),
    HomeworkState.CLOSED: ('reviewed', 'Closed'),
    HomeworkState.SCORED: ('reviewed', 'Scored'),
}


@staff_required
def homework_list(request, course_id):
    """List every ``Homework`` for a course: title, cohort, due date, state, submissions."""
    course = get_object_or_404(Course, pk=course_id)
    homeworks = (
        Homework.objects
        .filter(cohort__course=course)
        .select_related('cohort')
        .annotate(submission_count=Count('submissions'))
        .order_by('-due_date')
    )
    homework_rows = []
    for homework in homeworks:
        badge_key, state_label = _STATE_BADGE.get(
            homework.state, ('reviewed', homework.get_state_display()),
        )
        homework_rows.append({
            'homework': homework,
            'submission_count': homework.submission_count,
            'state_badge_key': badge_key,
            'state_label': state_label,
        })
    return render(request, 'studio/courses/homeworks.html', {
        'course': course,
        'homework_rows': homework_rows,
    })


@staff_required
def homework_submissions(request, homework_id):
    """Read-only list of every submission for one homework.

    Per-question answers and correctness are included -- this is the only
    place ``Answer.is_correct`` is ever rendered outside the model layer
    (see the "Why no answer_envelope" section of issue #1683). No scoring,
    re-score, or export controls.
    """
    homework = get_object_or_404(
        Homework.objects.select_related('cohort', 'cohort__course'),
        pk=homework_id,
    )
    submissions = (
        Submission.objects
        .filter(homework=homework)
        .select_related('student')
        .prefetch_related('answers__question')
        .order_by('-submitted_at')
    )
    submission_items = [
        {'submission': submission, 'answers': list(submission.answers.all())}
        for submission in submissions
    ]
    return render(request, 'studio/courses/homework_submissions.html', {
        'homework': homework,
        'course': homework.cohort.course,
        'submission_items': submission_items,
    })
