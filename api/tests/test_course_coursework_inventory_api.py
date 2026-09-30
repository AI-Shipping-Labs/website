"""Staff ``GET /api/courses/<slug>/coursework-inventory`` (#1696 phase 3).

One fixture course covers every section of the dry-run count report, so each
count below fails if its rule in ``content.services.coursework_inventory``
changes.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from content.models import (
    Cohort,
    CohortEnrollment,
    Course,
    CourseCertificate,
    CourseProject,
    PeerReview,
    ProjectSubmission,
)

User = get_user_model()

URL = '/api/courses/ai-buildcamp/coursework-inventory'


class CourseworkInventoryApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        today = timezone.localdate()
        cls.staff = User.objects.create_user(email='staff@test.com', is_staff=True)
        cls.staff_token = Token.objects.create(user=cls.staff, name='staff')
        # A token whose owner lost staff: tokens are only minted for staff.
        cls.member = User.objects.create_user(email='member@test.com', is_staff=True)
        cls.member_token = Token.objects.create(user=cls.member, name='member')
        cls.member.is_staff = False
        cls.member.save(update_fields=['is_staff'])

        users = {
            name: User.objects.create_user(email=f'{name}@test.com')
            for name in ('alice', 'bob', 'carol', 'dave', 'erin', 'frank', 'mallory')
        }
        cls.course = Course.objects.create(
            title='AI Buildcamp', slug='ai-buildcamp', status='published',
        )
        cohort4 = Cohort.objects.create(
            course=cls.course, name='Cohort 4', external_key='4', mode='cohort',
            start_date=today, end_date=today + datetime.timedelta(days=56),
        )
        cohort3 = Cohort.objects.create(
            course=cls.course, name='Cohort 3', external_key='3', mode='cohort',
            start_date=today - datetime.timedelta(days=120),
            end_date=today - datetime.timedelta(days=60),
        )
        self_paced = Cohort.objects.create(
            course=cls.course, name='Self-paced', mode='self_paced',
        )
        cls.cohort4 = cohort4
        for cohort, name in (
            (cohort4, 'alice'), (cohort4, 'bob'), (cohort3, 'erin'), (self_paced, 'dave'),
        ):
            CohortEnrollment.objects.create(cohort=cohort, user=users[name])

        def attempt(slug, cohort):
            return CourseProject.objects.create(
                course=cls.course, cohort=cohort, slug=slug, title=slug.title(),
                submission_due_at=now, review_due_at=now + datetime.timedelta(days=7),
            )

        attempt1 = attempt('attempt-1', cohort4)
        attempt('preview', None)
        open_attempt = attempt('open', None)

        pool_time = now - datetime.timedelta(days=2)

        def submit(name, status, description, **kwargs):
            return ProjectSubmission.objects.create(
                user=users[name], course=cls.course, status=status,
                project_url=f'https://github.com/{name}/p', description=description,
                **kwargs,
            )

        s1 = submit('alice', 'in_review', 'x', course_project=attempt1, cohort=cohort4)
        s2 = submit('bob', 'review_complete', '', course_project=attempt1, cohort=cohort4)
        # Self-paced legacy pool: no cohort and a self-paced cohort share one run.
        s3 = submit('carol', 'in_review', '   ', batch_assigned_at=pool_time,
                    review_deadline=pool_time + datetime.timedelta(days=7))
        s4 = submit('dave', 'in_review', 'y', cohort=self_paced,
                    batch_assigned_at=pool_time,
                    review_deadline=pool_time + datetime.timedelta(days=7))
        # Dated-cohort legacy batch: not a pooled group.
        submit('erin', 'in_review', 'z', cohort=cohort4,
               batch_assigned_at=now - datetime.timedelta(days=1))
        s6 = submit('frank', 'certified', 'w', course_project=open_attempt)

        for submission, reviewer, complete, score in (
            (s2, 'alice', True, 4),
            (s1, 'bob', False, None),
            (s3, 'dave', True, None),
            (s1, 'mallory', True, 5),   # mallory has no submission anywhere
            (s2, 'mallory', True, 3),   # same (reviewer, attempt) pair
            (s4, 'carol', True, 2),
            (s3, 'alice', False, None),  # alice only submitted attempt-1
        ):
            PeerReview.objects.create(
                submission=submission, reviewer=users[reviewer],
                is_complete=complete, score=score,
            )

        CourseCertificate.objects.create(user=users['frank'], course=cls.course, submission=s6)
        CourseCertificate.objects.create(user=users['alice'], course=cls.course)

        # Another course's rows must not leak into ai-buildcamp's counts.
        other = Course.objects.create(title='Other', slug='other', status='published')
        ProjectSubmission.objects.create(
            user=users['alice'], course=other, project_url='https://github.com/a/o',
        )
        CourseCertificate.objects.create(user=users['bob'], course=other)

    def _get(self, token=None, url=URL):
        return self.client.get(
            url, HTTP_AUTHORIZATION=f'Token {(token or self.staff_token).key}',
        )

    def test_counts_every_section_of_the_dry_run_report(self):
        data = self._get().json()

        self.assertEqual(data['course'], 'ai-buildcamp')
        self.assertEqual(data['cohorts']['total'], 3)
        self.assertEqual(data['cohorts']['by_mode'], {'cohort': 2, 'self_paced': 1})
        self.assertEqual(
            {c['name']: c['has_projects'] for c in data['cohorts']['items']},
            {'Cohort 3': False, 'Cohort 4': True, 'Self-paced': True},
        )

        self.assertEqual(data['enrollments']['cohorts_with_projects'], 2)
        self.assertEqual(data['enrollments']['cohort_enrollments'], 3)
        self.assertEqual(
            {c['name']: c['count'] for c in data['enrollments']['by_cohort']},
            {'Cohort 4': 2, 'Self-paced': 1},
        )

        projects = data['projects']
        self.assertEqual(projects['total'], 3)
        self.assertEqual(projects['null_cohort'], 2)
        self.assertEqual(
            [(p['external_key'], p['count']) for p in projects['by_cohort']], [('4', 1)],
        )
        self.assertEqual(
            projects['unscoped_without_submissions'], {'count': 1, 'slugs': ['preview']},
        )

        submissions = data['submissions']
        self.assertEqual(submissions['total'], 6)
        self.assertEqual(submissions['by_status'], {
            'submitted': 0, 'in_review': 4, 'review_complete': 1, 'certified': 1,
        })
        self.assertEqual(submissions['without_course_project'], 3)
        self.assertEqual(submissions['description'], {'blank': 2, 'non_blank': 4})

        self.assertEqual(data['reviews'], {
            'total': 7,
            'complete': 5,
            'incomplete': 2,
            'scored': 4,
            'unscored': 3,
            'reviewers_without_submission': {'pairs': 2, 'reviews': 3},
        })

        batches = data['batches']
        self.assertEqual(batches['legacy_pooled_groups'], 1)
        self.assertEqual(batches['submissions_in_groups'], 2)
        self.assertEqual([g['size'] for g in batches['groups']], [2])

        self.assertEqual(
            data['certificates'], {'total': 2, 'with_submission': 1, 'revoked': 0},
        )

    def test_requires_a_token(self):
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 401)

    def test_non_staff_token_is_rejected(self):
        response = self._get(self.member_token)
        self.assertEqual(response.status_code, 401)
        self.assertNotIn('submissions', response.json())

    def test_is_read_only(self):
        response = self.client.post(
            URL, HTTP_AUTHORIZATION=f'Token {self.staff_token.key}',
        )
        self.assertEqual(response.status_code, 405)

    def test_unknown_course_is_404(self):
        response = self._get(url='/api/courses/missing/coursework-inventory')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'unknown_course')
