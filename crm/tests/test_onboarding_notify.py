"""Staff notification when a member completes onboarding (issue #882).

Mirrors the plan-request fan-out: a member who submits onboarding (via the
#802 form OR the #804 AI chat) notifies every active staff user three ways
-- a best-effort Slack post OR (when Slack did not post) a staff email, and
ALWAYS one in-app ``onboarding_submitted`` Notification per active staff
user. The in-app notification deep-links to the member's CRM record when
tracked, else the Studio user-detail page (``/studio/users/<pk>/``).

These tests exercise the real submission views (form path) and the shared
``finalize_conversation`` finalizer (chat path) so the single notification
site is verified end to end.
"""

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from crm.models import CRMRecord
from notifications.models import Notification
from questionnaires.models import Response, ResponseQuestion

User = get_user_model()


@override_settings(ONBOARDING_AI_ENABLED='false')
class OnboardingFormNotifiesStaffTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='alice@test.com', password='pw', first_name='Alice',
        )
        cls.staff1 = User.objects.create_user(
            email='staff1@test.com', password='pw', is_staff=True,
        )
        cls.staff2 = User.objects.create_user(
            email='staff2@test.com', password='pw', is_staff=True,
        )
        # An inactive staff user must NOT be notified.
        cls.inactive_staff = User.objects.create_user(
            email='gone@test.com', password='pw',
            is_staff=True, is_active=False,
        )

    def setUp(self):
        self.client.force_login(self.member)
        self.client.post(reverse('onboarding_identify'), {'self_id': 'none'})
        self.response = Response.objects.get(respondent=self.member)
        self.required = ResponseQuestion.objects.create(
            response=self.response, source_question=None,
            question_type='text', prompt='Required one-off question',
            is_required=True, order=999,
        )

    def _submit(self, follow=False):
        post = {f'question_{self.required.pk}': 'an answer'}
        return self.client.post(
            reverse(
                'onboarding_submit',
                kwargs={'response_id': self.response.pk},
            ),
            post,
            follow=follow,
        )

    def test_submit_creates_one_notification_per_active_staff(self):
        self._submit()
        notifs = Notification.objects.filter(
            notification_type='onboarding_submitted',
        )
        self.assertEqual(notifs.count(), 2)
        self.assertEqual(
            set(notifs.values_list('user__email', flat=True)),
            {'staff1@test.com', 'staff2@test.com'},
        )
        # Inactive staff is never targeted.
        self.assertFalse(
            notifs.filter(user=self.inactive_staff).exists(),
        )

    def test_notification_titles_name_the_member(self):
        self._submit()
        notif = Notification.objects.filter(
            notification_type='onboarding_submitted',
        ).first()
        self.assertIn('Alice', notif.title)

    def test_links_to_studio_user_detail_when_no_crm_record(self):
        self._submit()
        notif = Notification.objects.filter(
            notification_type='onboarding_submitted',
        ).first()
        self.assertIn(
            f'/studio/users/{self.member.pk}/',
            notif.url,
        )
        # Never routes staff to the Django admin.
        self.assertNotIn('/admin/', notif.url)

    def test_email_links_to_studio_not_admin_when_no_crm_record(self):
        self._submit()
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertIn(f'/studio/users/{self.member.pk}/', body)
        self.assertNotIn('/admin/', body)

    def test_links_to_crm_record_when_tracked(self):
        record = CRMRecord.objects.create(user=self.member)
        self._submit()
        notif = Notification.objects.filter(
            notification_type='onboarding_submitted',
        ).first()
        self.assertIn(f'/studio/crm/{record.pk}/', notif.url)
        self.assertNotIn('/admin/', notif.url)

    def test_emails_staff_when_slack_disabled(self):
        # SLACK_ENABLED is false by default in tests -> email fallback.
        self._submit()
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(
            set(sent.to), {'staff1@test.com', 'staff2@test.com'},
        )
        self.assertIn(self.member.email, sent.subject)

    def test_member_sees_success_and_lands_on_dashboard(self):
        resp = self._submit(follow=True)
        self.response.refresh_from_db()
        self.assertEqual(self.response.status, 'submitted')
        msgs = [m.message for m in resp.context['messages']]
        self.assertTrue(any('plan' in m.lower() for m in msgs))

    def test_resubmit_does_not_double_notify(self):
        self._submit()
        baseline = Notification.objects.filter(
            notification_type='onboarding_submitted',
        ).count()
        self.assertEqual(baseline, 2)
        mail.outbox.clear()
        # Re-POST the already-submitted response.
        resp = self._submit(follow=True)
        msgs = [m.message for m in resp.context['messages']]
        self.assertTrue(any('already' in m.lower() for m in msgs))
        # No second round of notifications and no second email.
        self.assertEqual(
            Notification.objects.filter(
                notification_type='onboarding_submitted',
            ).count(),
            2,
        )
        self.assertEqual(len(mail.outbox), 0)


@override_settings(
    ONBOARDING_AI_ENABLED='false',
    SLACK_ENABLED=True,
    SLACK_BOT_TOKEN='xoxb-fake',
)
class OnboardingSlackChannelTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='bob@test.com', password='pw', first_name='Bob',
        )
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client.force_login(self.member)
        self.client.post(reverse('onboarding_identify'), {'self_id': 'none'})
        self.response = Response.objects.get(respondent=self.member)
        self.required = ResponseQuestion.objects.create(
            response=self.response, source_question=None,
            question_type='text', prompt='Q', is_required=True, order=999,
        )

    def _submit(self):
        return self.client.post(
            reverse(
                'onboarding_submit',
                kwargs={'response_id': self.response.pk},
            ),
            {f'question_{self.required.pk}': 'a'},
        )

    def test_posts_to_slack_and_skips_email_when_enabled(self):
        with patch(
            'community.slack_config.get_slack_team_requests_channel_id',
            return_value='C123',
        ):
            with patch('requests.post') as mock_post:
                mock_post.return_value.json.return_value = {'ok': True}
                self._submit()
        self.assertEqual(mock_post.call_count, 1)
        payload = mock_post.call_args.kwargs['json']
        self.assertEqual(payload['channel'], 'C123')
        self.assertIn('onboarding', json.dumps(payload).lower())
        # Slack succeeded -> email fallback NOT used.
        self.assertEqual(len(mail.outbox), 0)
        # In-app notification ALWAYS created.
        self.assertEqual(
            Notification.objects.filter(
                notification_type='onboarding_submitted',
            ).count(),
            1,
        )

    def test_email_fallback_when_no_channel_configured(self):
        with patch(
            'community.slack_config.get_slack_team_requests_channel_id',
            return_value='',
        ):
            with patch('requests.post') as mock_post:
                self._submit()
        # No channel -> never attempts the Slack API.
        self.assertEqual(mock_post.call_count, 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            Notification.objects.filter(
                notification_type='onboarding_submitted',
            ).count(),
            1,
        )


@override_settings(ONBOARDING_AI_ENABLED='false')
class OnboardingNotifyBestEffortTest(TestCase):
    """A notification-plumbing failure must not break the submission."""

    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='carol@test.com', password='pw',
        )
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client.force_login(self.member)
        self.client.post(reverse('onboarding_identify'), {'self_id': 'none'})
        self.response = Response.objects.get(respondent=self.member)
        self.required = ResponseQuestion.objects.create(
            response=self.response, source_question=None,
            question_type='text', prompt='Q', is_required=True, order=999,
        )

    def test_submission_succeeds_even_if_notifier_raises(self):
        with patch(
            'crm.services.onboarding_notify'
            '._create_staff_onboarding_notifications',
            side_effect=RuntimeError('boom'),
        ):
            resp = self.client.post(
                reverse(
                    'onboarding_submit',
                    kwargs={'response_id': self.response.pk},
                ),
                {f'question_{self.required.pk}': 'a'},
                follow=True,
            )
        # Member's onboarding is marked submitted and the thank-you shows.
        self.response.refresh_from_db()
        self.assertEqual(self.response.status, 'submitted')
        msgs = [m.message for m in resp.context['messages']]
        self.assertTrue(any('plan' in m.lower() for m in msgs))


class OnboardingChatFinalizeNotifiesStaffTest(TestCase):
    """The AI-chat finalizer is the same single notification site (#882)."""

    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='dana@test.com', password='pw', first_name='Dana',
        )
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def test_finalize_conversation_notifies_staff(self):
        from questionnaires.onboarding import (
            get_generic_onboarding_questionnaire,
        )
        from questionnaires.services_onboarding_ai import finalize_conversation

        generic = get_generic_onboarding_questionnaire()
        self.assertIsNotNone(generic)
        response = Response.objects.create(
            questionnaire=generic, respondent=self.member, status='draft',
        )
        from questionnaires.models import OnboardingConversation

        conversation = OnboardingConversation.objects.create(
            response=response, transcript=[],
        )

        # A completed turn result with no extracted answers / signal.
        class _Result:
            is_complete = True
            extraction = None
            answers = []

        finalize_conversation(conversation, _Result())

        response.refresh_from_db()
        self.assertEqual(response.status, 'submitted')
        notifs = Notification.objects.filter(
            notification_type='onboarding_submitted', user=self.staff,
        )
        self.assertEqual(notifs.count(), 1)
        self.assertIn('Dana', notifs.first().title)
