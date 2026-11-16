"""Data for the learner's reviews page of one attempt (CMP's eval + results)."""

from django.urls import reverse

from content.models import CourseCertificate, PeerReview, ProjectSubmission
from content.services.peer_pseudonym import peer_pseudonym
from content.services.peer_review_form import evaluation_form
from content.services.project_criteria import settle_completion
from content.services.project_phase import attempt_phase
from content.services.project_results import project_result


def assigned_reviews_for(user, project):
    return settle_completion(PeerReview.objects.filter(
        reviewer=user, submission__course_project=project,
    ).select_related('submission').order_by('pk'), project.evaluation_criteria)


def _evaluation_rows(assigned, course):
    rows = []
    for review in assigned:
        status_key, status_label = 'pending', 'To review'
        if review.is_complete:
            status_key, status_label = 'review_complete', 'Reviewed'
        rows.append({
            'review': review,
            'submission_id': review.submission_id,
            'author_label': peer_pseudonym(review.submission.user_id, course.pk),
            'status_key': status_key,
            'status_label': status_label,
        })
    return rows


def peer_feedback_rows(received):
    """Non-empty comments as "Reviewer 1..n" in review order."""
    rows = []
    for number, review in enumerate(received, start=1):
        if review.feedback.strip():
            rows.append({'label': f'Reviewer {number}', 'feedback': review.feedback})
    return rows


def _certificate(user, course, submission):
    if submission is None or submission.status != 'certified':
        return None
    return CourseCertificate.objects.filter(user=user, course=course).first()


def attempt_reviews_context(user, course, project, now):
    submission = ProjectSubmission.objects.filter(user=user, course_project=project).first()
    assigned = assigned_reviews_for(user, project)
    received = []
    if submission is not None:
        received = [
            review for review in settle_completion(
                submission.reviews.filter(is_complete=True).order_by('pk'),
                project.evaluation_criteria,
            ) if review.is_complete
        ]
    reviews_done = sum(1 for review in assigned if review.is_complete)
    reviewed_enough = bool(assigned) and reviews_done == len(assigned)
    result = None
    if submission is not None:
        result = project_result(
            project.evaluation_criteria, received,
            course.project_passing_score or 0, reviewed_enough,
        )
    phase = attempt_phase(project, submission, assigned, now, result)
    return {
        'submission': submission,
        'phase': phase,
        'evaluation_rows': _evaluation_rows(assigned, course),
        'next_pending_url': next_pending_url(course, project, assigned),
        'project_result': result,
        'peer_feedback': peer_feedback_rows(received),
        'certificate': _certificate(user, course, submission),
        'review_open': project.is_review_open(now),
    }


def _evaluation_url(course, project, review):
    return reverse('course_project_review_form', kwargs={
        'slug': course.slug, 'attempt_slug': project.slug,
        'submission_id': review.submission_id,
    })


def _reviews_url(course, project):
    return reverse('course_project_reviews', kwargs={
        'slug': course.slug, 'attempt_slug': project.slug,
    })


def next_pending_url(course, project, assigned):
    """The first unfinished evaluation, else the attempt's reviews page."""
    for review in assigned:
        if not review.is_complete:
            return _evaluation_url(course, project, review)
    return _reviews_url(course, project)


def after_save_url(course, project, assigned, review):
    """Where Save & continue goes: the next pending review, else the list."""
    position = [r.pk for r in assigned].index(review.pk)
    ordered = assigned[position + 1:] + assigned[:position]
    for other in ordered:
        if not other.is_complete:
            return _evaluation_url(course, project, other)
    return _reviews_url(course, project)


def _step_urls(course, project, assigned, review):
    """Previous / next review in assignment order ('' at either end)."""
    position = [r.pk for r in assigned].index(review.pk)
    previous_url, next_url = '', ''
    if position > 0:
        previous_url = _evaluation_url(course, project, assigned[position - 1])
    if position + 1 < len(assigned):
        next_url = _evaluation_url(course, project, assigned[position + 1])
    return {
        'previous_review_url': previous_url,
        'next_review_url': next_url,
        'reviews_url': _reviews_url(course, project),
        'has_next_pending': after_save_url(course, project, assigned, review)
        != _reviews_url(course, project),
    }


def review_form_context(user, course, project, review, now):
    """The evaluation form: peer pseudonym, progress, prefilled choices."""
    submission = review.submission
    own_submission = ProjectSubmission.objects.filter(user=user, course_project=project).first()
    assigned = assigned_reviews_for(user, project)
    review_open = project.is_review_open(now)
    return {
        **_step_urls(course, project, assigned, review),
        'assigned_reviews': assigned,
        'submission': submission,
        'review': review,
        'phase': attempt_phase(project, own_submission, assigned, now),
        'author_label': peer_pseudonym(submission.user_id, course.pk),
        'evaluation': evaluation_form(
            project.evaluation_criteria, review.criteria_scores,
            readonly=not review_open,
        ),
        'feedback_value': review.feedback,
        'review_open': review_open,
        'deadline_passed': not review_open,
    }
