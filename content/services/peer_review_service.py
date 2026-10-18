"""Service for peer review batch formation, assignment, and certificate issuance."""

import logging
from datetime import timedelta

from django.utils import timezone

from content.models import (
    CourseCertificate,
    PeerReview,
    ProjectSubmission,
    Unit,
    UserCourseProgress,
)
from content.models.course import UNIT_KIND_EVENT, non_bonus_units
from content.services.peer_review_batches import (
    form_course_batches,
    form_self_paced_project_batches,
)
from content.services.peer_review_notifications import notify_reviews_complete

logger = logging.getLogger(__name__)


def certificate_required_units(queryset, course_project):
    """Return units required by the certificate's qualifying attempt."""
    units = non_bonus_units(queryset)
    if course_project is not None and course_project.is_self_paced:
        return units.exclude(kind=UNIT_KIND_EVENT)
    return units


class PeerReviewService:
    """Handles batch formation, review assignment, and certificate issuance."""

    @staticmethod
    def form_batches_for_course(course):
        """Form dated, legacy, and self-paced batches for a course."""
        return form_course_batches(course, PeerReviewService._assign_reviews)

    @staticmethod
    def form_self_paced_batches_for_project(project):
        """Form complete rolling microbatches for one self-paced project."""
        return form_self_paced_project_batches(
            project, PeerReviewService._assign_reviews,
        )

    @staticmethod
    def _assign_reviews(course, submissions, project=None, *, no_deadline=False):
        """Assign peer reviews using round-robin.

        Each student gets assigned `peer_review_count` other submissions to
        review (or fewer if there aren't enough peers).

        Returns:
            Number of PeerReview records created.
        """
        now = timezone.now()
        deadline = None if no_deadline else (
            project.review_due_at if project else
            now + timedelta(days=course.peer_review_deadline_days)
        )
        n = len(submissions)
        configured_count = (
            project.peer_review_count
            if project and project.peer_review_count is not None
            else course.peer_review_count
        )
        review_count = min(configured_count, n - 1)

        PeerReviewService._prepare_submissions(submissions, now, deadline)
        reviews_created = PeerReviewService._create_review_assignments(
            submissions, review_count,
        )
        PeerReviewService._notify_batch_ready(course, submissions, deadline, project)
        return reviews_created

    @staticmethod
    def _prepare_submissions(submissions, now, deadline):
        for submission in submissions:
            submission.status = 'in_review'
            submission.batch_assigned_at = now
            submission.review_deadline = deadline
            submission.save(update_fields=[
                'status', 'batch_assigned_at', 'review_deadline',
            ])

    @staticmethod
    def _create_review_assignments(submissions, review_count):
        reviews_created = 0
        n = len(submissions)
        for i, submission in enumerate(submissions):
            for offset in range(1, review_count + 1):
                reviewer_idx = (i + offset) % n
                reviewer_submission = submissions[reviewer_idx]
                reviewer_user = reviewer_submission.user

                _, created = PeerReview.objects.get_or_create(
                    submission=submission,
                    reviewer=reviewer_user,
                )
                if created:
                    reviews_created += 1
        return reviews_created

    @staticmethod
    def _notify_batch_ready(course, submissions, deadline, project=None):
        """Send on-platform notifications when a batch is formed."""
        try:
            from notifications.models import Notification

            for submission in submissions:
                review_count = PeerReview.objects.filter(
                    reviewer=submission.user,
                    submission__course=course,
                    submission__course_project=project,
                    is_complete=False,
                ).count()

                Notification.objects.create(
                    user=submission.user,
                    title=f'Peer reviews ready: {course.title}',
                    body=PeerReviewService._batch_ready_body(
                        review_count, deadline,
                    ),
                    url=(
                        f'/courses/{course.slug}/projects/{project.slug}/reviews'
                        if project else f'/courses/{course.slug}/reviews'
                    ),
                    notification_type='new_content',
                )
        except Exception:
            logger.exception('Failed to send batch ready notifications')

    @staticmethod
    def _batch_ready_body(review_count, deadline):
        if deadline is None:
            return (
                f'Your review group is ready. You have {review_count} '
                'submissions to review. Reviews remain open until completed.'
            )
        return (
            f'Your review batch has been formed. You have {review_count} '
            f'submissions to review by {deadline.strftime("%B %d, %Y")}.'
        )

    @staticmethod
    def check_and_update_submission_status(submission):
        """Update review status and check project-scoped certificate eligibility."""
        if submission.status not in ('in_review', 'review_complete'):
            return
        all_reviews = submission.reviews.all()
        if not all_reviews.exists():
            return
        all_complete = all(r.is_complete for r in all_reviews)
        if all_complete and submission.status == 'in_review':
            submission.status = 'review_complete'
            submission.save(update_fields=['status'])
            notify_reviews_complete(submission)
        PeerReviewService.check_certificate_eligibility(
            submission.user, submission.course, submission.course_project,
        )

    @staticmethod
    def check_certificate_eligibility(user, course, course_project=None):
        """Check if a student meets all requirements for a certificate.

        Requirements:
        1. All course units marked as completed
        2. Project is submitted
        3. All assigned peer reviews completed by the student
        4. All reviews on the student's submission are complete

        Returns:
            CourseCertificate if issued, None otherwise.
        """
        if CourseCertificate.objects.filter(user=user, course=course).exists():
            return None
        submission = PeerReviewService._qualifying_submission(
            user, course, course_project,
        )
        if submission is None:
            return None
        if not PeerReviewService._all_certificate_units_complete(
            user, course, submission.course_project,
        ):
            return None
        return PeerReviewService._issue_certificate(user, course, submission)

    @staticmethod
    def _issue_certificate(user, course, submission):
        certificate = CourseCertificate.objects.create(
            user=user,
            course=course,
            submission=submission,
        )

        # Update submission status
        submission.status = 'certified'
        submission.certificate_issued_at = timezone.now()
        submission.save(update_fields=['status', 'certificate_issued_at'])

        # Notify
        try:
            from notifications.models import Notification
            Notification.objects.create(
                user=user,
                title=f'Certificate earned: {course.title}',
                body='Congratulations! You have earned your certificate of completion.',
                url=certificate.get_absolute_url(),
                notification_type='new_content',
            )
        except Exception:
            logger.exception('Failed to send certificate notification')

        return certificate

    @staticmethod
    def _all_certificate_units_complete(user, course, course_project):
        course_units = Unit.objects.filter(module__course=course)
        units = certificate_required_units(course_units, course_project)
        total = units.count()
        if total == 0:
            return False
        completed = UserCourseProgress.objects.filter(
            user=user, unit__in=units, completed_at__isnull=False,
        ).count()
        return completed >= total

    @staticmethod
    def _qualifying_submission(user, course, course_project):
        submissions = ProjectSubmission.objects.filter(
            user=user, course=course,
        ).select_related('course_project', 'course_project__cohort')
        if course_project is not None:
            submissions = submissions.filter(course_project=course_project)
        for submission in submissions.order_by('submitted_at', 'pk'):
            if PeerReviewService._reviews_complete(user, course, submission):
                return submission
        return None

    @staticmethod
    def _reviews_complete(user, course, submission):
        assigned = PeerReview.objects.filter(
            reviewer=user,
            submission__course=course,
            submission__course_project=submission.course_project,
        )
        received = submission.reviews.all()
        if assigned.filter(is_complete=False).exists():
            return False
        if not received.exists() or received.filter(is_complete=False).exists():
            return False
        project = submission.course_project
        if not project or not project.is_self_paced:
            return True
        required = project.peer_review_count or course.peer_review_count
        return assigned.count() >= required and received.count() >= required

    @staticmethod
    def issue_certificates_for_course(course):
        """Manually issue certificates for every eligible course learner."""
        count = 0
        submissions = ProjectSubmission.objects.filter(
            course=course,
        ).exclude(status='certified')

        for submission in submissions:
            cert = PeerReviewService.check_certificate_eligibility(
                submission.user, course, submission.course_project,
            )
            if cert:
                count += 1

        return count
