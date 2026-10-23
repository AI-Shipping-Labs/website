"""PostgreSQL regression coverage for the self-paced review batch lock.

The rolling batch query filters ``reviews__isnull=True``, which is a LEFT
OUTER JOIN to the nullable ``PeerReview`` side, while also joining the
enrollment tables. A bare ``select_for_update()`` makes PostgreSQL raise
``NotSupportedError: FOR UPDATE cannot be applied to the nullable side of
an outer join`` and the scheduled
``content.tasks.peer_reviews.form_self_paced_review_batches`` fails every
run. SQLite drops the locking clause, so only the CI ``PostgreSQL 16
Verification`` job (``--tag=postgresql``) can catch it. The fix locks the
base table only: ``select_for_update(of=('self',))``.
"""

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, tag

from content.models import (
    Cohort,
    CohortEnrollment,
    Course,
    CourseProject,
    ProjectSubmission,
)
from content.services.peer_review_service import PeerReviewService

User = get_user_model()


@tag('core', 'postgresql')
class SelfPacedBatchLockPostgresTest(TestCase):
    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest(
                'FOR UPDATE over a nullable outer join is a PostgreSQL-only '
                'failure; SQLite drops the locking clause entirely'
            )
        self.course = Course.objects.create(
            title='Lock course', slug='lock-course',
            peer_review_enabled=True, peer_review_count=1,
        )
        self.cohort = Cohort.objects.create(
            course=self.course, name='Self-paced', mode='self_paced',
            is_active=True,
        )
        self.project = CourseProject.objects.create(
            course=self.course, cohort=self.cohort, slug='lock-project',
            title='Lock project', submission_due_at=None,
            review_due_at=None, peer_review_count=1,
        )
        self.submissions = [self._submission(number) for number in range(3)]

    def _submission(self, number):
        user = User.objects.create_user(email=f'lock-{number}@test.com')
        CohortEnrollment.objects.create(user=user, cohort=self.cohort)
        return ProjectSubmission.objects.create(
            user=user, course=self.course, cohort=self.cohort,
            course_project=self.project,
            project_url=f'https://example.com/lock/{number}',
        )

    def test_batch_claim_locks_base_table_only_on_postgres(self):
        result = PeerReviewService.form_self_paced_batches_for_project(
            self.project,
        )

        self.assertEqual(result, {'batched': 2, 'reviews_assigned': 2})
        for submission in self.submissions[:2]:
            submission.refresh_from_db()
            self.assertEqual(submission.status, 'in_review')
            self.assertEqual(submission.reviews.count(), 1)
        self.submissions[2].refresh_from_db()
        self.assertEqual(self.submissions[2].status, 'submitted')
