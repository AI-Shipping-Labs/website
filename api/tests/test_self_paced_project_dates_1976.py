"""Operator API serialization for date-free Self-paced projects."""

from api.tests.test_submissions_on_behalf import OnBehalfBase
from content.models import Cohort, CourseProject


class SelfPacedProjectOnBehalfTest(OnBehalfBase):
    def test_dry_run_serializes_null_deadline(self):
        cohort = Cohort.objects.create(
            course=self.course, name='Self-paced', external_key='self-paced',
            mode='self_paced',
        )
        CourseProject.objects.create(
            course=self.course, cohort=cohort,
            slug='self-paced-attempt', title='Self-paced attempt',
            submission_due_at=None, review_due_at=None,
        )
        body = {
            'kind': 'project',
            'user': self.member.email,
            'course': self.course.slug,
            'project': 'self-paced-attempt',
            'project_url': 'https://github.com/member/project',
            'dry_run': True,
        }

        response = self.post(body)

        self.assertEqual(response.json()['status'], 'would_create')
        self.assertIsNone(response.json()['project']['submission_due_at'])
