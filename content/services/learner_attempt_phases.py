"""The learner's phase (``project_phase.AttemptPhase``) for many attempts at once."""

from collections import defaultdict

from django.urls import reverse

from content.models import PeerReview, ProjectSubmission
from content.services.project_criteria import settle_completion
from content.services.project_phase import (
    FEEDBACK,
    MISSED,
    REVIEW,
    REVIEWS_URL,
    SUBMIT_URL,
    WAITING,
    attempt_phase,
)
from content.services.project_results import project_result


def learner_submissions(user, projects):
    """``{project_pk: submission}`` for the learner's own submissions."""
    return {
        s.course_project_id: s
        for s in ProjectSubmission.objects.filter(user=user, course_project__in=projects)
    }


def assigned_by_project(user, projects):
    """``{project_pk: [PeerReview, ...]}`` the learner must complete, in pk order."""
    assigned = defaultdict(list)
    reviews = PeerReview.objects.filter(
        reviewer=user, submission__course_project__in=projects,
    ).select_related('submission').order_by('pk')
    criteria = {project.pk: project.evaluation_criteria for project in projects}
    for review in reviews:
        project_id = review.submission.course_project_id
        settle_completion([review], criteria.get(project_id))
        assigned[project_id].append(review)
    return assigned


def _received_by_submission(submissions, criteria_by_project):
    received = defaultdict(list)
    reviews = PeerReview.objects.filter(
        submission__in=submissions, is_complete=True,
    ).order_by('pk')
    submission_project = {s.pk: s.course_project_id for s in submissions}
    for review in reviews:
        settle_completion([review], criteria_by_project.get(submission_project[review.submission_id]))
        if review.is_complete:
            received[review.submission_id].append(review)
    return received


def _result(project, submission, received, assigned, passing_score):
    if submission is None:
        return None
    done = sum(1 for review in assigned if review.is_complete)
    reviewed_enough = bool(assigned) and done == len(assigned)
    return project_result(
        project.evaluation_criteria, received.get(submission.pk, []),
        passing_score, reviewed_enough,
    )


def learner_attempt_phases(user, course, projects, now, *, submissions=None, assigned=None):
    """``{project.pk: AttemptPhase}`` for ``user`` across ``projects``."""
    projects = list(projects)
    if not projects:
        return {}
    received = {}
    if not getattr(user, 'is_authenticated', False):
        submissions, assigned = {}, {}
    if submissions is None:
        submissions = learner_submissions(user, projects)
    if assigned is None:
        assigned = assigned_by_project(user, projects)
    if submissions:
        received = _received_by_submission(
            list(submissions.values()),
            {project.pk: project.evaluation_criteria for project in projects},
        )
    passing_score = course.project_passing_score or 0
    phases = {}
    for project in projects:
        submission = submissions.get(project.pk)
        project_assigned = assigned.get(project.pk, [])
        result = _result(project, submission, received, project_assigned, passing_score)
        phases[project.pk] = attempt_phase(project, submission, project_assigned, now, result)
    return phases


def phase_url(course, project, phase):
    """Row link: the reviews page in review/feedback, else the submit page."""
    url_name = SUBMIT_URL
    if phase.action_url_name == REVIEWS_URL:
        url_name = REVIEWS_URL
    return reverse(url_name, kwargs={'slug': course.slug, 'attempt_slug': project.slug})


def _phase_when(project, phase):
    """Submission deadline while submitting, review deadline afterwards."""
    if project.is_self_paced:
        return None
    if phase.deadline is not None:
        return phase.deadline
    if phase.key in (WAITING, REVIEW, FEEDBACK):
        return project.review_due_at
    return project.submission_due_at


def phase_row_fields(course, project, phase):
    """Badge, tone, link, action and date of one attempt row on course home."""
    url, action = '', ''
    if phase.key != MISSED:
        url = phase_url(course, project, phase)
        action = phase.action_label or 'View project'
    return {
        'status': phase.badge_label,
        'status_tone': phase.badge_tone,
        'url': url,
        'action': action,
        'when': _phase_when(project, phase),
    }
