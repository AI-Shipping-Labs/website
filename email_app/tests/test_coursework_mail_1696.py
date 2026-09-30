"""Coursework peer-review mail renders from AISL file templates (#1696).

``community_base.coursework.notifications`` sends the four purposes itself.
Each test sends through the package function, then delivers the durable row
with the ``ses_local`` worker and a stub SES client, so it covers the
template file, the transactional classification, the review URL builder and
the worker hook that makes the links absolute.
"""

import datetime
import os
from unittest.mock import patch

from community_base.coursework import notifications
from community_base.coursework.models import (
    PeerReview,
    PeerReviewBatch,
    ProjectSubmission,
)
from community_base.mail.jobs import deliver as deliver_job
from community_base.mail.models import EmailDelivery
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from content.models import Cohort, Course
from content.services.coursework_bridge import ensure_curriculum_enrollment
from email_app.services.context_guard import ensure_no_rendered_urls
from email_app.testing import StubSESClient

User = get_user_model()

SITE = 'https://aishippinglabs.com'
REVIEWS = f'{SITE}/courses/ai-buildcamp/projects/attempt-1/reviews'
PROJECTS = f'{SITE}/courses/ai-buildcamp/home/projects'


def _deliver_latest(purpose):
    delivery = EmailDelivery.objects.filter(purpose=purpose).latest('created_at')
    # The durable row stores no link (#1613); the worker renders them.
    ensure_no_rendered_urls(purpose, delivery.context_data)
    stub = StubSESClient()
    with patch(
        'community_base.mail.backends.ses_local.configured_client',
        return_value=stub,
    ):
        deliver_job(None, {'delivery_id': str(delivery.id)})
    (call,) = stub.calls
    simple = call['Content']['Simple']
    return delivery, simple['Subject']['Data'], simple['Body']['Html']['Data']


@override_settings(SES_ENABLED=False, SITE_BASE_URL=SITE)
class CourseworkMailTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        course = Course.objects.create(title='AI Buildcamp', slug='ai-buildcamp')
        cohort = Cohort.objects.create(course=course, name='Self-paced', mode='self_paced')
        cls.ada = User.objects.create_user(
            email='ada@test.com', first_name='Ada',
            preferred_timezone='Europe/Berlin',
        )
        cls.bob = User.objects.create_user(email='bob@test.com', first_name='Bob')
        mirror = ensure_curriculum_enrollment(cls.ada, cohort).cohort
        ensure_curriculum_enrollment(cls.bob, cohort)
        cls.project = mirror.projects.create(
            slug='attempt-1', title='Attempt 1', commit_id_field=False,
        )
        cls.ada_submission, cls.bob_submission = (
            ProjectSubmission.objects.create(
                project=cls.project,
                student=user,
                enrollment=user.curriculum_enrollments.get(),
                github_link=f'https://github.com/{user.first_name.lower()}/project',
            )
            for user in (cls.ada, cls.bob)
        )
        cls.due_at = datetime.datetime(2026, 11, 23, 22, 59, tzinfo=datetime.UTC)
        cls.batch = PeerReviewBatch.objects.create(project=cls.project, due_at=cls.due_at)
        cls.ada_reviews_bob = PeerReview.objects.create(
            submission_under_evaluation=cls.bob_submission,
            reviewer=cls.ada_submission,
            batch=cls.batch,
        )
        cls.bob_reviews_ada = PeerReview.objects.create(
            submission_under_evaluation=cls.ada_submission,
            reviewer=cls.bob_submission,
            batch=cls.batch,
        )

    def setUp(self):
        # The package sends without a per-purpose sender, so ses_local falls
        # back to SES_FROM_EMAIL (phase 6 follow-up: the transactional sender
        # and the SES configuration set for package-originated mail).
        env = patch.dict(os.environ, {'SES_FROM_EMAIL': 'noreply@aishippinglabs.com'})
        env.start()
        self.addCleanup(env.stop)

    def test_pool_ready_links_each_review_and_formats_the_due_date(self):
        notifications.send_pool_ready_notification(
            self.batch, self.ada_submission,
            [self.ada_reviews_bob, self.bob_reviews_ada],
        )

        delivery, subject, html = _deliver_latest('coursework.pool_ready')

        self.assertEqual(delivery.recipient_email, 'ada@test.com')
        self.assertEqual(subject, 'Review 1 project for Attempt 1')
        self.assertIn('Hi Ada,', html)
        self.assertIn(f'href="{REVIEWS}/{self.ada_reviews_bob.id}"', html)
        self.assertIn(f'href="{REVIEWS}"', html)
        self.assertIn('November 23, 2026', html)
        self.assertIn('Europe/Berlin', html)

    def test_review_assigned_links_the_review_list(self):
        self.project.peer_review_due_date = self.due_at
        self.project.save(update_fields=['peer_review_due_date'])
        self.bob_reviews_ada.batch = None
        self.bob_reviews_ada.save(update_fields=['batch'])

        notifications.send_review_assigned_notifications([self.bob_reviews_ada])

        delivery, subject, html = _deliver_latest('coursework.review_assigned')

        self.assertEqual(delivery.recipient_email, 'bob@test.com')
        self.assertEqual(subject, 'Peer reviews are open for Attempt 1')
        self.assertIn(f'href="{REVIEWS}"', html)
        self.assertIn('November 23, 2026, 22:59 UTC', html)

    def test_review_received_links_the_projects_tab(self):
        notifications.send_review_received_notification(self.bob_reviews_ada)

        delivery, subject, html = _deliver_latest('coursework.review_received')

        self.assertEqual(delivery.recipient_email, 'ada@test.com')
        self.assertEqual(subject, 'A peer reviewed your project Attempt 1')
        self.assertIn(f'href="{PROJECTS}"', html)

    def test_review_window_expired_is_delivered_to_an_unsubscribed_reviewer(self):
        # Transactional: the newsletter opt-out does not suppress it.
        self.ada.unsubscribed = True
        self.ada.save(update_fields=['unsubscribed'])

        notifications.send_review_expired_notification(self.ada_reviews_bob)

        delivery, subject, html = _deliver_latest('coursework.review_window_expired')

        self.assertEqual(delivery.recipient_email, 'ada@test.com')
        self.assertEqual(subject, 'Your review window for Attempt 1 has closed')
        self.assertIn(f'href="{PROJECTS}"', html)
