"""Service for peer review batch formation, assignment, and certificate issuance."""

import logging
from datetime import timedelta

from django.utils import timezone

from content.models import (
    Cohort,
    CourseCertificate,
    PeerReview,
    ProjectSubmission,
    Unit,
    UserCourseProgress,
)

logger = logging.getLogger(__name__)


class PeerReviewService:
    """Handles batch formation, review assignment, and certificate issuance."""

    @staticmethod
    def form_batches_for_course(course):
        """Form review batches for a course.

        Handles both cohort mode (synchronous) and self-paced mode (micro-batching).

        Returns:
            A dict with summary info: {'batched': int, 'reviews_assigned': int}
        """
        total_batched = 0
        total_reviews = 0

        # Cohort mode: check cohorts past their end date with unassigned submissions
        today = timezone.now().date()
        past_cohorts = Cohort.objects.filter(
            course=course,
            end_date__lte=today,
        )
        for cohort in past_cohorts:
            submissions = ProjectSubmission.objects.filter(
                course=course,
                cohort=cohort,
                status='submitted',
            )
            if submissions.count() >= 2:
                count = PeerReviewService._assign_reviews(
                    course, list(submissions),
                )
                total_batched += submissions.count()
                total_reviews += count

        # Self-paced mode: submissions without a cohort in 'submitted' status
        waiting = list(
            ProjectSubmission.objects.filter(
                course=course,
                cohort__isnull=True,
                status='submitted',
            )
        )
        min_needed = course.peer_review_count + 1
        if len(waiting) >= min_needed:
            count = PeerReviewService._assign_reviews(course, waiting)
            total_batched += len(waiting)
            total_reviews += count

        return {'batched': total_batched, 'reviews_assigned': total_reviews}

    @staticmethod
    def _assign_reviews(course, submissions):
        """Assign peer reviews using round-robin.

        Each student gets assigned `peer_review_count` other submissions to
        review (or fewer if there aren't enough peers).

        Returns:
            Number of PeerReview records created.
        """
        now = timezone.now()
        deadline = now + timedelta(days=course.peer_review_deadline_days)
        n = len(submissions)
        review_count = min(course.peer_review_count, n - 1)

        reviews_created = 0

        for i, submission in enumerate(submissions):
            submission.status = 'in_review'
            submission.batch_assigned_at = now
            submission.review_deadline = deadline
            submission.save(update_fields=[
                'status', 'batch_assigned_at', 'review_deadline',
            ])

        # Round-robin assignment: student i reviews submissions at
        # offsets (i+1), (i+2), ..., (i+review_count) modulo n
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

        # Send notifications
        PeerReviewService._notify_batch_ready(course, submissions, deadline)

        return reviews_created

    @staticmethod
    def _notify_batch_ready(course, submissions, deadline):
        """Send on-platform notifications when a batch is formed."""
        try:
            from notifications.models import Notification

            for submission in submissions:
                # Count how many reviews this student needs to complete
                review_count = PeerReview.objects.filter(
                    reviewer=submission.user,
                    submission__course=course,
                    is_complete=False,
                ).count()

                Notification.objects.create(
                    user=submission.user,
                    title=f'Peer reviews ready: {course.title}',
                    body=(
                        f'Your review batch has been formed. You have '
                        f'{review_count} submissions to review by '
                        f'{deadline.strftime("%B %d, %Y")}.'
                    ),
                    url=f'/courses/{course.slug}/reviews',
                    notification_type='new_content',
                )
        except Exception:
            logger.exception('Failed to send batch ready notifications')

    @staticmethod
    def check_and_update_submission_status(submission):
        """Check if all reviews for a submission are complete and update status.

        Also checks if the student has completed all their own reviews,
        and if so, triggers certificate eligibility check.
        """
        if submission.status not in ('in_review', 'review_complete'):
            return

        # Check if all reviews for this submission are complete
        all_reviews = submission.reviews.all()
        if not all_reviews.exists():
            return

        all_complete = all(r.is_complete for r in all_reviews)

        if all_complete and submission.status == 'in_review':
            submission.status = 'review_complete'
            submission.save(update_fields=['status'])

            # Notify the submitter
            try:
                from notifications.models import Notification
                Notification.objects.create(
                    user=submission.user,
                    title=f'Reviews complete: {submission.course.title}',
                    body='All peer reviews for your project are in. View your feedback.',
                    url=f'/courses/{submission.course.slug}/reviews',
                    notification_type='new_content',
                )
            except Exception:
                logger.exception('Failed to send reviews complete notification')

        # Check certificate eligibility
        PeerReviewService.check_certificate_eligibility(submission.user, submission.course)

    @staticmethod
    def check_certificate_eligibility(user, course):
        """Check if a student meets all requirements for a certificate.

        Requirements:
        1. All course units marked as completed
        2. Project is submitted
        3. All assigned peer reviews completed by the student
        4. All reviews on the student's submission are complete

        Returns:
            CourseCertificate if issued, None otherwise.
        """
        # Already has certificate?
        if CourseCertificate.objects.filter(user=user, course=course).exists():
            return None

        # 1. All units completed
        total_units = Unit.objects.filter(module__course=course).count()
        if total_units == 0:
            return None

        completed_units = UserCourseProgress.objects.filter(
            user=user,
            unit__module__course=course,
            completed_at__isnull=False,
        ).count()
        if completed_units < total_units:
            return None

        # 2. Project submitted
        try:
            submission = ProjectSubmission.objects.get(user=user, course=course)
        except ProjectSubmission.DoesNotExist:
            return None

        # 3. All assigned reviews completed by the student
        assigned_reviews = PeerReview.objects.filter(
            reviewer=user,
            submission__course=course,
        )
        if assigned_reviews.exists() and not all(r.is_complete for r in assigned_reviews):
            return None

        # 4. All reviews on the student's submission are complete
        received_reviews = submission.reviews.all()
        if not received_reviews.exists():
            return None
        if not all(r.is_complete for r in received_reviews):
            return None

        # All conditions met - issue certificate
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
    def issue_certificates_for_course(course):
        """Manually issue certificates for all eligible students in a course.

        Returns:
            Number of certificates issued.
        """
        count = 0
        submissions = ProjectSubmission.objects.filter(
            course=course,
        ).exclude(status='certified')

        for submission in submissions:
            cert = PeerReviewService.check_certificate_eligibility(
                submission.user, course,
            )
            if cert:
                count += 1

        return count
