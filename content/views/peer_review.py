"""Views for peer review: submission, review dashboard, review form, certificate."""

import json

from django.http import JsonResponse, Http404, HttpResponseForbidden
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from content.access import can_access
from content.models import (
    Course, ProjectSubmission, PeerReview, CourseCertificate,
)
from content.models.cohort import CohortEnrollment
from content.services.peer_review_service import PeerReviewService


def _require_auth(request):
    """Return a redirect response if user is not authenticated, else None."""
    if not request.user.is_authenticated:
        return redirect(f'/accounts/login/?next={request.path}')
    return None


def _require_course_access(request, course):
    """Return 404 if peer review not enabled, redirect/403 if no access."""
    if not course.peer_review_enabled:
        raise Http404
    auth_redirect = _require_auth(request)
    if auth_redirect:
        return auth_redirect
    if not can_access(request.user, course):
        raise Http404
    return None


# --- User-facing pages ---


def project_submit(request, slug):
    """GET/POST /courses/<slug>/submit -- submit or update project."""
    course = get_object_or_404(Course, slug=slug, status='published')
    guard = _require_course_access(request, course)
    if guard:
        return guard

    user = request.user
    submission = ProjectSubmission.objects.filter(user=user, course=course).first()

    if request.method == 'POST':
        project_url = request.POST.get('project_url', '').strip()
        description = request.POST.get('description', '').strip()

        if not project_url:
            context = {
                'course': course,
                'submission': submission,
                'error': 'Project URL is required.',
            }
            return render(request, 'content/peer_review/submit.html', context)

        if submission:
            # Can only update while status is 'submitted'
            if submission.status != 'submitted':
                context = {
                    'course': course,
                    'submission': submission,
                    'readonly': True,
                }
                return render(request, 'content/peer_review/submit.html', context)

            submission.project_url = project_url
            submission.description = description
            submission.save(update_fields=['project_url', 'description'])
        else:
            # Check if user is in a cohort for this course
            cohort = None
            enrollment = CohortEnrollment.objects.filter(
                user=user,
                cohort__course=course,
                cohort__is_active=True,
            ).select_related('cohort').first()
            if enrollment:
                cohort = enrollment.cohort

            submission = ProjectSubmission.objects.create(
                user=user,
                course=course,
                cohort=cohort,
                project_url=project_url,
                description=description,
            )

        context = {
            'course': course,
            'submission': submission,
            'just_submitted': True,
        }
        return render(request, 'content/peer_review/submit.html', context)

    # GET
    readonly = submission and submission.status != 'submitted'
    context = {
        'course': course,
        'submission': submission,
        'readonly': readonly,
    }
    return render(request, 'content/peer_review/submit.html', context)


def review_dashboard(request, slug):
    """GET /courses/<slug>/reviews -- peer review dashboard."""
    course = get_object_or_404(Course, slug=slug, status='published')
    guard = _require_course_access(request, course)
    if guard:
        return guard

    user = request.user
    submission = ProjectSubmission.objects.filter(user=user, course=course).first()

    # Reviews assigned to this student
    assigned_reviews = []
    if submission:
        assigned_reviews = list(
            PeerReview.objects.filter(
                reviewer=user,
                submission__course=course,
            ).select_related('submission', 'submission__user')
        )

    # Reviews received on this student's submission
    received_reviews = []
    if submission and submission.status in ('review_complete', 'certified'):
        received_reviews = list(
            submission.reviews.filter(is_complete=True).select_related('reviewer')
        )

    # Certificate
    certificate = None
    if submission and submission.status == 'certified':
        certificate = CourseCertificate.objects.filter(
            user=user, course=course,
        ).first()

    # Waiting state
    waiting_for_batch = (
        submission
        and submission.status == 'submitted'
        and not assigned_reviews
    )

    context = {
        'course': course,
        'submission': submission,
        'assigned_reviews': assigned_reviews,
        'received_reviews': received_reviews,
        'certificate': certificate,
        'waiting_for_batch': waiting_for_batch,
    }
    return render(request, 'content/peer_review/dashboard.html', context)


def review_form(request, slug, submission_id):
    """GET/POST /courses/<slug>/reviews/<submission_id> -- review form."""
    course = get_object_or_404(Course, slug=slug, status='published')
    guard = _require_course_access(request, course)
    if guard:
        return guard

    user = request.user
    submission = get_object_or_404(
        ProjectSubmission, pk=submission_id, course=course,
    )

    # Check that the user is assigned to review this submission
    try:
        review = PeerReview.objects.get(submission=submission, reviewer=user)
    except PeerReview.DoesNotExist:
        return HttpResponseForbidden('You are not assigned to review this submission')

    if request.method == 'POST' and not review.is_complete:
        score_raw = request.POST.get('score', '').strip()
        feedback = request.POST.get('feedback', '').strip()

        if not feedback:
            context = {
                'course': course,
                'submission': submission,
                'review': review,
                'error': 'Feedback is required.',
            }
            return render(request, 'content/peer_review/review_form.html', context)

        score = None
        if score_raw:
            try:
                score = int(score_raw)
                if score < 1 or score > 5:
                    score = None
            except (ValueError, TypeError):
                pass

        review.score = score
        review.feedback = feedback
        review.is_complete = True
        review.completed_at = timezone.now()
        review.save(update_fields=['score', 'feedback', 'is_complete', 'completed_at'])

        # Check if all reviews for this submission are now complete
        PeerReviewService.check_and_update_submission_status(submission)

        return redirect('peer_review_dashboard', slug=course.slug)

    context = {
        'course': course,
        'submission': submission,
        'review': review,
    }
    return render(request, 'content/peer_review/review_form.html', context)


def certificate_page(request, certificate_id):
    """GET /certificates/<uuid> -- public certificate page."""
    certificate = get_object_or_404(CourseCertificate, pk=certificate_id)

    context = {
        'certificate': certificate,
        'user': certificate.user,
        'course': certificate.course,
    }
    return render(request, 'content/peer_review/certificate.html', context)


# --- API endpoints ---


@require_POST
def api_submit_project(request, slug):
    """POST /api/courses/<slug>/submit -- submit or update project."""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    course = get_object_or_404(Course, slug=slug, status='published')
    if not course.peer_review_enabled:
        return JsonResponse({'error': 'Peer review not enabled'}, status=404)
    if not can_access(request.user, course):
        return JsonResponse({'error': 'Access denied'}, status=403)

    user = request.user
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        data = {}

    project_url = data.get('project_url', '').strip()
    description = data.get('description', '').strip()

    if not project_url:
        return JsonResponse({'error': 'project_url is required'}, status=400)

    submission = ProjectSubmission.objects.filter(user=user, course=course).first()

    if submission:
        if submission.status != 'submitted':
            return JsonResponse(
                {'error': 'Cannot update submission after review has started'},
                status=400,
            )
        submission.project_url = project_url
        submission.description = description
        submission.save(update_fields=['project_url', 'description'])
    else:
        cohort = None
        enrollment = CohortEnrollment.objects.filter(
            user=user,
            cohort__course=course,
            cohort__is_active=True,
        ).select_related('cohort').first()
        if enrollment:
            cohort = enrollment.cohort

        submission = ProjectSubmission.objects.create(
            user=user,
            course=course,
            cohort=cohort,
            project_url=project_url,
            description=description,
        )

    return JsonResponse({
        'id': submission.pk,
        'status': submission.status,
        'project_url': submission.project_url,
        'description': submission.description,
    })


def api_review_dashboard(request, slug):
    """GET /api/courses/<slug>/reviews -- review dashboard data."""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    course = get_object_or_404(Course, slug=slug, status='published')
    if not course.peer_review_enabled:
        return JsonResponse({'error': 'Peer review not enabled'}, status=404)

    user = request.user
    submission = ProjectSubmission.objects.filter(user=user, course=course).first()

    data = {'submission': None, 'assigned_reviews': [], 'certificate': None}

    if submission:
        data['submission'] = {
            'id': submission.pk,
            'project_url': submission.project_url,
            'description': submission.description,
            'status': submission.status,
            'submitted_at': submission.submitted_at.isoformat(),
        }

        # Assigned reviews
        reviews = PeerReview.objects.filter(
            reviewer=user,
            submission__course=course,
        ).select_related('submission')
        data['assigned_reviews'] = [
            {
                'submission_id': r.submission.pk,
                'project_url': r.submission.project_url,
                'is_complete': r.is_complete,
            }
            for r in reviews
        ]

        # Certificate
        cert = CourseCertificate.objects.filter(user=user, course=course).first()
        if cert:
            data['certificate'] = {
                'id': str(cert.id),
                'url': cert.get_absolute_url(),
                'issued_at': cert.issued_at.isoformat(),
            }

    return JsonResponse(data)


@require_POST
def api_submit_review(request, slug, submission_id):
    """POST /api/courses/<slug>/reviews/<submission_id> -- submit review."""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    course = get_object_or_404(Course, slug=slug, status='published')
    submission = get_object_or_404(
        ProjectSubmission, pk=submission_id, course=course,
    )

    user = request.user
    try:
        review = PeerReview.objects.get(submission=submission, reviewer=user)
    except PeerReview.DoesNotExist:
        return JsonResponse(
            {'error': 'You are not assigned to review this submission'},
            status=403,
        )

    if review.is_complete:
        return JsonResponse({'error': 'Review already submitted'}, status=400)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        data = {}

    feedback = data.get('feedback', '').strip()
    if not feedback:
        return JsonResponse({'error': 'feedback is required'}, status=400)

    score = data.get('score')
    if score is not None:
        try:
            score = int(score)
            if score < 1 or score > 5:
                score = None
        except (ValueError, TypeError):
            score = None

    review.score = score
    review.feedback = feedback
    review.is_complete = True
    review.completed_at = timezone.now()
    review.save(update_fields=['score', 'feedback', 'is_complete', 'completed_at'])

    PeerReviewService.check_and_update_submission_status(submission)

    return JsonResponse({
        'id': review.pk,
        'is_complete': True,
        'score': review.score,
    })
