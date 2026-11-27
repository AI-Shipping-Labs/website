"""Tests for AISL attempt-scoped certificate service adapter (intake 32366cde)."""

from datetime import timedelta

from community_base.coursework.attempt_certificates import PrivatePreviewArtifact
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from content.models import (
    Cohort,
    Course,
    CourseCertificate,
    CourseProject,
    Module,
    PeerReview,
    ProjectSubmission,
    Unit,
    UserCourseProgress,
)
from content.models.peer_review import ProjectState
from content.services.attempt_certificates import (
    generate_attempt_certificates,
    preview_attempt_certificates,
    preview_sample_certificate_pdf,
)

User = get_user_model()

CRITERIA = [
    {
        'id': 'quality',
        'title': 'Quality',
        'type': 'scale',
        'options': [
            {'text': 'Low', 'points': 0},
            {'text': 'Pass', 'points': 10},
        ],
    },
]


class AttemptCertificateServiceTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        cls.course = Course.objects.create(
            title='AI Engineering Buildcamp',
            slug='ai-buildcamp-certs',
            status='published',
            peer_review_enabled=True,
            project_passing_score=8,
        )
        cls.cohort = Cohort.objects.create(
            course=cls.course,
            name='Cohort 1',
            mode='cohort',
            start_date=now.date() - timedelta(days=30),
            end_date=now.date(),
            is_active=True,
        )
        cls.project = CourseProject.objects.create(
            course=cls.course,
            cohort=cls.cohort,
            slug='final-project',
            title='Final Project',
            submission_due_at=now - timedelta(days=10),
            review_due_at=now - timedelta(days=3),
            state=ProjectState.COMPLETED,
            evaluation_criteria=CRITERIA,
        )
        cls.alice = User.objects.create_user(
            email='alice@test.com', first_name='Alice', last_name='Grad',
        )
        cls.bob = User.objects.create_user(
            email='bob@test.com', first_name='Bob', last_name='Grad',
        )

    def _create_passing_pair(self):
        sub_a = ProjectSubmission.objects.create(
            user=self.alice,
            course=self.course,
            cohort=self.cohort,
            course_project=self.project,
            project_url='https://github.com/example/alice',
            status='review_complete',
        )
        sub_b = ProjectSubmission.objects.create(
            user=self.bob,
            course=self.course,
            cohort=self.cohort,
            course_project=self.project,
            project_url='https://github.com/example/bob',
            status='review_complete',
        )
        PeerReview.objects.create(
            submission=sub_a,
            reviewer=self.bob,
            is_complete=True,
            criteria_scores={'quality': 1},
        )
        PeerReview.objects.create(
            submission=sub_b,
            reviewer=self.alice,
            is_complete=True,
            criteria_scores={'quality': 1},
        )
        return sub_a, sub_b

    def test_non_completed_attempt_refuses_generation(self):
        self._create_passing_pair()
        self.project.state = ProjectState.PEER_REVIEWING
        self.project.save(update_fields=['state'])
        calls = []

        result = generate_attempt_certificates(
            self.course,
            self.project,
            render_to_s3_fn=lambda cert, course: calls.append(cert.id),
        )

        self.assertFalse(result.attempt_completed)
        self.assertEqual(calls, [])
        self.assertEqual(CourseCertificate.objects.count(), 0)

    def test_respects_course_unit_policy_and_communicates_difference(self):
        self._create_passing_pair()
        module = Module.objects.create(
            course=self.course, title='Module 1', slug='m1', sort_order=0,
        )
        unit = Unit.objects.create(
            module=module, title='Lesson 1', slug='l1', sort_order=0, kind='lesson',
        )
        UserCourseProgress.objects.create(
            user=self.alice, unit=unit, completed_at=timezone.now(),
        )

        preview = preview_attempt_certificates(self.course, self.project)

        self.assertEqual(preview.passed_attempt_count, 2)
        self.assertEqual(preview.excluded_by_policy_count, 1)
        self.assertEqual(preview.eligible_count, 1)
        self.assertIn('1 excluded', preview.policy_difference_note)

    def test_idempotent_generation_preserves_revoked_and_never_emails(self):
        sub_a, sub_b = self._create_passing_pair()
        revoked = CourseCertificate.objects.create(
            user=self.bob,
            course=self.course,
            submission=sub_b,
            revoked_at=timezone.now(),
            revoked_reason='Manual revoke',
        )
        rendered = []

        def fake_render(cert, *, course):
            rendered.append(str(cert.id))
            return f'https://cdn.aishippinglabs.com/banners/certificates/{cert.id}.pdf'

        first = generate_attempt_certificates(
            self.course, self.project, render_to_s3_fn=fake_render,
        )
        self.assertEqual(first.created_count, 1)
        self.assertEqual(first.skipped_revoked_count, 1)
        self.assertEqual(len(rendered), 1)
        self.assertEqual(len(mail.outbox), 0)

        cert_a = CourseCertificate.objects.get(user=self.alice, course=self.course)
        self.assertTrue(cert_a.pdf_url.endswith(f'/{cert_a.id}.pdf'))
        sub_a.refresh_from_db()
        self.assertEqual(sub_a.status, 'certified')
        revoked.refresh_from_db()
        self.assertTrue(revoked.is_revoked)
        self.assertEqual(revoked.pdf_url, '')

        second = generate_attempt_certificates(
            self.course, self.project, render_to_s3_fn=fake_render,
        )
        self.assertEqual(second.created_count, 0)
        self.assertEqual(second.skipped_issued_count, 1)
        self.assertEqual(second.skipped_revoked_count, 1)
        self.assertEqual(len(rendered), 1)

    def test_render_failure_retries_without_reissuing_credential(self):
        self._create_passing_pair()
        should_fail = {'bob': True}

        def flaky_render(cert, *, course):
            if cert.user_id == self.bob.pk and should_fail['bob']:
                raise RuntimeError('upstream 502')
            return f'https://cdn.aishippinglabs.com/banners/certificates/{cert.id}.pdf'

        first = generate_attempt_certificates(
            self.course, self.project, render_to_s3_fn=flaky_render,
        )
        self.assertEqual(first.created_count, 1)
        self.assertEqual(first.failed_count, 1)
        bob_cert_before = CourseCertificate.objects.get(
            user=self.bob, course=self.course,
        )
        self.assertEqual(bob_cert_before.pdf_url, '')

        should_fail['bob'] = False
        second = generate_attempt_certificates(
            self.course, self.project, render_to_s3_fn=flaky_render,
        )
        self.assertEqual(second.created_count, 0)
        self.assertEqual(second.retried_count, 1)
        self.assertEqual(second.skipped_issued_count, 1)
        bob_cert_after = CourseCertificate.objects.get(
            user=self.bob, course=self.course,
        )
        self.assertEqual(bob_cert_after.id, bob_cert_before.id)
        self.assertTrue(bob_cert_after.pdf_url.endswith(f'/{bob_cert_before.id}.pdf'))

    def test_cache_lock_skips_in_progress_concurrent_candidate(self):
        self._create_passing_pair()
        lock_key = f'attempt-cert-lock:{self.course.pk}:{self.alice.pk}'
        cache.set(lock_key, '1', timeout=60)
        self.addCleanup(cache.delete, lock_key)

        result = generate_attempt_certificates(
            self.course,
            self.project,
            render_to_s3_fn=lambda cert, course: (
                f'https://cdn.aishippinglabs.com/banners/certificates/{cert.id}.pdf'
            ),
        )

        self.assertEqual(result.skipped_in_progress_count, 1)
        self.assertEqual(result.created_count, 1)
        self.assertFalse(
            CourseCertificate.objects.filter(user=self.alice, course=self.course).exists()
        )

    def test_private_preview_creates_no_credentials(self):
        artifact = preview_sample_certificate_pdf(
            self.course,
            self.project,
            recipient_name='Owner Preview',
            preview_fn=lambda recipient_name, course: PrivatePreviewArtifact(
                content=b'%PDF-1.4 sample',
                content_type='application/pdf',
                filename='preview.pdf',
            ),
        )

        self.assertEqual(artifact.content, b'%PDF-1.4 sample')
        self.assertEqual(CourseCertificate.objects.count(), 0)
