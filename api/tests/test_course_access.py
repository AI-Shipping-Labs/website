"""``POST /api/courses/<slug>/access`` and ``DELETE .../access/<email>``.

Staff grant ``CourseAccess(granted)`` by email list or by dated cohort,
and revoke only granted rows, through the same service as Studio.
"""

import datetime
import json

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.utils import timezone

from accounts.models import EmailAlias, Token
from content.access import LEVEL_PREMIUM
from content.models import Cohort, CohortEnrollment, Course, CourseAccess

User = get_user_model()


class CourseAccessApiBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name='s')
        cls.course = Course.objects.create(
            title='AI Buildcamp', slug='ai-buildcamp',
            status='published', required_level=LEVEL_PREMIUM,
        )
        cls.alice = User.objects.create_user(email='alice@test.com', password='pw')
        cls.buyer = User.objects.create_user(email='buyer@test.com', password='pw')
        CourseAccess.objects.create(
            user=cls.buyer, course=cls.course, access_type='purchased',
        )
        today = timezone.localdate()
        cls.cohort1 = Cohort.objects.create(
            course=cls.course, name='Cohort 1', external_key='1',
            start_date=today - datetime.timedelta(days=200),
            end_date=today - datetime.timedelta(days=100),
        )

    def _auth(self):
        return {'HTTP_AUTHORIZATION': f'Token {self.staff_token.key}'}

    def _post(self, body, slug='ai-buildcamp'):
        return self.client.post(
            f'/api/courses/{slug}/access',
            data=json.dumps(body),
            content_type='application/json',
            **self._auth(),
        )

    def _delete(self, email):
        return self.client.delete(
            f'/api/courses/{self.course.slug}/access/{email}', **self._auth(),
        )


class CourseAccessGrantByEmailTest(CourseAccessApiBase):
    def test_grants_per_email_with_statuses_and_granted_by(self):
        carol = User.objects.create_user(email='carol@test.com', password='pw')
        EmailAlias.objects.create(user=carol, email='carol.alt@test.com')

        response = self._post({'emails': [
            'Alice@Test.com', 'buyer@test.com', 'carol.alt@test.com',
            'ghost@test.com', 'not-an-email', 'alice@test.com',
        ]})

        body = response.json()
        self.assertEqual(body['results'], [
            {'email': 'alice@test.com', 'status': 'granted', 'access_type': 'granted'},
            {'email': 'buyer@test.com', 'status': 'already_has_access',
             'access_type': 'purchased'},
            {'email': 'carol.alt@test.com', 'status': 'granted',
             'access_type': 'granted'},
            {'email': 'ghost@test.com', 'status': 'user_not_found'},
            {'email': 'not-an-email', 'status': 'malformed'},
        ])
        self.assertEqual(
            (body['granted'], body['already_has_access'],
             body['user_not_found'], body['malformed']),
            (2, 1, 1, 1),
        )
        granted = CourseAccess.objects.filter(
            course=self.course, access_type='granted',
        )
        self.assertEqual(
            {(a.user_id, a.granted_by_id) for a in granted},
            {(self.alice.pk, self.staff.pk), (carol.pk, self.staff.pk)},
        )
        # Purchased row is untouched; granting sends no email.
        self.assertEqual(
            CourseAccess.objects.get(user=self.buyer).access_type, 'purchased',
        )
        self.assertEqual(mail.outbox, [])

    def test_repeat_grant_is_idempotent(self):
        self._post({'emails': ['alice@test.com']})
        response = self._post({'emails': ['alice@test.com']})

        self.assertEqual(response.json()['results'][0]['status'], 'already_has_access')
        self.assertEqual(
            CourseAccess.objects.filter(user=self.alice, course=self.course).count(),
            1,
        )

    def test_dry_run_reports_would_grant_and_writes_nothing(self):
        response = self._post({'emails': ['alice@test.com'], 'dry_run': True})

        body = response.json()
        self.assertTrue(body['dry_run'])
        self.assertEqual(body['results'][0]['status'], 'would_grant')
        self.assertFalse(CourseAccess.objects.filter(user=self.alice).exists())

    def test_body_must_name_exactly_one_of_emails_or_cohort(self):
        for body in ({}, {'emails': ['alice@test.com'], 'cohort': '1'}):
            with self.subTest(body=body):
                response = self._post(body)
                self.assertEqual(response.status_code, 422)
        self.assertFalse(CourseAccess.objects.filter(user=self.alice).exists())

    def test_unknown_course_returns_404(self):
        response = self._post({'emails': ['alice@test.com']}, slug='nope')
        self.assertEqual(response.json()['code'], 'unknown_course')
        self.assertEqual(response.status_code, 404)


class CourseAccessGrantByCohortTest(CourseAccessApiBase):
    def test_grants_every_current_member_of_the_cohort_only(self):
        other = Cohort.objects.create(
            course=self.course, name='Cohort 2', external_key='2',
            start_date=self.cohort1.start_date, end_date=self.cohort1.end_date,
        )
        outsider = User.objects.create_user(email='outsider@test.com', password='pw')
        CohortEnrollment.objects.create(cohort=other, user=outsider)
        for user in (self.alice, self.buyer):
            CohortEnrollment.objects.create(cohort=self.cohort1, user=user)

        response = self._post({'cohort': '1'})

        body = response.json()
        self.assertEqual(body['cohort'], '1')
        self.assertEqual(
            [(r['email'], r['status']) for r in body['results']],
            [('alice@test.com', 'granted'), ('buyer@test.com', 'already_has_access')],
        )
        self.assertTrue(CourseAccess.objects.filter(
            user=self.alice, course=self.course, access_type='granted',
        ).exists())
        self.assertFalse(CourseAccess.objects.filter(user=outsider).exists())

    def test_unknown_cohort_returns_400(self):
        response = self._post({'cohort': '9'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'unknown_cohort')

    def test_fifty_member_cohort_grant_uses_constant_queries(self):
        members = User.objects.bulk_create([
            User(email=f'm{i}@test.com')
            for i in range(50)
        ])
        CohortEnrollment.objects.bulk_create([
            CohortEnrollment(cohort=self.cohort1, user=u) for u in members
        ])

        # Warm the redirect-middleware cache so only the grant is counted.
        self._post({'cohort': '9'})

        # Token auth (2) + course + cohort + members + existing access +
        # savepoints (2) + one bulk insert, whatever the cohort size.
        with self.assertNumQueries(9):
            response = self._post({'cohort': '1'})

        self.assertEqual(response.json()['granted'], 50)
        self.assertEqual(
            CourseAccess.objects.filter(
                course=self.course, access_type='granted',
            ).count(),
            50,
        )


class CourseAccessRevokeTest(CourseAccessApiBase):
    def test_revoke_deletes_granted_row(self):
        CourseAccess.objects.create(
            user=self.alice, course=self.course, access_type='granted',
            granted_by=self.staff,
        )
        response = self._delete('alice@test.com')

        self.assertEqual(response.json(), {'email': 'alice@test.com', 'status': 'revoked'})
        self.assertFalse(CourseAccess.objects.filter(user=self.alice).exists())

    def test_revoke_never_removes_purchased_access(self):
        response = self._delete('buyer@test.com')

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'purchased_access')
        self.assertTrue(CourseAccess.objects.filter(user=self.buyer).exists())

    def test_revoke_without_access_is_a_no_op(self):
        response = self._delete('alice@test.com')
        self.assertEqual(response.json()['status'], 'no_access')

    def test_revoke_unknown_user_returns_404(self):
        response = self._delete('ghost@test.com')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'user_not_found')
