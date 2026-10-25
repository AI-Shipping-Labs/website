"""Staff email sent when a learner unenrolls from a course."""

import datetime
from unittest.mock import patch

from community_base.mail.jobs import deliver as deliver_job
from community_base.mail.models import EmailDelivery
from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import User
from community.services.staff_notifications import notify_course_unenroll
from content.models import Cohort, CohortEnrollment, Course, Enrollment
from email_app.testing import StubRelayClient
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting

PURPOSE = 'course_unenroll_notification'


def _drain(delivery):
    """Deliver one delivery through the real worker and return its Relay call."""
    stub = StubRelayClient()
    with patch(
        'community_base.mail.backends.relay.configured_client',
        return_value=stub,
    ):
        deliver_job(None, {'delivery_id': str(delivery.id)})
    return stub.calls[0]


@tag('core')
class NotifyCourseUnenrollTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.learner = User.objects.create_user(
            email='leaver@example.com', first_name='Lee', last_name='Ver',
        )
        cls.staff = User.objects.create_user(
            email='ops@example.com', is_staff=True,
        )
        cls.course = Course.objects.create(
            title='AI Engineering Buildcamp', slug='notify-buildcamp',
            status='published',
        )
        today = timezone.localdate()
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 4', external_key='c4',
            start_date=today - datetime.timedelta(days=7),
            end_date=today + datetime.timedelta(days=30),
        )
        stayer = User.objects.create_user(email='stayer@example.com')
        Enrollment.objects.create(user=stayer, course=cls.course)
        CohortEnrollment.objects.create(cohort=cls.cohort, user=stayer)

    def setUp(self):
        IntegrationSetting.objects.update_or_create(
            key='STAFF_SIGNUP_NOTIFY_EMAIL',
            defaults={'value': 'staff@example.com'},
        )
        clear_config_cache()
        self.addCleanup(clear_config_cache)

    def test_emails_staff_learner_course_cohort_cause_and_count(self):
        self.assertTrue(notify_course_unenroll(
            self.learner.pk, self.course.pk,
            cohort_id=self.cohort.pk, cause='staff', actor_id=self.staff.pk,
        ))

        call = _drain(EmailDelivery.objects.get(purpose=PURPOSE))
        self.assertEqual(call['delivery'].recipient_email, 'staff@example.com')
        self.assertEqual(
            call['context']['studio_user_url'],
            f'https://aishippinglabs.com/studio/users/{self.learner.pk}/',
        )
        self.assertEqual(call['context']['name'], 'Lee Ver')
        self.assertEqual(call['context']['course'], 'AI Engineering Buildcamp')
        self.assertEqual(call['context']['cohort'], 'Cohort 4')
        self.assertEqual(call['context']['cause_sentence'], 'Removed by ops@example.com.')
        self.assertEqual(call['context']['enrolled_count'], 1)

    def test_self_unenroll_without_cohort_names_only_the_course(self):
        notify_course_unenroll(self.learner.pk, self.course.pk, cause='self')

        call = _drain(EmailDelivery.objects.get(purpose=PURPOSE))
        self.assertEqual(call['context']['cohort'], '')
        self.assertEqual(call['context']['cause_sentence'], 'They did it themselves.')

    def test_skips_when_staff_mailbox_blank(self):
        IntegrationSetting.objects.filter(key='STAFF_SIGNUP_NOTIFY_EMAIL').delete()
        clear_config_cache()

        self.assertFalse(notify_course_unenroll(self.learner.pk, self.course.pk))
        self.assertFalse(EmailDelivery.objects.filter(purpose=PURPOSE).exists())

    def test_send_failure_never_raises(self):
        with patch(
            'community.services.staff_notifications.send_package_mail',
            side_effect=RuntimeError('boom'),
        ):
            self.assertFalse(
                notify_course_unenroll(self.learner.pk, self.course.pk),
            )
