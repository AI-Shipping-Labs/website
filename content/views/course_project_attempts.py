"""Learner pages for dated course project attempts."""

from django.http import Http404, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from content.models import Course, PeerReview, ProjectSubmission
from content.models.cohort import CohortEnrollment
from content.models.peer_review import CourseProject
from content.services.attempt_nav import (
    EVALUATION_PAGE,
    REVIEWS_PAGE,
    SUBMIT_PAGE,
    AttemptNavPage,
)
from content.services.attempt_reviews import (
    after_save_url,
    assigned_reviews_for,
    attempt_reviews_context,
    review_form_context,
)
from content.services.course_schedule import (
    schedule_timezone_name,
)
from content.services.peer_review_form import (
    missing_criteria_message,
    save_draft,
    save_review,
)
from content.services.peer_review_service import PeerReviewService
from content.services.project_criteria import criteria_summary, settle_completion
from content.services.project_form_adapter import (
    REMOVAL_UNAVAILABLE_MESSAGE,
    build_submission_form,
    certificate_cohort,
    lookup_curriculum_enrollment,
    submission_cohort,
    update_commit_action,
)
from content.services.project_phase import attempt_phase
from content.services.project_reader import project_reader_context
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


def _current_phase(reader, user, project, submission):
    """The step indicator's phase: the sidebar row's, else built directly."""
    for row in reader.get('reader_project_rows', []):
        if row.is_current:
            return row.phase
    return attempt_phase(
        project, submission, assigned_reviews_for(user, project), timezone.now(),
    )


def _criteria_lesson_url(course):
    """URL of the course's Evaluation criteria page (generated from course.yaml)."""
    return reverse('course_project_criteria', kwargs={'slug': course.slug})


def _submit_context(
    request, course, project, submission, readonly,
    deadline_passed, cert_cohort, enrollment,
):
    reader = project_reader_context(
        request, course, project, nav_page=AttemptNavPage(SUBMIT_PAGE),
    )
    return {
        **reader,
        'phase': _current_phase(reader, request.user, project, submission),
        'course': course, 'course_project': project,
        'submission': submission, 'readonly': readonly,
        'project_criteria': criteria_summary(project.evaluation_criteria),
        'criteria_lesson_url': _criteria_lesson_url(course),
        'deadline_passed': deadline_passed,
        'submission_overdue': not deadline_passed and project.submission_due_at is not None
        and project.submission_due_at <= timezone.now(),
        'review_open': project.is_review_open(),
        'project_completed': project.is_completed(),
        'self_paced_project': project.is_self_paced,
        'project_form': build_submission_form(
            project=project, submission=submission,
            enrollment=enrollment, user=request.user,
            certificate_cohort=cert_cohort,
            commit_id_enabled=False, resolve_commit=True,
        ),
        **_schedule_zone_context(course, request.user),
    }


def _handle_attempt_submit(
    request, course, project, submission, context,
    readonly, deadline_passed, cert_cohort, enrollment,
):
    if readonly:
        context['error'] = 'Submissions are closed.'
        if not deadline_passed:
            context['error'] = 'Reviews have started; this submission can no longer be edited.'
        return render(request, 'content/peer_review/submit.html', context, status=403)
    if request.POST.get('action') == 'delete':
        context['error'] = REMOVAL_UNAVAILABLE_MESSAGE
        return render(request, 'content/peer_review/submit.html', context, status=403)
    if request.POST.get('action') == 'update_commit':
        return _handle_update_commit(
            request, context, submission, project, course, cert_cohort, enrollment,
        )
    form = build_submission_form(
        data=request.POST, project=project, submission=submission,
        enrollment=enrollment, user=request.user,
        cohort=submission_cohort(request.user, course, project.cohort),
        certificate_cohort=cert_cohort,
        commit_id_enabled=False, resolve_commit=True,
    )
    context['project_form'] = form
    if not form.is_valid():
        return render(request, 'content/peer_review/submit.html', context, status=400)
    submission, _created = form.save()
    context['commit_notice'] = form.commit_notice
    reader = project_reader_context(
        request, course, project, nav_page=AttemptNavPage(SUBMIT_PAGE),
    )
    context.update(
        submission=submission, just_submitted=True,
        reader_project_rows=reader.get('reader_project_rows', []),
        phase=_current_phase(reader, request.user, project, submission),
    )
    return render(request, 'content/peer_review/submit.html', context)


def _handle_update_commit(
    request, context, submission, project, course, cert_cohort, enrollment,
):
    """The "update" control: store the typed GitHub link with its latest commit."""
    if submission is None:
        context['error'] = 'Save your project first; the first save records the commit.'
        return render(request, 'content/peer_review/submit.html', context, status=400)
    form = build_submission_form(
        data=request.POST, project=project, submission=submission,
        enrollment=enrollment, user=request.user,
        cohort=submission_cohort(request.user, course, project.cohort),
        certificate_cohort=cert_cohort,
        commit_id_enabled=False, resolve_commit=True,
    )
    ok, message = update_commit_action(form, submission)
    context.update(
        project_form=form, submission=submission,
        commit_notice=message,
    )
    return render(
        request, 'content/peer_review/submit.html', context,
        status=200 if ok else 400,
    )


def attempt_reviews(request, slug, attempt_slug):
    """CMP-style evaluation list and results for one attempt, by phase."""
    course, project, guard = _attempt(request, slug, attempt_slug)
    if guard:
        return guard
    return render(request, 'content/peer_review/dashboard.html', {
        **project_reader_context(
            request, course, project, nav_page=AttemptNavPage(REVIEWS_PAGE),
        ),
        'course': course, 'course_project': project,
        **attempt_reviews_context(request.user, course, project, timezone.now()),
        'self_paced_project': project.is_self_paced,
        **_schedule_zone_context(course, request.user),
    })


def attempt_review_form(request, slug, attempt_slug, submission_id):
    """Evaluate (and, while the window is open, re-evaluate) an assigned submission."""
    course, project, guard = _attempt(request, slug, attempt_slug)
    if guard:
        return guard
    submission = get_object_or_404(ProjectSubmission, pk=submission_id, course_project=project)
    review = PeerReview.objects.filter(submission=submission, reviewer=request.user).first()
    if review is None:
        return HttpResponseForbidden('You are not assigned to review this submission')
    settle_completion([review], project.evaluation_criteria)
    context = {
        **project_reader_context(
            request, course, project,
            nav_page=AttemptNavPage(EVALUATION_PAGE, submission.pk),
        ),
        'course': course, 'course_project': project,
        **review_form_context(request.user, course, project, review, timezone.now()),
        'self_paced_project': project.is_self_paced,
        **_schedule_zone_context(course, request.user),
    }
    if request.method == 'POST' and request.POST.get('action') == 'draft':
        return _autosave_draft(request, project, review)
    if request.method == 'POST':
        response = _complete_attempt_review(request, project, submission, review, context)
        if response:
            return response
        return redirect(after_save_url(course, project, context['assigned_reviews'], review))
    return render(request, 'content/peer_review/review_form.html', context)


def _autosave_draft(request, project, review):
    """JSON autosave of the form state; a submitted review is not changed."""
    if not project.is_review_open(timezone.now()):
        return JsonResponse({'saved': False, 'error': 'closed'}, status=403)
    saved = save_draft(review, project.evaluation_criteria, request.POST)
    return JsonResponse({'saved': saved, 'submitted': review.is_complete})


def _complete_attempt_review(request, project, submission, review, context):
    if not project.is_review_open(timezone.now()):
        context['error'] = 'Peer review is not open for this project.'
        return render(request, 'content/peer_review/review_form.html', context, status=403)
    form, complete = save_review(review, project.evaluation_criteria, request.POST)
    if not complete:
        context.update(evaluation=form, feedback_value=form['feedback'], error=missing_criteria_message(form['missing_titles']))
        return render(request, 'content/peer_review/review_form.html', context, status=400)
    PeerReviewService.check_and_update_submission_status(submission)
    if project.is_self_paced:
        PeerReviewService.check_certificate_eligibility(
            request.user, project.course, course_project=project,
        )
    return None
