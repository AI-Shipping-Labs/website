"""Tests for staff Studio attempt certificate controls and views (intake 32366cde)."""

from datetime import timedelta
from unittest.mock import patch

from community_base.coursework.attempt_certificates import PrivatePreviewArtifact
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from content.models import (
    Cohort,
    Course,
    CourseCertificate,
    CourseProject,
    PeerReview,
    ProjectSubmission,
)
from content.models.peer_review import ProjectState

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


class StudioAttemptCertificatesTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        cls.staff = User.objects.create_user(
            email='staff-cert@test.com',
            password='pw',
            is_staff=True,
            first_name='Alexey',
            last_name='Instructor',
        )
        cls.member = User.objects.create_user(
            email='member-cert@test.com', password='pw', is_staff=False,
        )
        cls.course = Course.objects.create(
            title='Buildcamp Studio Certs',
            slug='buildcamp-studio-certs',
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
        cls.completed_project = CourseProject.objects.create(
            course=cls.course,
            cohort=cls.cohort,
            slug='attempt-completed',
            title='Completed Attempt',
            submission_due_at=now - timedelta(days=10),
            review_due_at=now - timedelta(days=3),
            state=ProjectState.COMPLETED,
            evaluation_criteria=CRITERIA,
        )
        cls.open_project = CourseProject.objects.create(
            course=cls.course,
            cohort=cls.cohort,
            slug='attempt-open',
            title='Open Attempt',
            submission_due_at=now + timedelta(days=3),
            review_due_at=now + timedelta(days=10),
            state=ProjectState.COLLECTING_SUBMISSIONS,
            evaluation_criteria=CRITERIA,
        )
        cls.grad_a = User.objects.create_user(email='grad-a@test.com')
        cls.grad_b = User.objects.create_user(email='grad-b@test.com')

    def _seed_passing_submissions(self):
        sub_a = ProjectSubmission.objects.create(
            user=self.grad_a,
            course=self.course,
            cohort=self.cohort,
            course_project=self.completed_project,
            project_url='https://github.com/example/a',
            status='review_complete',
        )
        sub_b = ProjectSubmission.objects.create(
            user=self.grad_b,
            course=self.course,
            cohort=self.cohort,
            course_project=self.completed_project,
            project_url='https://github.com/example/b',
            status='review_complete',
        )
        PeerReview.objects.create(
            submission=sub_a,
            reviewer=self.grad_b,
            is_complete=True,
            criteria_scores={'quality': 1},
        )
        PeerReview.objects.create(
            submission=sub_b,
            reviewer=self.grad_a,
            is_complete=True,
            criteria_scores={'quality': 1},
        )

    def test_permissions_enforce_staff_only_on_generate_and_preview(self):
        gen_url = reverse(
            'studio_peer_review_generate_certificates',
            kwargs={
                'course_id': self.course.pk,
                'project_id': self.completed_project.pk,
            },
        )
        prev_url = reverse(
            'studio_peer_review_preview_certificate',
            kwargs={
                'course_id': self.course.pk,
                'project_id': self.completed_project.pk,
            },
        )
        anon = Client()
        self.assertEqual(anon.post(gen_url).status_code, 302)
        self.assertEqual(anon.post(prev_url).status_code, 302)

        member_client = Client()
        member_client.force_login(self.member)
        self.assertEqual(member_client.post(gen_url).status_code, 403)
        self.assertEqual(member_client.post(prev_url).status_code, 403)

    def test_management_page_shows_scoped_controls_only_for_completed_attempt(self):
        self._seed_passing_submissions()
        client = Client()
        client.force_login(self.staff)
        url = reverse(
            'studio_peer_review_management',
            kwargs={'course_id': self.course.pk},
        )

        response = client.get(url)
        html = response.content.decode()

        self.assertIn('data-testid="attempt-certificate-controls"', html)
        self.assertIn('data-project="attempt-completed"', html)
        self.assertIn('Eligible: 2', html)
        self.assertIn('Missing: 2', html)
        self.assertIn('Already issued: 0', html)
        self.assertNotIn(
            f'projects/{self.open_project.pk}/certificates/generate',
            html,
        )

    @patch('content.services.attempt_certificates.render_certificate_pdf_to_s3')
    def test_staff_generate_action_is_idempotent_and_sends_no_email(self, mock_render):
        self._seed_passing_submissions()
        mock_render.side_effect = (
            lambda cert, course: (
                f'https://cdn.aishippinglabs.com/banners/certificates/{cert.id}.pdf'
            )
        )
        client = Client()
        client.force_login(self.staff)
        url = reverse(
            'studio_peer_review_generate_certificates',
            kwargs={
                'course_id': self.course.pk,
                'project_id': self.completed_project.pk,
            },
        )

        first = client.post(url, follow=True)
        self.assertContains(first, '2 created')
        self.assertEqual(CourseCertificate.objects.count(), 2)
        self.assertEqual(mock_render.call_count, 2)
        self.assertEqual(len(mail.outbox), 0)

        second = client.post(url, follow=True)
        self.assertContains(second, '0 created')
        self.assertContains(second, '2 already issued')
        self.assertEqual(mock_render.call_count, 2)

    @patch('content.services.attempt_certificates.render_certificate_preview_pdf')
    def test_staff_private_preview_returns_pdf_without_issuing(self, mock_preview):
        mock_preview.return_value = PrivatePreviewArtifact(
            content=b'%PDF-1.4 staff-preview',
            content_type='application/pdf',
            filename='buildcamp-preview.pdf',
        )
        client = Client()
        client.force_login(self.staff)
        url = reverse(
            'studio_peer_review_preview_certificate',
            kwargs={
                'course_id': self.course.pk,
                'project_id': self.completed_project.pk,
            },
        )

        response = client.post(url)

        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertEqual(response.content, b'%PDF-1.4 staff-preview')
        self.assertEqual(CourseCertificate.objects.count(), 0)
