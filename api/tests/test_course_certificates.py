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


def make_user(email, **overrides):
    fields = {"email": email, "password": None}
    fields.update(overrides)
    return User.objects.create_user(**fields)


def make_course(title, slug):
    return Course.objects.create(
        title=title,
        slug=slug,
        status='published',
    )


class CourseCertificateApiTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = make_user('staff@test.com', is_staff=True)
        cls.member = make_user('m@test.com')
        cls.alice = make_user('alice@test.com')
        cls.former_staff = make_user('former-staff@test.com', is_staff=True)
        cls.staff_token = Token.objects.create(user=cls.staff, name='s')
        cls.former_staff_token = Token.objects.create(
            user=cls.former_staff,
            name='former',
        )
        cls.former_staff.is_staff = False
        cls.former_staff.save(update_fields=['is_staff'])
        cls.course = make_course('AI Buildcamp', 'ai-buildcamp')
        cls.other_course = make_course('Other Course', 'other-course')

    def _auth(self, token=None):
        if token is None:
            token = self.staff_token
        return {'HTTP_AUTHORIZATION': f'Token {token.key}'}

    def assert_json_error(self, response, *, status, code=None, error=None):
        self.assertEqual(response.status_code, status)
        body = response.json()
        self.assertIn('error', body)
        if code is not None:
            self.assertEqual(body['code'], code)
        if error is not None:
            self.assertEqual(body['error'], error)
        return body


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
        self.assert_json_error(
            response,
            status=401,
            error='Authentication token required',
        )

    def test_unknown_course_returns_404(self):
        response = self.client.get(
            '/api/courses/nope/certificates', **self._auth(),
        )
        self.assert_json_error(response, status=404, code='unknown_course')


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
        self.assert_json_error(response, status=422, code='invalid_url')
        self.assertFalse(
            CourseCertificate.objects.filter(
                user=self.alice, course=self.course,
            ).exists()
        )

    def test_ftp_url_scheme_returns_422(self):
        response = self._post({
            'user_email': 'alice@test.com',
            'pdf_url': 'ftp://example.com/file.pdf',
        })
        self.assert_json_error(response, status=422, code='invalid_url')
        self.assertFalse(
            CourseCertificate.objects.filter(
                user=self.alice, course=self.course,
            ).exists()
        )

    def test_http_url_is_accepted(self):
        # The operator's real URLs are http (not https).
        response = self._post({
            'user_email': 'alice@test.com',
            'pdf_url': 'http://certificates.aishippinglabs.com/x/y.pdf',
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['created'])
        self.assertEqual(
            body['pdf_url'],
            'http://certificates.aishippinglabs.com/x/y.pdf',
        )
        self.assertTrue(
            CourseCertificate.objects.filter(
                user=self.alice,
                course=self.course,
                pdf_url='http://certificates.aishippinglabs.com/x/y.pdf',
            ).exists()
        )

    def test_https_url_is_accepted(self):
        response = self._post({
            'user_email': 'alice@test.com',
            'pdf_url': 'https://certificates.aishippinglabs.com/x/y.pdf',
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['created'])
        self.assertEqual(
            body['pdf_url'],
            'https://certificates.aishippinglabs.com/x/y.pdf',
        )
        self.assertTrue(
            CourseCertificate.objects.filter(
                user=self.alice,
                course=self.course,
                pdf_url='https://certificates.aishippinglabs.com/x/y.pdf',
            ).exists()
        )

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
        before = CourseCertificate.objects.count()
        response = self._post(
            {'user_email': 'alice@test.com', 'pdf_url': ''},
            course_slug='nope',
        )
        self.assert_json_error(response, status=404, code='unknown_course')
        self.assertEqual(CourseCertificate.objects.count(), before)


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
        before = CourseCertificate.objects.count()
        response = self.client.delete(
            '/api/courses/ai-buildcamp/certificates/alice@test.com',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(CourseCertificate.objects.count(), before)

    def test_delete_unknown_email_returns_204(self):
        before = CourseCertificate.objects.count()
        response = self.client.delete(
            '/api/courses/ai-buildcamp/certificates/nobody@x.com',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(CourseCertificate.objects.count(), before)

    def test_unknown_course_returns_404(self):
        before = CourseCertificate.objects.count()
        response = self.client.delete(
            '/api/courses/nope/certificates/alice@test.com',
            **self._auth(),
        )
        self.assert_json_error(response, status=404, code='unknown_course')
        self.assertEqual(CourseCertificate.objects.count(), before)

    def test_non_staff_token_cannot_delete_existing_row(self):
        CourseCertificate.objects.create(
            user=self.alice, course=self.course,
        )
        response = self.client.delete(
            '/api/courses/ai-buildcamp/certificates/alice@test.com',
            **self._auth(self.former_staff_token),
        )
        self.assert_json_error(response, status=401, error='Invalid token')
        self.assertTrue(
            CourseCertificate.objects.filter(
                user=self.alice, course=self.course,
            ).exists()
        )


class CourseCertificatePermissionTest(CourseCertificateApiTestBase):
    def test_non_staff_token_cannot_list_certificates(self):
        CourseCertificate.objects.create(
            user=self.alice, course=self.course,
        )
        response = self.client.get(
            '/api/courses/ai-buildcamp/certificates',
            **self._auth(self.former_staff_token),
        )
        self.assert_json_error(response, status=401, error='Invalid token')

    def test_non_staff_token_cannot_create_certificate(self):
        response = self.client.post(
            '/api/courses/ai-buildcamp/certificates',
            data=json.dumps({
                'user_email': 'alice@test.com',
                'pdf_url': 'https://certs.example.com/alice.pdf',
            }),
            content_type='application/json',
            **self._auth(self.former_staff_token),
        )

        self.assert_json_error(response, status=401, error='Invalid token')
        self.assertFalse(
            CourseCertificate.objects.filter(
                user=self.alice, course=self.course,
            ).exists()
        )


class CourseCertificatePublicPageTest(TestCase):
    """The public ``/certificates/<uuid>`` page should surface the PDF
    button when ``pdf_url`` is set and render unchanged when it's empty.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user('alice@test.com', first_name='Alice')
        cls.course = make_course('AI Buildcamp', 'ai-buildcamp')

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
