"""``POST /api/courses/<slug>/enrollments`` with an optional ``cohort``.

Staff put a user into a specific dated cohort (by external key, the
``?cohort=`` value) through the API. The dated cohort must win over an
existing self-paced membership on course Home.
"""

import datetime
import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from content.access import LEVEL_PREMIUM
from content.models import Cohort, CohortEnrollment, Course
from content.models.enrollment import Enrollment
from payments.models import Tier
from tests.fixtures import set_membership

User = get_user_model()


class CourseEnrollmentCohortTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name='s')
        premium = Tier.objects.get(slug='premium')
        cls.alice = User.objects.create_user(email='alice@test.com', password='pw')
        cls.bob = User.objects.create_user(email='bob@test.com', password='pw')
        set_membership(cls.alice, tier=premium)
        set_membership(cls.bob, tier=premium)
        cls.course = Course.objects.create(
            title='AI Buildcamp', slug='ai-buildcamp',
            status='published', required_level=LEVEL_PREMIUM,
        )
        today = timezone.localdate()
        cls.cohort4 = Cohort.objects.create(
            course=cls.course, name='Cohort 4', external_key='4',
            start_date=today - datetime.timedelta(days=7),
            end_date=today + datetime.timedelta(days=60),
        )
        cls.self_paced = Cohort.objects.create(
            course=cls.course, name='Self-paced', mode='self_paced',
        )

    def _post(self, body):
        return self.client.post(
            f'/api/courses/{self.course.slug}/enrollments',
            data=json.dumps(body),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.staff_token.key}',
        )

    def test_single_enroll_with_cohort_creates_cohort_enrollment(self):
        response = self._post({'user_email': 'alice@test.com', 'cohort': '4'})

        self.assertEqual(response.json()['cohort_enrollments'], [{
            'user_email': 'alice@test.com',
            'cohort': '4',
            'cohort_name': 'Cohort 4',
            'created': True,
        }])
        self.assertTrue(Enrollment.objects.filter(
            course=self.course, user=self.alice, unenrolled_at__isnull=True,
        ).exists())
        self.assertTrue(CohortEnrollment.objects.filter(
            cohort=self.cohort4, user=self.alice,
        ).exists())

    def test_repeat_is_idempotent(self):
        self._post({'user_email': 'alice@test.com', 'cohort': '4'})
        response = self._post({'user_email': 'alice@test.com', 'cohort': '4'})

        body = response.json()
        self.assertEqual(body['already_enrolled'], 1)
        self.assertFalse(body['cohort_enrollments'][0]['created'])
        self.assertEqual(CohortEnrollment.objects.filter(
            cohort=self.cohort4, user=self.alice,
        ).count(), 1)

    def test_unknown_cohort_returns_400_and_enrolls_nobody(self):
        response = self._post({'user_email': 'alice@test.com', 'cohort': '99'})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'unknown_cohort')
        self.assertIn("'99'", response.json()['error'])
        self.assertFalse(Enrollment.objects.filter(user=self.alice).exists())

    def test_bulk_enroll_with_cohort(self):
        response = self._post({
            'user_emails': ['alice@test.com', 'bob@test.com', 'ghost@test.com'],
            'cohort': '4',
        })

        body = response.json()
        self.assertEqual(body['unknown_emails'], ['ghost@test.com'])
        self.assertEqual(
            [row['user_email'] for row in body['cohort_enrollments']],
            ['alice@test.com', 'bob@test.com'],
        )
        self.assertEqual(
            set(CohortEnrollment.objects.filter(cohort=self.cohort4)
                .values_list('user__email', flat=True)),
            {'alice@test.com', 'bob@test.com'},
        )

    def test_dated_cohort_wins_on_course_home_over_self_paced(self):
        CohortEnrollment.objects.create(cohort=self.self_paced, user=self.alice)

        self._post({'user_email': 'alice@test.com', 'cohort': '4'})

        self.assertTrue(CohortEnrollment.objects.filter(
            cohort=self.self_paced, user=self.alice,
        ).exists())
        self.client.force_login(self.alice)
        response = self.client.get(f'/courses/{self.course.slug}/home')
        self.assertEqual(response.context['cohort'], self.cohort4)
