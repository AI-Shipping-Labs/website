"""Tests for the course enrollment endpoints (issue #445).

Mirrors the structure of ``test_enrollments.py`` (sprints) — covers the
GET scope-by-bearer behaviour, the four-bucket POST shape (single +
bulk + mixed forms), the soft-delete idempotent DELETE, and the
re-enrollment flow that creates a NEW active row while preserving the
soft-deleted history row.
"""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
from content.access import LEVEL_PREMIUM
from content.models import Course
from content.models.enrollment import (
    SOURCE_ADMIN,
    SOURCE_MANUAL,
    Enrollment,
)
from payments.models import Tier

User = get_user_model()


def _attach_tier(user, slug):
    user.tier = Tier.objects.get(slug=slug)
    user.save(update_fields=['tier'])
    return user


class CourseEnrollmentApiTestBase(TestCase):
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
        cls.course = Course.objects.create(
            title='Premium Deep Dive',
            slug='premium-deep-dive',
            status='published',
            required_level=LEVEL_PREMIUM,
        )

    def _auth(self, token=None):
        if token is None:
            token = self.staff_token
        return {'HTTP_AUTHORIZATION': f'Token {token.key}'}


class CourseEnrollmentsListScopeTest(CourseEnrollmentApiTestBase):
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
        Enrollment.objects.create(course=cls.course, user=cls.member)
        Enrollment.objects.create(course=cls.course, user=cls.alice)
        Enrollment.objects.create(course=cls.course, user=cls.bob)

    def test_staff_token_lists_active_enrollments_only_by_default(self):
        # Soft-delete bob to ensure he is filtered out by default.
        from django.utils import timezone
        bob_enrollment = Enrollment.objects.get(
            course=self.course, user=self.bob,
        )
        bob_enrollment.unenrolled_at = timezone.now()
        bob_enrollment.save(update_fields=['unenrolled_at'])

        response = self.client.get(
            '/api/courses/premium-deep-dive/enrollments', **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        emails = {row['user_email'] for row in response.json()['enrollments']}
        self.assertEqual(emails, {'m@test.com', 'alice@test.com'})

    def test_include_unenrolled_returns_soft_deleted_rows(self):
        from django.utils import timezone
        bob_enrollment = Enrollment.objects.get(
            course=self.course, user=self.bob,
        )
        bob_enrollment.unenrolled_at = timezone.now()
        bob_enrollment.save(update_fields=['unenrolled_at'])

        response = self.client.get(
            '/api/courses/premium-deep-dive/enrollments?include_unenrolled=1',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        rows = response.json()['enrollments']
        emails = {row['user_email'] for row in rows}
        self.assertEqual(emails, {'m@test.com', 'alice@test.com', 'bob@test.com'})

        bob_row = next(r for r in rows if r['user_email'] == 'bob@test.com')
        self.assertIsNotNone(bob_row['unenrolled_at'])

    def test_no_token_returns_401(self):
        response = self.client.get('/api/courses/premium-deep-dive/enrollments')
        self.assertEqual(response.status_code, 401)

    def test_unknown_course_returns_404(self):
        response = self.client.get(
            '/api/courses/nope/enrollments', **self._auth(),
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'unknown_course')

    def test_draft_course_returns_404(self):
        Course.objects.create(
            title='Draft Course',
            slug='draft-course',
            status='draft',
        )
        response = self.client.get(
            '/api/courses/draft-course/enrollments', **self._auth(),
        )
        self.assertEqual(response.status_code, 404)


class CourseEnrollmentsBulkPostTest(CourseEnrollmentApiTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.basic_user = _attach_tier(
            User.objects.create_user(email='basic@test.com', password='pw'),
            'basic',
        )
        cls.premium_user = _attach_tier(
            User.objects.create_user(email='alice@test.com', password='pw'),
            'premium',
        )

    def _post(self, payload, *, token=None):
        return self.client.post(
            '/api/courses/premium-deep-dive/enrollments',
            data=json.dumps(payload),
            content_type='application/json',
            **self._auth(token),
        )

    def test_single_user_email_returns_four_bucket_summary(self):
        response = self._post({'user_email': 'alice@test.com'})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['enrolled'], 1)
        self.assertEqual(body['already_enrolled'], 0)
        self.assertEqual(body['under_tier'], [])
        self.assertEqual(body['unknown_emails'], [])

    def test_bulk_user_emails_returns_four_bucket_summary(self):
        # Pre-existing active enrollment to trigger ``already_enrolled``.
        Enrollment.objects.create(course=self.course, user=self.member)
        response = self._post({
            'user_emails': [
                'm@test.com',
                'alice@test.com',
                'basic@test.com',
                'nope@nope.com',
            ],
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['enrolled'], 2)
        self.assertEqual(body['already_enrolled'], 1)
        # basic user can't access the premium-gated course.
        self.assertEqual(body['under_tier'], ['basic@test.com'])
        self.assertEqual(body['unknown_emails'], ['nope@nope.com'])

        # Under-tier user is STILL enrolled (warning, not rejection).
        self.assertTrue(
            Enrollment.objects.filter(
                course=self.course,
                user=self.basic_user,
                unenrolled_at__isnull=True,
            ).exists(),
        )

    def test_combines_user_email_and_user_emails_dedup(self):
        response = self._post({
            'user_email': 'alice@test.com',
            'user_emails': ['ALICE@test.com', 'basic@test.com'],
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        # alice deduped to one; both alice + basic enrolled
        self.assertEqual(body['enrolled'], 2)
        self.assertEqual(body['already_enrolled'], 0)

    def test_admin_source_recorded(self):
        self._post({'user_email': 'alice@test.com'})
        enrollment = Enrollment.objects.get(
            course=self.course, user=self.premium_user,
        )
        self.assertEqual(enrollment.source, SOURCE_ADMIN)

    def test_idempotent_post(self):
        self._post({'user_email': 'alice@test.com'})
        before = Enrollment.objects.filter(course=self.course).count()
        response = self._post({'user_email': 'alice@test.com'})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['enrolled'], 0)
        self.assertEqual(body['already_enrolled'], 1)
        self.assertEqual(
            Enrollment.objects.filter(course=self.course).count(), before,
        )

    def test_re_enrolls_after_soft_delete_creates_new_row(self):
        # First enroll, then unenroll, then re-enroll.
        from django.utils import timezone

        original = Enrollment.objects.create(
            course=self.course, user=self.premium_user,
        )
        original.unenrolled_at = timezone.now()
        original.save(update_fields=['unenrolled_at'])

        response = self._post({'user_email': 'alice@test.com'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['enrolled'], 1)

        rows = Enrollment.objects.filter(
            course=self.course, user=self.premium_user,
        ).order_by('enrolled_at')
        self.assertEqual(rows.count(), 2)
        # Old row is preserved, new row is active
        self.assertIsNotNone(rows[0].unenrolled_at)
        self.assertIsNone(rows[1].unenrolled_at)

    def test_missing_email_field_returns_422(self):
        response = self._post({})
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body['code'], 'missing_field')
        self.assertEqual(
            body['details']['field'], 'user_email_or_user_emails',
        )

    def test_non_object_body_returns_422(self):
        response = self.client.post(
            '/api/courses/premium-deep-dive/enrollments',
            data=json.dumps([1, 2, 3]),
            content_type='application/json',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'invalid_type')

    def test_invalid_json_returns_400(self):
        response = self.client.post(
            '/api/courses/premium-deep-dive/enrollments',
            data='not-json',
            content_type='application/json',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 400)

    def test_unknown_course_returns_404(self):
        response = self.client.post(
            '/api/courses/nope/enrollments',
            data=json.dumps({'user_email': 'alice@test.com'}),
            content_type='application/json',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'unknown_course')


class CourseEnrollmentDeleteTest(CourseEnrollmentApiTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.target = _attach_tier(
            User.objects.create_user(email='alice@test.com', password='pw'),
            'premium',
        )

    def test_delete_idempotent_204_when_not_enrolled(self):
        response = self.client.delete(
            '/api/courses/premium-deep-dive/enrollments/alice@test.com',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 204)

    def test_delete_soft_deletes_active_row(self):
        enrollment = Enrollment.objects.create(
            course=self.course, user=self.target, source=SOURCE_MANUAL,
        )
        response = self.client.delete(
            '/api/courses/premium-deep-dive/enrollments/alice@test.com',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 204)
        enrollment.refresh_from_db()
        self.assertIsNotNone(enrollment.unenrolled_at)

    def test_repeated_delete_is_204_no_extra_state_change(self):
        enrollment = Enrollment.objects.create(
            course=self.course, user=self.target,
        )
        first = self.client.delete(
            '/api/courses/premium-deep-dive/enrollments/alice@test.com',
            **self._auth(),
        )
        enrollment.refresh_from_db()
        first_unenrolled_at = enrollment.unenrolled_at

        second = self.client.delete(
            '/api/courses/premium-deep-dive/enrollments/alice@test.com',
            **self._auth(),
        )
        enrollment.refresh_from_db()

        self.assertEqual(first.status_code, 204)
        self.assertEqual(second.status_code, 204)
        # The timestamp from the first delete is preserved.
        self.assertEqual(enrollment.unenrolled_at, first_unenrolled_at)

    def test_delete_unknown_user_email_returns_204(self):
        response = self.client.delete(
            '/api/courses/premium-deep-dive/enrollments/nobody@x.com',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 204)

    def test_unknown_course_returns_404(self):
        response = self.client.delete(
            '/api/courses/nope/enrollments/alice@test.com',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'unknown_course')


class CourseEnrollmentsPermissionDisciplineTest(TestCase):
    """Ensure the view module never reads ``is_staff`` directly.

    Mirrors the discipline check applied to ``api/views/interview_notes.py``
    in #433: only ``api/views/_permissions.py`` may inspect that
    attribute. Other view modules go through ``bearer_is_admin``.
    """

    def test_no_is_staff_literal_in_view_module(self):
        from pathlib import Path
        path = Path(__file__).resolve().parent.parent / 'views' / 'course_enrollments.py'
        source = path.read_text()
        self.assertNotIn('is_staff', source)
