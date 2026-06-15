"""Tests for course certificate revoke / un-revoke (issue #949).

Covers the soft-revoke model behaviour (``is_revoked`` property), the
Studio revoke/un-revoke views (staff-gated, POST-only, idempotent), and
the public certificate page rendering its revoked state.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.models import Course, CourseCertificate, Module, Unit
from tests.fixtures import TierSetupMixin

User = get_user_model()


def _make_course(slug='c'):
    course = Course.objects.create(
        title=f'Course {slug}', slug=slug, status='published',
    )
    module = Module.objects.create(
        course=course, title='M', slug=f'{slug}-m', sort_order=0,
    )
    Unit.objects.create(module=module, title='U', slug=f'{slug}-u', sort_order=0)
    return course


class CertificateIsRevokedPropertyTest(TierSetupMixin, TestCase):
    def setUp(self):
        self.member = User.objects.create_user(
            email='member@example.com', password='pw',
        )
        self.course = _make_course(slug='prop')

    def test_is_revoked_false_by_default(self):
        cert = CourseCertificate.objects.create(
            user=self.member, course=self.course,
        )
        self.assertFalse(cert.is_revoked)

    def test_is_revoked_true_when_stamped(self):
        cert = CourseCertificate.objects.create(
            user=self.member, course=self.course,
            revoked_at=timezone.now(),
        )
        self.assertTrue(cert.is_revoked)


class CertificateRevokeViewTest(TierSetupMixin, TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email='staff@example.com', password='pw', is_staff=True,
        )
        self.member = User.objects.create_user(
            email='member@example.com', password='pw',
        )
        self.course = _make_course(slug='revoke')
        self.cert = CourseCertificate.objects.create(
            user=self.member, course=self.course,
        )

    def test_revoke_stamps_fields(self):
        self.client.login(email='staff@example.com', password='pw')
        response = self.client.post(
            f'/studio/certificates/{self.cert.pk}/revoke',
            {'revoked_reason': 'plagiarism'},
        )
        self.assertEqual(response.status_code, 302)
        self.cert.refresh_from_db()
        self.assertTrue(self.cert.is_revoked)
        self.assertEqual(self.cert.revoked_by, self.staff)
        self.assertEqual(self.cert.revoked_reason, 'plagiarism')

    def test_revoke_is_idempotent(self):
        self.cert.revoked_at = timezone.now()
        self.cert.revoked_by = self.staff
        self.cert.save(update_fields=['revoked_at', 'revoked_by'])
        self.client.login(email='staff@example.com', password='pw')
        response = self.client.post(
            f'/studio/certificates/{self.cert.pk}/revoke',
        )
        self.assertEqual(response.status_code, 302)
        self.cert.refresh_from_db()
        self.assertTrue(self.cert.is_revoked)

    def test_unrevoke_clears_fields(self):
        self.cert.revoked_at = timezone.now()
        self.cert.revoked_by = self.staff
        self.cert.revoked_reason = 'mistake'
        self.cert.save(
            update_fields=['revoked_at', 'revoked_by', 'revoked_reason'],
        )
        self.client.login(email='staff@example.com', password='pw')
        response = self.client.post(
            f'/studio/certificates/{self.cert.pk}/unrevoke',
        )
        self.assertEqual(response.status_code, 302)
        self.cert.refresh_from_db()
        self.assertFalse(self.cert.is_revoked)
        self.assertIsNone(self.cert.revoked_by)
        self.assertEqual(self.cert.revoked_reason, '')

    def test_revoke_get_returns_405(self):
        self.client.login(email='staff@example.com', password='pw')
        response = self.client.get(
            f'/studio/certificates/{self.cert.pk}/revoke',
        )
        self.assertEqual(response.status_code, 405)
        self.cert.refresh_from_db()
        self.assertFalse(self.cert.is_revoked)

    def test_revoke_anonymous_redirects_to_login(self):
        response = self.client.post(
            f'/studio/certificates/{self.cert.pk}/revoke',
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])
        self.cert.refresh_from_db()
        self.assertFalse(self.cert.is_revoked)

    def test_revoke_non_staff_forbidden(self):
        self.client.login(email='member@example.com', password='pw')
        response = self.client.post(
            f'/studio/certificates/{self.cert.pk}/revoke',
        )
        self.assertEqual(response.status_code, 403)
        self.cert.refresh_from_db()
        self.assertFalse(self.cert.is_revoked)


class PublicCertificatePageRevokedTest(TierSetupMixin, TestCase):
    def setUp(self):
        self.member = User.objects.create_user(
            email='member@example.com', password='pw',
        )
        self.course = _make_course(slug='public')
        self.cert = CourseCertificate.objects.create(
            user=self.member, course=self.course,
        )

    def test_valid_certificate_shows_credential(self):
        response = self.client.get(f'/certificates/{self.cert.pk}')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'This certifies that')
        self.assertNotContains(response, 'certificate-revoked-message')

    def test_revoked_certificate_shows_revoked_state(self):
        self.cert.revoked_at = timezone.now()
        self.cert.save(update_fields=['revoked_at'])
        response = self.client.get(f'/certificates/{self.cert.pk}')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'certificate-revoked-message')
        self.assertContains(response, 'This certificate has been revoked')
        # The valid-credential body ("This certifies that ...") is suppressed.
        self.assertNotContains(response, 'This certifies that')
        self.assertNotContains(response, 'certificate-pdf-link')


class StudioPeerReviewCertificateControlTest(TierSetupMixin, TestCase):
    """The peer-reviews page surfaces a revoke control for certificates."""

    def setUp(self):
        from content.models import ProjectSubmission

        self.staff = User.objects.create_user(
            email='staff@example.com', password='pw', is_staff=True,
        )
        self.member = User.objects.create_user(
            email='member@example.com', password='pw',
        )
        self.course = _make_course(slug='ctrl')
        self.submission = ProjectSubmission.objects.create(
            user=self.member, course=self.course,
            project_url='https://example.com/p', status='certified',
        )
        self.cert = CourseCertificate.objects.create(
            user=self.member, course=self.course, submission=self.submission,
        )

    def test_revoke_control_rendered(self):
        self.client.login(email='staff@example.com', password='pw')
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/peer-reviews',
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'certificate-revoke-form')
        self.assertContains(
            response,
            f'/studio/certificates/{self.cert.pk}/revoke',
        )

    def test_unrevoke_control_rendered_when_revoked(self):
        self.cert.revoked_at = timezone.now()
        self.cert.save(update_fields=['revoked_at'])
        self.client.login(email='staff@example.com', password='pw')
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/peer-reviews',
        )
        self.assertContains(response, 'certificate-unrevoke-form')
        self.assertContains(response, 'certificate-revoked-badge')
