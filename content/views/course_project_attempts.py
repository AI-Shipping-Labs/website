"""Learner pages for dated course project attempts."""

from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from content.models import Course, CourseCertificate, PeerReview, ProjectSubmission
from content.models.cohort import CohortEnrollment
from content.models.peer_review import CourseProject
from content.services.course_schedule import (
    schedule_timezone_name,
)
from content.services.peer_review_service import PeerReviewService
from content.services.project_form_adapter import (
    REMOVAL_UNAVAILABLE_MESSAGE,
    build_submission_form,
    certificate_cohort,
    lookup_curriculum_enrollment,
    submission_cohort,
)
from content.views.peer_review import _require_course_access
from events.services.display_time import should_use_browser_timezone


def _schedule_zone_context(course, user):
    """Display zone for attempt dates, shared with the syllabus (#1795)."""
    return {
        'schedule_timezone': schedule_timezone_name(course, user),
        'schedule_can_localize': should_use_browser_timezone(user),
    }


def _attempt(request, slug, attempt_slug):
    course = get_object_or_404(Course, slug=slug, status='published')
    guard = _require_course_access(request, course)
    if guard:
        return course, None, guard
    projects = CourseProject.objects.select_related('cohort').filter(
        course=course, slug=attempt_slug,
    )
    if projects.count() != 1:
        raise Http404
    project = projects.get()
    if project.cohort_id and not CohortEnrollment.objects.filter(
        user=request.user, cohort_id=project.cohort_id,
    ).exists() and not request.user.is_staff:
        raise Http404
    return course, project, None


def attempt_submit(request, slug, attempt_slug):
    """Submit or edit one attempt until its own submission deadline."""
    course, project, guard = _attempt(request, slug, attempt_slug)
    if guard:
        return guard
    submission = ProjectSubmission.objects.filter(
        user=request.user, course_project=project,
    ).first()
    deadline_passed = not project.is_submission_open(timezone.now())
    readonly = bool(deadline_passed or (submission and submission.status != 'submitted'))
    cert_cohort = certificate_cohort(request.user, course, project.cohort)
    enrollment = lookup_curriculum_enrollment(request.user, cert_cohort)
    context = _submit_context(
        request, course, project, submission, readonly,
        deadline_passed, cert_cohort, enrollment,
    )
    if request.method == 'POST':
        return _handle_attempt_submit(
            request, course, project, submission, context,
            readonly, deadline_passed, cert_cohort, enrollment,
        )
    return render(request, 'content/peer_review/submit.html', context)


def _submit_context(
    request, course, project, submission, readonly,
    deadline_passed, cert_cohort, enrollment,
):
    return {
        'course': course, 'course_project': project,
        'submission': submission, 'readonly': readonly,
        'deadline_passed': deadline_passed,
        'self_paced_project': project.is_self_paced,
        'project_form': build_submission_form(
            project=project, submission=submission,
            enrollment=enrollment, user=request.user,
            certificate_cohort=cert_cohort,
        ),
        **_schedule_zone_context(course, request.user),
    }


def _handle_attempt_submit(
    request, course, project, submission, context,
    readonly, deadline_passed, cert_cohort, enrollment,
):
    if readonly:
        context['error'] = 'The submission deadline has passed.'
        if not deadline_passed:
            context['error'] = 'Reviews have started; this submission can no longer be edited.'
        return render(request, 'content/peer_review/submit.html', context, status=403)
    if request.POST.get('action') == 'delete':
        context['error'] = REMOVAL_UNAVAILABLE_MESSAGE
        return render(request, 'content/peer_review/submit.html', context, status=403)
    form = build_submission_form(
        data=request.POST, project=project, submission=submission,
        enrollment=enrollment, user=request.user,
        cohort=submission_cohort(request.user, course, project.cohort),
        certificate_cohort=cert_cohort,
    )
    context['project_form'] = form
    if not form.is_valid():
        return render(request, 'content/peer_review/submit.html', context, status=400)
    submission, _created = form.save()
    context.update(submission=submission, just_submitted=True)
    return render(request, 'content/peer_review/submit.html', context)


def attempt_reviews(request, slug, attempt_slug):
    """Show the current learner's submission and assigned reviews for one attempt."""
    course, project, guard = _attempt(request, slug, attempt_slug)
    if guard:
        return guard
    submission = ProjectSubmission.objects.filter(
        user=request.user, course_project=project,
    ).first()
    assigned_reviews = list(PeerReview.objects.filter(
        reviewer=request.user, submission__course_project=project,
    ).select_related('submission', 'submission__user')) if submission else []
    received_reviews = list(submission.reviews.filter(
        is_complete=True,
    ).select_related('reviewer')) if submission else []
    certificate = CourseCertificate.objects.filter(
        user=request.user, course=course,
    ).first() if submission and submission.status == 'certified' else None
    return render(request, 'content/peer_review/dashboard.html', {
        'course': course, 'course_project': project,
        'submission': submission, 'assigned_reviews': assigned_reviews,
        'received_reviews': received_reviews, 'certificate': certificate,
        'waiting_for_batch': bool(submission and submission.status == 'submitted' and not assigned_reviews),
        'deadline_passed': not project.is_submission_open(timezone.now()),
        'self_paced_project': project.is_self_paced,
        **_schedule_zone_context(course, request.user),
    })


def attempt_review_form(request, slug, attempt_slug, submission_id):
    """Review an assigned submission from the selected attempt."""
    course, project, guard = _attempt(request, slug, attempt_slug)
    if guard:
        return guard
    submission = get_object_or_404(ProjectSubmission, pk=submission_id, course_project=project)
    review = PeerReview.objects.filter(submission=submission, reviewer=request.user).first()
    if review is None:
        return HttpResponseForbidden('You are not assigned to review this submission')
    context = {
        'course': course, 'course_project': project,
        'submission': submission, 'review': review,
        'deadline_passed': not project.is_review_open(timezone.now()),
        'self_paced_project': project.is_self_paced,
        **_schedule_zone_context(course, request.user),
    }
    if request.method == 'POST' and not review.is_complete:
        response = _complete_attempt_review(request, project, submission, review, context)
        if response:
            return response
        return redirect('course_project_reviews', slug=slug, attempt_slug=attempt_slug)
    return render(request, 'content/peer_review/review_form.html', context)


def _complete_attempt_review(request, project, submission, review, context):
    if not project.is_review_open(timezone.now()):
        context['error'] = 'The peer review deadline has passed.'
        return render(request, 'content/peer_review/review_form.html', context, status=403)
    feedback = request.POST.get('feedback', '').strip()
    if not feedback:
        context['error'] = 'Feedback is required.'
        return render(request, 'content/peer_review/review_form.html', context, status=400)
    score_raw = request.POST.get('score', '').strip()
    score = None
    if score_raw.isdigit() and 1 <= int(score_raw) <= 5:
        score = int(score_raw)
    review.score = score
    review.feedback = feedback
    review.is_complete = True
    review.completed_at = timezone.now()
    review.save(update_fields=['score', 'feedback', 'is_complete', 'completed_at'])
    PeerReviewService.check_and_update_submission_status(submission)
    if project.is_self_paced:
        PeerReviewService.check_certificate_eligibility(
            request.user, project.course, course_project=project,
        )
    return None
