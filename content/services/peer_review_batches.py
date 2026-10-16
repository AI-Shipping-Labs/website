"""Review pool selection for dated, legacy, and self-paced attempts."""

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from content.models import Cohort, CourseProject, ProjectSubmission


def _add(summary, submissions, reviews):
    summary['batched'] += len(submissions)
    summary['reviews_assigned'] += reviews


def _form_dated_attempt_batches(course, assign_reviews, summary):
    projects = CourseProject.objects.filter(
        course=course,
        submission_due_at__lte=timezone.now(),
        review_due_at__gt=timezone.now(),
    ).exclude(cohort__mode='self_paced')
    for project in projects:
        waiting = ProjectSubmission.objects.filter(
            course=course,
            course_project=project,
            status='submitted',
            submitted_at__lte=project.submission_due_at,
        ).order_by('pk')
        if project.cohort_id:
            waiting = waiting.filter(cohort_id=project.cohort_id)
        for cohort_id in waiting.values_list('cohort_id', flat=True).distinct():
            submissions = list(waiting.filter(cohort_id=cohort_id))
            if len(submissions) >= 2:
                _add(summary, submissions, assign_reviews(
                    course, submissions, project=project,
                ))


def _form_legacy_cohort_batches(course, assign_reviews, summary):
    cohorts = Cohort.objects.filter(
        course=course, end_date__lte=timezone.now().date(),
    )
    for cohort in cohorts:
        submissions = list(ProjectSubmission.objects.filter(
            course=course,
            cohort=cohort,
            course_project__isnull=True,
            status='submitted',
        ))
        if len(submissions) >= 2:
            _add(summary, submissions, assign_reviews(course, submissions))


def _form_legacy_self_paced_batch(course, assign_reviews, summary):
    submissions = list(ProjectSubmission.objects.filter(
        course=course,
        course_project__isnull=True,
        status='submitted',
    ).filter(Q(cohort__isnull=True) | Q(cohort__mode='self_paced')))
    if len(submissions) >= course.peer_review_count + 1:
        _add(summary, submissions, assign_reviews(course, submissions))


def form_self_paced_project_batches(project, assign_reviews):
    """Assign complete, oldest-first microbatches for one self-paced attempt."""
    summary = {'batched': 0, 'reviews_assigned': 0}
    if not project.course.peer_review_enabled:
        return summary
    configured_count = project.peer_review_count or project.course.peer_review_count
    batch_size = configured_count + 1
    with transaction.atomic():
        waiting = list(
            ProjectSubmission.objects.select_for_update().filter(
                course=project.course,
                course_project=project,
                cohort=project.cohort,
                status='submitted',
                batch_assigned_at__isnull=True,
                reviews__isnull=True,
                user__cohort_enrollments__cohort=project.cohort,
            ).order_by('submitted_at', 'pk')
        )
        while len(waiting) >= batch_size:
            submissions, waiting = waiting[:batch_size], waiting[batch_size:]
            reviews = assign_reviews(
                project.course,
                submissions,
                project=project,
                no_deadline=True,
            )
            _add(summary, submissions, reviews)
    return summary


def form_course_batches(course, assign_reviews):
    """Form every eligible pool while preserving each pool's semantics."""
    summary = {'batched': 0, 'reviews_assigned': 0}
    _form_dated_attempt_batches(course, assign_reviews, summary)
    _form_legacy_cohort_batches(course, assign_reviews, summary)
    _form_legacy_self_paced_batch(course, assign_reviews, summary)
    projects = CourseProject.objects.select_related('course', 'cohort').filter(
        course=course,
        cohort__mode='self_paced',
        cohort__is_active=True,
    )
    for project in projects:
        result = form_self_paced_project_batches(project, assign_reviews)
        summary['batched'] += result['batched']
        summary['reviews_assigned'] += result['reviews_assigned']
    return summary
