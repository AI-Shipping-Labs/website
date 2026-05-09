"""Tests for the course certificate endpoints (issue #445).

Covers GET (staff-only listing ordered by ``issued_at`` desc), POST
(create-or-update with idempotent semantics, validation of ``pdf_url``
scheme and ``submission_id`` foreign-course rejection), and DELETE
(idempotent hard-delete).
"""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
from content.models import Course
from content.models.peer_review import CourseCertificate, ProjectSubmission

User = get_user_model()


class CourseCertificateApiTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(email='m@test.com', password='pw')
        cls.alice = User.objects.create_user(
            email='alice@test.com', password='pw',
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name='s')
        cls.course = Course.objects.create(
            title='AI Buildcamp',
            slug='ai-buildcamp',
            status='published',
        )
        cls.other_course = Course.objects.create(
            title='Other Course',
            slug='other-course',
            status='published',
        )

    def _auth(self, token=None):
        if token is None:
            token = self.staff_token
        return {'HTTP_AUTHORIZATION': f'Token {token.key}'}


class CourseCertificatesListTest(CourseCertificateApiTestBase):
    def test_returns_certificates_ordered_by_issued_at_desc(self):
        # Create two certs; verify ordering and shape.
        cert_alice = CourseCertificate.objects.create(
            user=self.alice, course=self.course,
            pdf_url='http://example.com/alice.pdf',
        )
        cert_member = CourseCertificate.objects.create(
            user=self.member, course=self.course,
        )

        response = self.client.get(
            '/api/courses/ai-buildcamp/certificates', **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        rows = response.json()['certificates']
        self.assertEqual(len(rows), 2)
        # Most-recent first.
        self.assertEqual(rows[0]['user_email'], cert_member.user.email)
        self.assertEqual(rows[1]['user_email'], cert_alice.user.email)
        # Field shape on a populated row.
        alice_row = rows[1]
        self.assertEqual(alice_row['id'], str(cert_alice.id))
        self.assertEqual(alice_row['pdf_url'], 'http://example.com/alice.pdf')
        self.assertIsNone(alice_row['submission_id'])
        self.assertIsNotNone(alice_row['issued_at'])

    def test_no_token_returns_401(self):
        response = self.client.get('/api/courses/ai-buildcamp/certificates')
        self.assertEqual(response.status_code, 401)

    def test_unknown_course_returns_404(self):
        response = self.client.get(
            '/api/courses/nope/certificates', **self._auth(),
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'unknown_course')


class CourseCertificateCreateTest(CourseCertificateApiTestBase):
    def _post(self, payload, *, token=None, course_slug='ai-buildcamp'):
        return self.client.post(
            f'/api/courses/{course_slug}/certificates',
            data=json.dumps(payload),
            content_type='application/json',
            **self._auth(token),
        )

    def test_creates_new_certificate_with_pdf_url(self):
        response = self._post({
            'user_email': 'alice@test.com',
            'pdf_url': 'http://certs.example.com/alice.pdf',
            'submission_id': None,
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['created'])
        self.assertEqual(body['user_email'], 'alice@test.com')
        self.assertEqual(body['pdf_url'], 'http://certs.example.com/alice.pdf')
        self.assertIsNone(body['submission_id'])
        self.assertIsNotNone(body['issued_at'])
        cert = CourseCertificate.objects.get(
            user=self.alice, course=self.course,
        )
        self.assertEqual(cert.pdf_url, 'http://certs.example.com/alice.pdf')

    def test_update_does_not_change_issued_at(self):
        cert = CourseCertificate.objects.create(
            user=self.alice, course=self.course,
            pdf_url='http://old.example.com/v1.pdf',
        )
        original_issued_at = cert.issued_at

        response = self._post({
            'user_email': 'alice@test.com',
            'pdf_url': 'http://new.example.com/v2.pdf',
            'submission_id': None,
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body['created'])
        self.assertEqual(body['pdf_url'], 'http://new.example.com/v2.pdf')

        cert.refresh_from_db()
        self.assertEqual(cert.issued_at, original_issued_at)
        self.assertEqual(cert.pdf_url, 'http://new.example.com/v2.pdf')

    def test_update_with_empty_pdf_url_clears_field(self):
        CourseCertificate.objects.create(
            user=self.alice, course=self.course,
            pdf_url='http://old.example.com/old.pdf',
        )
        response = self._post({
            'user_email': 'alice@test.com',
            'pdf_url': '',
            'submission_id': None,
        })
        self.assertEqual(response.status_code, 200)
        cert = CourseCertificate.objects.get(
            user=self.alice, course=self.course,
        )
        self.assertEqual(cert.pdf_url, '')

    def test_update_with_null_submission_id_clears_fk(self):
        submission = ProjectSubmission.objects.create(
            user=self.alice,
            course=self.course,
            project_url='http://github.com/alice/project',
        )
        CourseCertificate.objects.create(
            user=self.alice, course=self.course, submission=submission,
        )
        response = self._post({
            'user_email': 'alice@test.com',
            'pdf_url': '',
            'submission_id': None,
        })
        self.assertEqual(response.status_code, 200)
        cert = CourseCertificate.objects.get(
            user=self.alice, course=self.course,
        )
        self.assertIsNone(cert.submission_id)

    def test_submission_id_for_other_course_returns_422(self):
        # Submission belongs to a DIFFERENT course.
        foreign_submission = ProjectSubmission.objects.create(
            user=self.alice,
            course=self.other_course,
            project_url='http://github.com/alice/other',
        )
        response = self._post({
            'user_email': 'alice@test.com',
            'pdf_url': '',
            'submission_id': foreign_submission.pk,
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'invalid_submission')
        self.assertFalse(
            CourseCertificate.objects.filter(
                user=self.alice, course=self.course,
            ).exists()
        )

    def test_javascript_url_scheme_returns_422(self):
        response = self._post({
            'user_email': 'alice@test.com',
            'pdf_url': 'javascript:alert(1)',
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'invalid_url')
        self.assertFalse(
            CourseCertificate.objects.filter(
                user=self.alice, course=self.course,
            ).exists()
        )

    def test_file_url_scheme_returns_422(self):
        response = self._post({
            'user_email': 'alice@test.com',
            'pdf_url': 'file:///etc/passwd',
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'invalid_url')

    def test_ftp_url_scheme_returns_422(self):
        response = self._post({
            'user_email': 'alice@test.com',
            'pdf_url': 'ftp://example.com/file.pdf',
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'invalid_url')

    def test_http_url_is_accepted(self):
        # The operator's real URLs are http (not https).
        response = self._post({
            'user_email': 'alice@test.com',
            'pdf_url': 'http://certificates.aishippinglabs.com/x/y.pdf',
        })
        self.assertEqual(response.status_code, 200)

    def test_https_url_is_accepted(self):
        response = self._post({
            'user_email': 'alice@test.com',
            'pdf_url': 'https://certificates.aishippinglabs.com/x/y.pdf',
        })
        self.assertEqual(response.status_code, 200)

    def test_unknown_user_email_returns_422(self):
        response = self._post({
            'user_email': 'nobody@nope.com',
            'pdf_url': '',
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'unknown_user')

    def test_missing_user_email_returns_422(self):
        response = self._post({'pdf_url': ''})
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body['code'], 'missing_field')
        self.assertEqual(body['details']['field'], 'user_email')

    def test_idempotent_repeat_post_no_new_row(self):
        self._post({
            'user_email': 'alice@test.com',
            'pdf_url': 'http://example.com/1.pdf',
        })
        before = CourseCertificate.objects.count()
        response = self._post({
            'user_email': 'alice@test.com',
            'pdf_url': 'http://example.com/1.pdf',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['created'])
        self.assertEqual(CourseCertificate.objects.count(), before)

    def test_unknown_course_returns_404(self):
        response = self._post(
            {'user_email': 'alice@test.com', 'pdf_url': ''},
            course_slug='nope',
        )
        self.assertEqual(response.status_code, 404)


class CourseCertificateDeleteTest(CourseCertificateApiTestBase):
    def test_delete_removes_existing_row(self):
        CourseCertificate.objects.create(
            user=self.alice, course=self.course,
        )
        response = self.client.delete(
            '/api/courses/ai-buildcamp/certificates/alice@test.com',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(
            CourseCertificate.objects.filter(
                user=self.alice, course=self.course,
            ).exists()
        )

    def test_delete_is_idempotent_when_no_row(self):
        response = self.client.delete(
            '/api/courses/ai-buildcamp/certificates/alice@test.com',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 204)

    def test_delete_unknown_email_returns_204(self):
        response = self.client.delete(
            '/api/courses/ai-buildcamp/certificates/nobody@x.com',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 204)

    def test_unknown_course_returns_404(self):
        response = self.client.delete(
            '/api/courses/nope/certificates/alice@test.com',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 404)


class CourseCertificatePublicPageTest(TestCase):
    """The public ``/certificates/<uuid>`` page should surface the PDF
    button when ``pdf_url`` is set and render unchanged when it's empty.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='alice@test.com', password='pw', first_name='Alice',
        )
        cls.course = Course.objects.create(
            title='AI Buildcamp', slug='ai-buildcamp', status='published',
        )

    def test_pdf_button_renders_when_pdf_url_set(self):
        cert = CourseCertificate.objects.create(
            user=self.user, course=self.course,
            pdf_url='http://example.com/alice.pdf',
        )
        response = self.client.get(f'/certificates/{cert.id}')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="certificate-pdf-link"')
        self.assertContains(response, 'http://example.com/alice.pdf')
        self.assertContains(response, 'Download PDF')
        self.assertContains(response, 'rel="noopener noreferrer"')
        self.assertContains(response, 'target="_blank"')

    def test_pdf_button_absent_when_pdf_url_empty(self):
        cert = CourseCertificate.objects.create(
            user=self.user, course=self.course, pdf_url='',
        )
        response = self.client.get(f'/certificates/{cert.id}')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="certificate-pdf-link"')
        self.assertNotContains(response, 'Download PDF')
        # Page still shows essential cert content.
        self.assertContains(response, str(cert.id))
        self.assertContains(response, self.course.title)


class CourseCertificatesPermissionDisciplineTest(TestCase):
    """Ensure the view module never reads ``is_staff`` directly."""

    def test_no_is_staff_literal_in_view_module(self):
        from pathlib import Path
        path = Path(__file__).resolve().parent.parent / 'views' / 'course_certificates.py'
        source = path.read_text()
        self.assertNotIn('is_staff', source)
