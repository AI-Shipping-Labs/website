"""Tests for sprint partner intro email service (#1124)."""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag

from email_app.models import EmailLog
from email_app.services.email_classification import (
    EMAIL_KIND_TRANSACTIONAL,
    TRANSACTIONAL_EMAIL_TYPES,
    classify_email_type,
)
from plans.models import (
    PARTNER_INTRO_EMAIL_STATUS_FAILED,
    PARTNER_INTRO_EMAIL_STATUS_SENT,
    Plan,
    Sprint,
    SprintAccountabilityPartner,
    SprintEnrollment,
    SprintPartnerIntroEmailLog,
)
from plans.services import (
    assign_accountability_partners,
    preview_partner_intro_emails,
    send_partner_intro_emails,
)

User = get_user_model()


@tag('core')
class PartnerIntroEmailServiceTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.sprint = Sprint.objects.create(
            name='May Sprint',
            slug='may-sprint',
            start_date=datetime.date(2026, 5, 1),
            status='active',
        )

    def _member(self, email, **fields):
        return User.objects.create_user(email=email, password='pw', **fields)

    def _enroll(self, user, *, with_plan=True):
        SprintEnrollment.objects.get_or_create(
            sprint=self.sprint,
            user=user,
            defaults={'enrolled_by': self.staff},
        )
        if with_plan:
            Plan.objects.get_or_create(member=user, sprint=self.sprint)

    def _ready_pair(self, *, bob_kwargs=None):
        alice = self._member('alice@test.com')
        bob = self._member('bob@test.com', **(bob_kwargs or {}))
        self._enroll(alice)
        self._enroll(bob)
        assign_accountability_partners(
            sprint=self.sprint,
            member=alice,
            partner=bob,
            assigned_by=self.staff,
        )
        return alice, bob

    def test_transactional_classification(self):
        self.assertIn('sprint_partner_intro', TRANSACTIONAL_EMAIL_TYPES)
        self.assertEqual(
            classify_email_type('sprint_partner_intro'),
            EMAIL_KIND_TRANSACTIONAL,
        )

    def test_readiness_requires_active_sprint_plans_and_enrolled_partners(self):
        alice = self._member('alice@test.com')
        bob = self._member('bob@test.com')
        carol = self._member('carol@test.com')
        self._enroll(alice)
        self._enroll(bob)
        self._enroll(carol, with_plan=False)
        assign_accountability_partners(
            sprint=self.sprint,
            member=alice,
            partner=bob,
            assigned_by=self.staff,
        )
        assign_accountability_partners(
            sprint=self.sprint,
            member=bob,
            partner=carol,
            assigned_by=self.staff,
        )

        summary = preview_partner_intro_emails(self.sprint)

        self.assertFalse(summary['send_ready'])
        self.assertEqual(summary['total_enrolled'], 3)
        self.assertEqual(summary['eligible_count'], 0)
        self.assertEqual(summary['missing_plan_count'], 1)
        self.assertEqual(summary['missing_partner_count'], 0)
        self.assertEqual(summary['missing_plan'][0]['member_email'], 'carol@test.com')
        self.assertIn('missing_plans', {row['code'] for row in summary['blockers']})

    def test_unenrolled_partner_edges_are_ignored_and_reported(self):
        alice = self._member('alice@test.com')
        bob = self._member('bob@test.com')
        outsider = self._member('outsider@test.com')
        self._enroll(alice)
        self._enroll(bob)
        SprintAccountabilityPartner.objects.create(
            sprint=self.sprint,
            member=alice,
            partner=outsider,
            assigned_by=self.staff,
        )
        assign_accountability_partners(
            sprint=self.sprint,
            member=alice,
            partner=bob,
            assigned_by=self.staff,
        )

        summary = preview_partner_intro_emails(self.sprint)

        self.assertTrue(summary['send_ready'])
        self.assertEqual(summary['invalid_partner_count'], 1)
        self.assertEqual(summary['invalid_partners'][0]['partner_email'], 'outsider@test.com')
        alice_row = next(
            row for row in summary['eligible']
            if row['member_email'] == 'alice@test.com'
        )
        self.assertEqual(
            [partner['email'] for partner in alice_row['partners']],
            ['bob@test.com'],
        )

    @override_settings(SLACK_TEAM_ID='TTEAM', SITE_BASE_URL='https://example.test')
    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_send_records_snapshot_and_renders_slack_identity_and_link(self, mock_ses):
        mock_ses.return_value = 'ses-1'
        alice, bob = self._ready_pair(
            bob_kwargs={
                'first_name': 'Bob',
                'slack_user_id': 'UBOB',
                'import_metadata': {'slack': {'display_name': 'Bobby Slack'}},
            },
        )

        summary = send_partner_intro_emails(sprint=self.sprint, actor=self.staff)

        self.assertEqual(summary['sent_count'], 2)
        log = SprintPartnerIntroEmailLog.objects.get(sprint=self.sprint, member=alice)
        self.assertEqual(log.status, PARTNER_INTRO_EMAIL_STATUS_SENT)
        self.assertEqual(log.triggered_by, self.staff)
        self.assertEqual(log.email_log.email_type, 'sprint_partner_intro')
        self.assertEqual(log.partner_snapshot[0]['slack_identity'], 'Bobby Slack')
        self.assertEqual(
            log.partner_snapshot[0]['slack_profile_url'],
            'https://app.slack.com/client/TTEAM/UBOB',
        )
        html = mock_ses.call_args_list[0].args[2]
        self.assertIn('Bobby Slack', html)
        self.assertIn('https://app.slack.com/client/TTEAM/UBOB', html)
        self.assertIn('https://example.test/sprints/may-sprint/board', html)
        self.assertNotIn('/studio/', html)
        self.assertEqual(
            EmailLog.objects.filter(user=alice, email_type='sprint_partner_intro').count(),
            1,
        )

    @override_settings(SLACK_TEAM_ID='')
    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_missing_slack_profile_link_is_warning_not_blocker(self, mock_ses):
        mock_ses.return_value = 'ses-1'
        alice, bob = self._ready_pair(bob_kwargs={'slack_user_id': 'UBOB'})

        preview = preview_partner_intro_emails(self.sprint)
        sent = send_partner_intro_emails(sprint=self.sprint, actor=self.staff)

        self.assertTrue(preview['send_ready'])
        self.assertEqual(preview['missing_slack_link_count'], 2)
        alice_row = next(
            row for row in preview['eligible'] if row['member_id'] == alice.pk
        )
        self.assertEqual(alice_row['partners'][0]['slack_identity'], 'UBOB')
        self.assertEqual(alice_row['partners'][0]['slack_profile_url'], '')
        self.assertEqual(sent['sent_count'], 2)
        html = mock_ses.call_args_list[0].args[2]
        self.assertIn('Slack: UBOB', html)
        self.assertIn('Slack profile link unavailable', html)

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_second_send_skips_successful_logs(self, mock_ses):
        mock_ses.return_value = 'ses-1'
        self._ready_pair()

        first = send_partner_intro_emails(sprint=self.sprint, actor=self.staff)
        second = send_partner_intro_emails(sprint=self.sprint, actor=self.staff)

        self.assertEqual(first['sent_count'], 2)
        self.assertEqual(second['sent_count'], 0)
        self.assertEqual(second['skipped_already_sent_count'], 2)
        self.assertEqual(SprintPartnerIntroEmailLog.objects.count(), 2)
        self.assertEqual(
            EmailLog.objects.filter(email_type='sprint_partner_intro').count(),
            2,
        )
        self.assertEqual(mock_ses.call_count, 2)

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_failed_log_is_retryable_and_sent_log_is_skipped(self, mock_ses):
        alice, bob = self._ready_pair()
        SprintPartnerIntroEmailLog.objects.create(
            sprint=self.sprint,
            member=alice,
            status=PARTNER_INTRO_EMAIL_STATUS_FAILED,
            last_error='SES timeout',
        )
        SprintPartnerIntroEmailLog.objects.create(
            sprint=self.sprint,
            member=bob,
            status=PARTNER_INTRO_EMAIL_STATUS_SENT,
        )
        mock_ses.return_value = 'ses-retry'

        summary = send_partner_intro_emails(sprint=self.sprint, actor=self.staff)

        self.assertEqual(summary['sent_count'], 1)
        self.assertEqual(summary['skipped_already_sent_count'], 1)
        self.assertEqual(mock_ses.call_count, 1)
        self.assertEqual(
            SprintPartnerIntroEmailLog.objects.get(member=alice).status,
            PARTNER_INTRO_EMAIL_STATUS_SENT,
        )
        self.assertEqual(
            SprintPartnerIntroEmailLog.objects.get(member=bob).status,
            PARTNER_INTRO_EMAIL_STATUS_SENT,
        )
