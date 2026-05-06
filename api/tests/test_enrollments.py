"""Tests for the sprint enrollment endpoints (issue #443).

Covers GET (scope-by-bearer), POST (four-bucket bulk enroll), and
DELETE (idempotent, auto-private). Tokens are reused across the
collection / detail tests via a shared base class to keep fixtures
small.
"""

import datetime
import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
from payments.models import Tier
from plans.models import Plan, Sprint, SprintEnrollment

User = get_user_model()


def _attach_tier(user, slug):
    user.tier = Tier.objects.get(slug=slug)
    user.save(update_fields=['tier'])
    return user


class EnrollmentApiTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = _attach_tier(
            User.objects.create_user(email='m@test.com', password='pw'),
            'premium',
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name='s')
        cls.member_token = Token.objects.create(user=cls.member, name='m')
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
            min_tier_level=30,
        )

    def _auth(self, token=None):
        if token is None:
            token = self.staff_token
        return {'HTTP_AUTHORIZATION': f'Token {token.key}'}


class EnrollmentsListScopeTest(EnrollmentApiTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.alice = _attach_tier(
            User.objects.create_user(email='alice@test.com', password='pw'),
            'premium',
        )
        cls.bob = _attach_tier(
            User.objects.create_user(email='bob@test.com', password='pw'),
            'premium',
        )
        SprintEnrollment.objects.create(sprint=cls.sprint, user=cls.member)
        SprintEnrollment.objects.create(sprint=cls.sprint, user=cls.alice)
        SprintEnrollment.objects.create(sprint=cls.sprint, user=cls.bob)

    def test_staff_token_lists_every_enrollment(self):
        response = self.client.get(
            '/api/sprints/may-2026/enrollments', **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        emails = {row['user_email'] for row in response.json()['enrollments']}
        self.assertEqual(
            emails, {'m@test.com', 'alice@test.com', 'bob@test.com'},
        )

    def test_non_staff_token_only_sees_own_row(self):
        response = self.client.get(
            '/api/sprints/may-2026/enrollments',
            **self._auth(self.member_token),
        )
        self.assertEqual(response.status_code, 200)
        emails = [row['user_email'] for row in response.json()['enrollments']]
        self.assertEqual(emails, ['m@test.com'])

    def test_non_enrolled_non_staff_sees_empty_list(self):
        outsider = User.objects.create_user(
            email='out@test.com', password='pw',
        )
        token = Token.objects.create(user=outsider, name='o')
        response = self.client.get(
            '/api/sprints/may-2026/enrollments',
            HTTP_AUTHORIZATION=f'Token {token.key}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'enrollments': []})

    def test_no_token_returns_401(self):
        response = self.client.get('/api/sprints/may-2026/enrollments')
        self.assertEqual(response.status_code, 401)

    def test_unknown_sprint_returns_404(self):
        response = self.client.get(
            '/api/sprints/nope/enrollments', **self._auth(),
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'unknown_sprint')


class EnrollmentsBulkPostTest(EnrollmentApiTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.main_user = _attach_tier(
            User.objects.create_user(email='main@test.com', password='pw'),
            'main',
        )
        cls.basic_user = _attach_tier(
            User.objects.create_user(email='basic@test.com', password='pw'),
            'basic',
        )

    def _post(self, payload, *, token=None):
        return self.client.post(
            '/api/sprints/may-2026/enrollments',
            data=json.dumps(payload),
            content_type='application/json',
            **self._auth(token),
        )

    def test_returns_four_bucket_summary(self):
        # Pre-existing enrollment for member to trigger ``already_enrolled``.
        SprintEnrollment.objects.create(sprint=self.sprint, user=self.member)
        response = self._post({
            'user_emails': [
                'm@test.com', 'main@test.com', 'basic@test.com',
                'nope@nope.com',
            ],
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['enrolled'], 2)
        self.assertEqual(body['already_enrolled'], 1)
        self.assertEqual(
            sorted(body['under_tier']),
            sorted(['main@test.com', 'basic@test.com']),
        )
        self.assertEqual(body['unknown_emails'], ['nope@nope.com'])
        self.assertTrue(
            SprintEnrollment.objects.filter(
                sprint=self.sprint, user=self.main_user,
            ).exists()
        )
        self.assertTrue(
            SprintEnrollment.objects.filter(
                sprint=self.sprint, user=self.basic_user,
            ).exists()
        )

    def test_under_tier_rows_record_staff_enrolled_by(self):
        self._post({'user_emails': ['main@test.com']})
        enrollment = SprintEnrollment.objects.get(
            sprint=self.sprint, user=self.main_user,
        )
        self.assertEqual(enrollment.enrolled_by_id, self.staff.pk)

    def test_non_staff_returns_403_no_side_effects(self):
        before = SprintEnrollment.objects.count()
        response = self._post(
            {'user_emails': ['main@test.com']},
            token=self.member_token,
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'forbidden_other_user_plan')
        self.assertEqual(SprintEnrollment.objects.count(), before)

    def test_missing_user_emails_returns_422(self):
        response = self._post({})
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body['code'], 'missing_field')
        self.assertEqual(body['details']['field'], 'user_emails')

    def test_non_list_user_emails_returns_422(self):
        response = self._post({'user_emails': 'not-a-list'})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'missing_field')

    def test_unknown_sprint_returns_404(self):
        response = self.client.post(
            '/api/sprints/nope/enrollments',
            data=json.dumps({'user_emails': ['x@x.com']}),
            content_type='application/json',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'unknown_sprint')


class EnrollmentDeleteTest(EnrollmentApiTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.target = _attach_tier(
            User.objects.create_user(email='main@test.com', password='pw'),
            'main',
        )
        cls.target_token = Token.objects.create(user=cls.target, name='t')

    def test_delete_idempotent_204_when_not_enrolled(self):
        response = self.client.delete(
            '/api/sprints/may-2026/enrollments/main@test.com',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 204)

    def test_delete_removes_enrollment_and_auto_privates_plan(self):
        SprintEnrollment.objects.create(sprint=self.sprint, user=self.target)
        plan = Plan.objects.create(
            member=self.target, sprint=self.sprint, visibility='cohort',
        )
        response = self.client.delete(
            '/api/sprints/may-2026/enrollments/main@test.com',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(
            SprintEnrollment.objects.filter(
                sprint=self.sprint, user=self.target,
            ).exists()
        )
        plan.refresh_from_db()
        self.assertEqual(plan.visibility, 'private')

    def test_repeated_delete_is_204(self):
        SprintEnrollment.objects.create(sprint=self.sprint, user=self.target)
        first = self.client.delete(
            '/api/sprints/may-2026/enrollments/main@test.com',
            **self._auth(),
        )
        second = self.client.delete(
            '/api/sprints/may-2026/enrollments/main@test.com',
            **self._auth(),
        )
        self.assertEqual(first.status_code, 204)
        self.assertEqual(second.status_code, 204)

    def test_non_staff_delete_returns_403_no_side_effects(self):
        SprintEnrollment.objects.create(sprint=self.sprint, user=self.target)
        before = SprintEnrollment.objects.count()
        response = self.client.delete(
            '/api/sprints/may-2026/enrollments/main@test.com',
            **self._auth(self.target_token),
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'forbidden_other_user_plan')
        self.assertEqual(SprintEnrollment.objects.count(), before)

    def test_unknown_sprint_returns_404(self):
        response = self.client.delete(
            '/api/sprints/nope/enrollments/main@test.com',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'unknown_sprint')
