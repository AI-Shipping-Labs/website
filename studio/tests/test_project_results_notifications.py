"""Tests for AISL Studio project results email preview, send, retry, and mail delivery."""

from datetime import timedelta
from unittest.mock import patch

from community_base.coursework.models import ProjectResultsNotification
from community_base.mail.jobs import deliver as deliver_job
from community_base.mail.models import EmailDelivery
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from content.models import Cohort, Course, CourseProject, ProjectSubmission
from content.models.peer_review import ProjectState
from email_app.services.context_guard import ensure_no_rendered_urls
from email_app.testing import StubRelayClient

User = get_user_model()

SITE = 'https://aishippinglabs.com'


class AISLProjectResultsNotificationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(title='AI Buildcamp', slug='ai-buildcamp')
        cls.cohort = Cohort.objects.create(
            course=cls.course,
            name='Fall 2026',
            external_key='fall-2026',
            mode='cohort',
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timedelta(days=30),
        )
        now = timezone.now()
        cls.project = CourseProject.objects.create(
            course=cls.course,
            cohort=cls.cohort,
            slug='attempt-1',
            title='Attempt 1',
            state=ProjectState.COMPLETED,
            submission_due_at=now - timedelta(days=7),
            review_due_at=now - timedelta(days=1),
        )
        cls.other_project = CourseProject.objects.create(
            course=cls.course,
            cohort=cls.cohort,
            slug='attempt-2',
            title='Attempt 2',
            state=ProjectState.PEER_REVIEWING,
            submission_due_at=now - timedelta(days=3),
            review_due_at=now + timedelta(days=4),
        )
        cls.staff = User.objects.create_user(
            email='staff@aishippinglabs.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@aishippinglabs.com', password='pw', is_staff=False,
        )
        cls.ada = User.objects.create_user(email='ada@test.com', first_name='Ada', password='pw')
        cls.bob = User.objects.create_user(email='bob@test.com', first_name='Bob', password='pw')
        cls.other_user = User.objects.create_user(email='other@test.com', password='pw')

        ProjectSubmission.objects.create(
            user=cls.ada,
            course=cls.course,
            cohort=cls.cohort,
            course_project=cls.project,
            project_url='https://github.com/ada/proj',
            status='review_complete',
        )
        ProjectSubmission.objects.create(
            user=cls.bob,
            course=cls.course,
            cohort=cls.cohort,
            course_project=cls.project,
            project_url='https://github.com/bob/proj',
            status='submitted',
        )
        ProjectSubmission.objects.create(
            user=cls.other_user,
            course=cls.course,
            cohort=cls.cohort,
            course_project=cls.other_project,
            project_url='https://github.com/other/proj',
            status='submitted',
        )

    def test_preview_send_delivery_and_retry(self):
        preview_url = reverse(
            'studio_peer_review_results_notifications',
            kwargs={'course_id': self.course.pk, 'project_id': self.project.pk},
        )
        send_url = reverse(
            'studio_peer_review_results_notifications_send',
            kwargs={'course_id': self.course.pk, 'project_id': self.project.pk},
        )
        retry_url = reverse(
            'studio_peer_review_results_notifications_retry',
            kwargs={'course_id': self.course.pk, 'project_id': self.project.pk},
        )

        # Non-staff cannot access.
        self.client.force_login(self.member)
        self.assertIn(self.client.get(preview_url).status_code, (302, 403))
        self.assertIn(self.client.post(send_url).status_code, (302, 403))

        # Staff preview is read-only and shows exact eligible count (2).
        self.client.force_login(self.staff)
        resp = self.client.get(preview_url)
        self.assertContains(resp, 'data-testid="eligible-count">2<')
        self.assertEqual(EmailDelivery.objects.count(), 0)

        # Uncompleted project is blocked on send.
        other_send_url = reverse(
            'studio_peer_review_results_notifications_send',
            kwargs={'course_id': self.course.pk, 'project_id': self.other_project.pk},
        )
        self.client.post(other_send_url)
        self.assertEqual(EmailDelivery.objects.count(), 0)

        # Explicit send queues exactly the 2 submitters of attempt-1.
        self.client.post(send_url)
        self.assertEqual(ProjectResultsNotification.objects.count(), 1)
        deliveries = list(
            EmailDelivery.objects.filter(purpose='coursework.project_results').order_by('id'),
        )
        self.assertEqual(len(deliveries), 2)
        self.assertEqual(
            {d.recipient_email for d in deliveries},
            {'ada@test.com', 'bob@test.com'},
        )

        # Durable context stores relative path only (#1613); worker resolves absolute URL.
        with override_settings(SITE_BASE_URL=SITE):
            for delivery in deliveries:
                ensure_no_rendered_urls('coursework.project_results', delivery.context_data)
                stub = StubRelayClient()
                with patch(
                    'community_base.mail.backends.relay.configured_client',
                    return_value=stub,
                ):
                    deliver_job(None, {'delivery_id': str(delivery.id)})
                (call,) = stub.calls
                self.assertEqual(
                    call['context']['results_url'],
                    f'{SITE}/courses/ai-buildcamp/projects/attempt-1/reviews',
                )

        # Mark one dead and retry via Studio POST.
        deliveries[0].state = EmailDelivery.State.DEAD
        deliveries[0].save(update_fields=['state'])
        self.client.post(retry_url)
        self.assertEqual(
            EmailDelivery.objects.filter(purpose='coursework.project_results').count(),
            3,
        )
