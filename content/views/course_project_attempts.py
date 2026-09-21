"""Learner pages for dated course project attempts."""

from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from content.models import Course, CourseCertificate, PeerReview, ProjectSubmission
from content.models.cohort import CohortEnrollment
from content.models.peer_review import CourseProject
from content.services.peer_review_service import PeerReviewService
from content.views.peer_review import _require_course_access, _valid_project_url


def _attempt(request, slug, attempt_slug):
    course = get_object_or_404(Course, slug=slug, status='published')
    guard = _require_course_access(request, course)
    if guard:
        return course, None, guard
    projects = CourseProject.objects.filter(course=course, slug=attempt_slug)
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
    deadline_passed = timezone.now() >= project.submission_due_at
    readonly = bool(deadline_passed or (submission and submission.status != 'submitted'))
    context = {
        'course': course, 'course_project': project,
        'submission': submission, 'readonly': readonly,
        'deadline_passed': deadline_passed,
    }
    if request.method == 'POST':
        if readonly:
            context['error'] = 'The submission deadline has passed.' if deadline_passed else 'Reviews have started; this submission can no longer be edited.'
            return render(request, 'content/peer_review/submit.html', context, status=403)
        project_url = request.POST.get('project_url', '').strip()
        description = request.POST.get('description', '').strip()
        if not _valid_project_url(project_url):
            context['error'] = 'Project URL is required.' if not project_url else 'Enter a valid http or https project URL.'
            return render(request, 'content/peer_review/submit.html', context, status=400)
        if submission:
            submission.project_url = project_url
            submission.description = description
            submission.save(update_fields=['project_url', 'description'])
        else:
            cohort = project.cohort
            if cohort is None:
                enrollment = CohortEnrollment.objects.filter(
                    user=request.user, cohort__course=course,
                    cohort__mode='cohort', cohort__is_active=True,
                ).select_related('cohort').first()
                cohort = enrollment.cohort if enrollment else None
            submission = ProjectSubmission.objects.create(
                user=request.user, course=course, course_project=project,
                cohort=cohort, project_url=project_url, description=description,
            )
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
        'deadline_passed': timezone.now() >= project.submission_due_at,
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
        'deadline_passed': timezone.now() >= project.review_due_at,
    }
    if request.method == 'POST' and not review.is_complete:
        if timezone.now() >= project.review_due_at:
            context['error'] = 'The peer review deadline has passed.'
            return render(request, 'content/peer_review/review_form.html', context, status=403)
        feedback = request.POST.get('feedback', '').strip()
        if not feedback:
            context['error'] = 'Feedback is required.'
            return render(request, 'content/peer_review/review_form.html', context, status=400)
        score = None
        score_raw = request.POST.get('score', '').strip()
        if score_raw:
            try:
                score = int(score_raw)
                if not 1 <= score <= 5:
                    score = None
            except ValueError:
                pass
        review.score = score
        review.feedback = feedback
        review.is_complete = True
        review.completed_at = timezone.now()
        review.save(update_fields=['score', 'feedback', 'is_complete', 'completed_at'])
        PeerReviewService.check_and_update_submission_status(submission)
        return redirect('course_project_reviews', slug=slug, attempt_slug=attempt_slug)
    return render(request, 'content/peer_review/review_form.html', context)
