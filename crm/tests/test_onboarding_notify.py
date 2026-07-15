"""Staff notification when a member completes onboarding (issue #882).

Mirrors the plan-request fan-out: a member who submits onboarding (via the
#802 form OR the #804 AI chat) notifies every active staff user three ways
-- a best-effort Slack post OR (when Slack did not post) a staff email, and
ALWAYS one in-app ``onboarding_submitted`` Notification per active staff
user. The in-app notification deep-links to the member's CRM onboarding
section, creating the CRM record if needed.

These tests exercise the real submission views (form path) and the shared
``finalize_conversation`` finalizer (chat path) so the single notification
site is verified end to end.
"""

import json
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crm.models import CRMRecord
from notifications.models import Notification
from payments.models import Tier
from questionnaires.models import Response, ResponseQuestion

User = get_user_model()


@override_settings(ONBOARDING_AI_ENABLED='false')
class OnboardingFormNotifiesStaffTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Issue #982: onboarding is paid-gated, so the member who drives the
        # real submission views must be on a paid (Basic) tier to enter the
        # flow at all.
        cls.member = User.objects.create_user(
            email='alice@test.com', password='pw', first_name='Alice',
            tier=Tier.objects.get(slug='basic'),
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

    def test_submit_auto_creates_crm_record_and_links_to_onboarding_anchor(self):
        self.assertFalse(CRMRecord.objects.filter(user=self.member).exists())
        self._submit()
        record = CRMRecord.objects.get(user=self.member)
        self.assertEqual(record.status, 'active')
        self.assertIsNone(record.created_by)

        notif = Notification.objects.filter(
            notification_type='onboarding_submitted',
        ).first()
        self.assertIn(
            f'/studio/crm/{record.pk}/#onboarding',
            notif.url,
        )
        # Never routes staff to the Django admin.
        self.assertNotIn('/admin/', notif.url)

    def test_email_links_to_crm_onboarding_not_admin_when_created(self):
        self._submit()
        record = CRMRecord.objects.get(user=self.member)
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertIn(f'/studio/crm/{record.pk}/#onboarding', body)
        self.assertIn('Open onboarding in CRM', body)
        self.assertNotIn('/admin/', body)

    def test_existing_crm_record_is_reused_and_curated_fields_preserved(self):
        record = CRMRecord.objects.create(
            user=self.member,
            status='active',
            persona='Curated persona',
            summary='Curated summary',
            next_steps='Curated next steps',
        )
        self._submit()
        record.refresh_from_db()
        self.assertEqual(record.persona, 'Curated persona')
        self.assertEqual(record.summary, 'Curated summary')
        self.assertEqual(record.next_steps, 'Curated next steps')
        self.assertEqual(CRMRecord.objects.filter(user=self.member).count(), 1)
        notif = Notification.objects.filter(
            notification_type='onboarding_submitted',
        ).first()
        self.assertIn(f'/studio/crm/{record.pk}/#onboarding', notif.url)
        self.assertNotIn('/admin/', notif.url)

    def test_archived_crm_record_is_reused_without_reactivation(self):
        record = CRMRecord.objects.create(
            user=self.member,
            status='archived',
            summary='Keep archived context',
        )
        self._submit()
        record.refresh_from_db()
        self.assertEqual(record.status, 'archived')
        self.assertEqual(record.summary, 'Keep archived context')
        self.assertEqual(CRMRecord.objects.filter(user=self.member).count(), 1)

        notif = Notification.objects.filter(
            notification_type='onboarding_submitted',
        ).first()
        self.assertIn(f'/studio/crm/{record.pk}/#onboarding', notif.url)

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
        record = CRMRecord.objects.get(user=self.member)
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
        self.assertEqual(CRMRecord.objects.filter(user=self.member).count(), 1)
        self.assertEqual(CRMRecord.objects.get(user=self.member).pk, record.pk)


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
            tier=Tier.objects.get(slug='basic'),
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
        self.assertIn('Open onboarding in CRM', json.dumps(payload))
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
            tier=Tier.objects.get(slug='basic'),
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

    def test_crm_create_failure_logs_and_falls_back_to_user_detail(self):
        with self.assertLogs(
            'crm.services.onboarding_notify', level='ERROR',
        ) as logs:
            with patch(
                'crm.services.onboarding_notify.ensure_onboarding_crm_record',
                side_effect=RuntimeError('crm down'),
            ):
                resp = self.client.post(
                    reverse(
                        'onboarding_submit',
                        kwargs={'response_id': self.response.pk},
                    ),
                    {f'question_{self.required.pk}': 'a'},
                    follow=True,
                )

        self.response.refresh_from_db()
        self.assertEqual(self.response.status, 'submitted')
        msgs = [m.message for m in resp.context['messages']]
        self.assertTrue(any('plan' in m.lower() for m in msgs))
        self.assertFalse(CRMRecord.objects.filter(user=self.member).exists())
        self.assertTrue(any('Failed to auto-create/find CRM record' in line for line in logs.output))
        notif = Notification.objects.get(
            notification_type='onboarding_submitted',
            user=self.staff,
        )
        self.assertIn(f'/studio/users/{self.member.pk}/', notif.url)
        self.assertNotIn('/admin/', notif.url)


class OnboardingChatFinalizeNotifiesStaffTest(TestCase):
    """The queued AI-chat delivery uses the shared notification site."""

    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='dana@test.com', password='pw', first_name='Dana',
        )
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def test_finalized_conversation_notification_task_notifies_staff(self):
        from questionnaires.onboarding import (
            get_generic_onboarding_questionnaire,
        )
        from questionnaires.services_onboarding_ai import finalize_conversation

        generic = get_generic_onboarding_questionnaire()
        self.assertIsNotNone(generic)
        response = Response.objects.create(
            questionnaire=generic, respondent=self.member, status='draft',
        )
        from questionnaires.models import (
            OnboardingConversation,
            OnboardingTurnAttempt,
        )

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
        now = timezone.now()
        attempt = OnboardingTurnAttempt.objects.create(
            conversation=conversation,
            request_id=uuid4(),
            member_message_hash='0' * 64,
            admitted_version=conversation.turn_version,
            transport='stream',
            status='succeeded',
            outcome='final',
            started_at=now,
            completed_at=now,
            lease_expires_at=now,
            notification_status='pending',
        )

        from questionnaires.tasks import send_onboarding_staff_notification
        result = send_onboarding_staff_notification(attempt.pk)

        self.assertEqual(result['status'], 'succeeded')
        record = CRMRecord.objects.get(user=self.member)
        notifs = Notification.objects.filter(
            notification_type='onboarding_submitted', user=self.staff,
        )
        self.assertEqual(notifs.count(), 1)
        self.assertIn('Dana', notifs.first().title)
        self.assertIn(f'/studio/crm/{record.pk}/#onboarding', notifs.first().url)
