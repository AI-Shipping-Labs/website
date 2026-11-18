"""Tests for coursework.review_assigned emails during peer-review batching."""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from community_base.mail.jobs import deliver as deliver_job
from community_base.mail.models import EmailDelivery
from community_base.mail.service import MailError
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from content.models import (
    Cohort,
    CohortEnrollment,
    Course,
    CourseProject,
    ProjectSubmission,
)
from content.models.peer_review import ProjectState
from content.services.peer_review_service import PeerReviewService
from email_app.testing import StubRelayClient
from notifications.models import Notification

User = get_user_model()

SITE = 'https://aishippinglabs.com'


@override_settings(SITE_BASE_URL=SITE)
class PeerReviewAssignedEmailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='AI Buildcamp',
            slug='ai-buildcamp',
            peer_review_enabled=True,
            peer_review_count=2,
            peer_review_deadline_days=7,
        )
        now = timezone.now()
        cls.cohort = Cohort.objects.create(
            course=cls.course,
            name='Cohort 4',
            external_key='4',
            start_date=(now - timedelta(days=20)).date(),
            end_date=(now + timedelta(days=10)).date(),
        )
        cls.project = CourseProject.objects.create(
            course=cls.course,
            cohort=cls.cohort,
            slug='attempt-1',
            title='Attempt 1',
            state=ProjectState.COLLECTING_SUBMISSIONS,
            submission_due_at=now - timedelta(hours=2),
            review_due_at=now + timedelta(days=5),
            peer_review_count=2,
        )
        cls.learners = [
            User.objects.create_user(
                email=f'learner-{idx}@test.com',
                first_name=f'Learner{idx}',
                preferred_timezone='Europe/Berlin',
            )
            for idx in range(3)
        ]
        cls.submissions = []
        for learner in cls.learners:
            CohortEnrollment.objects.create(cohort=cls.cohort, user=learner)
            cls.submissions.append(
                ProjectSubmission.objects.create(
                    user=learner,
                    course=cls.course,
                    cohort=cls.cohort,
                    course_project=cls.project,
                    project_url=f'https://github.com/{learner.first_name.lower()}/repo',
                )
            )

    def test_dated_batch_queues_and_resolves_review_assigned_emails(self):
        result = PeerReviewService.form_batches_for_course(self.course)
        self.assertEqual(result['batched'], 3)
        self.assertEqual(result['reviews_assigned'], 6)

        deliveries = EmailDelivery.objects.filter(
            purpose='coursework.review_assigned',
            recipient_user__in=self.learners,
        ).order_by('id')
        self.assertEqual(deliveries.count(), 3)

        first = deliveries.first()
        self.assertEqual(first.context_data['course_slug'], 'ai-buildcamp')
        self.assertEqual(first.context_data['project_slug'], 'attempt-1')
        self.assertEqual(first.context_data['project_title'], 'Attempt 1')
        self.assertEqual(first.context_data['review_count'], 2)

        stub = StubRelayClient()
        with patch(
            'community_base.mail.backends.relay.configured_client',
            return_value=stub,
        ):
            deliver_job(None, {'delivery_id': str(first.id)})

        (call,) = stub.calls
        context = call['context']
        self.assertEqual(
            context['review_list_url'],
            f'{SITE}/courses/ai-buildcamp/projects/attempt-1/reviews',
        )
        self.assertEqual(
            context['projects_url'],
            f'{SITE}/courses/ai-buildcamp/home/projects',
        )
        self.assertIn('Europe/Berlin', context['due_date_display'])

    def test_notify_batch_ready_is_idempotent_for_emails(self):
        PeerReviewService.form_batches_for_course(self.course)
        self.assertEqual(
            EmailDelivery.objects.filter(purpose='coursework.review_assigned').count(),
            3,
        )

        # Calling _notify_batch_ready again must not create duplicate EmailDelivery rows.
        PeerReviewService._notify_batch_ready(
            self.course,
            self.submissions,
            self.project.review_due_at,
            project=self.project,
        )
        self.assertEqual(
            EmailDelivery.objects.filter(purpose='coursework.review_assigned').count(),
            3,
        )

    def test_email_failure_for_one_learner_does_not_block_batch_or_peers(self):
        failing_email = self.learners[0].email

        def fail_first_learner(user, *args, **kwargs):
            if user.email == failing_email:
                raise MailError('simulated failure')
            return EmailDelivery.objects.create(
                purpose='coursework.review_assigned',
                recipient_user=user,
                recipient_email=user.email,
                idempotency_key=kwargs['idempotency_key'],
            )

        with patch('email_app.package_mail.send_package_mail', side_effect=fail_first_learner):
            result = PeerReviewService.form_batches_for_course(self.course)

        self.assertEqual(result['batched'], 3)
        self.assertEqual(Notification.objects.filter(user__in=self.learners).count(), 3)
        self.assertEqual(
            EmailDelivery.objects.filter(purpose='coursework.review_assigned').count(),
            2,
        )

    def test_studio_and_command_entrypoints_send_emails(self):
        staff = User.objects.create_user(email='staff@test.com', is_staff=True)
        self.client.force_login(staff)
        response = self.client.post(
            reverse('studio_peer_review_form_batch', kwargs={'course_id': self.course.pk}),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            EmailDelivery.objects.filter(purpose='coursework.review_assigned').count(),
            3,
        )

    def test_management_command_sends_emails(self):
        out = StringIO()
        call_command('form_review_batches', stdout=out)
        self.assertEqual(
            EmailDelivery.objects.filter(purpose='coursework.review_assigned').count(),
            3,
        )


@override_settings(SITE_BASE_URL=SITE)
class SelfPacedReviewAssignedEmailTests(TestCase):
    def test_self_paced_batch_sends_email_without_due_date(self):
        course = Course.objects.create(
            title='Self Paced Course',
            slug='self-paced-course',
            peer_review_enabled=True,
            peer_review_count=1,
        )
        cohort = Cohort.objects.create(
            course=course, name='Self-paced', mode='self_paced', is_active=True,
        )
        project = CourseProject.objects.create(
            course=course,
            cohort=cohort,
            slug='capstone',
            title='Capstone',
            submission_due_at=None,
            review_due_at=None,
            peer_review_count=1,
        )
        learners = []
        for idx in range(2):
            user = User.objects.create_user(email=f'sp-{idx}@test.com', first_name=f'SP{idx}')
            CohortEnrollment.objects.create(cohort=cohort, user=user)
            ProjectSubmission.objects.create(
                user=user,
                course=course,
                cohort=cohort,
                course_project=project,
                project_url=f'https://example.com/{idx}',
            )
            learners.append(user)

        result = PeerReviewService.form_self_paced_batches_for_project(project)
        self.assertEqual(result['batched'], 2)
        deliveries = EmailDelivery.objects.filter(
            purpose='coursework.review_assigned',
            recipient_user__in=learners,
        )
        self.assertEqual(deliveries.count(), 2)
        self.assertIsNone(deliveries.first().context_data['due_date'])
