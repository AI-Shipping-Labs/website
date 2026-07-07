"""Tests for the one-week onboarding reminder sweep (issue #1133).

Covers the spec's 10 scenarios: the cohort/anchor query, idempotency,
completion / eligibility exclusion, settings resolution (enabled flag +
delay), the team BCC, the transactional classification, and the
``send_onboarding_reminders --dry-run`` command.
"""

import datetime
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings, tag
from django.utils import timezone

from accounts.models import TierOverride, User
from accounts.tasks.remind_onboarding import (
    REMINDER_EMAIL_TYPE,
    remind_onboarding_incomplete,
)
from email_app.models import EmailLog
from email_app.services.email_classification import (
    DEFAULT_WELCOME_FROM_EMAIL,
    EMAIL_KIND_TRANSACTIONAL,
    WELCOME_EMAIL_TYPES,
    classify_email_type,
    get_sender_for_email_type,
)
from payments.models import Tier
from questionnaires.models import Questionnaire, Response


def _tier(slug):
    return Tier.objects.get(slug=slug)


class _SendRecorder:
    """Stand-in for ``EmailService.send`` recording calls and writing a log.

    Because a class-instance with ``__call__`` is not a descriptor, binding
    it as the ``send`` class attribute means ``service.send(user, ...)``
    calls this without the bound ``EmailService`` self — so the first
    positional arg is the recipient user, matching production semantics
    closely enough (writes an ``EmailLog`` so idempotency holds).
    """

    def __init__(self):
        self.calls = []

    def __call__(self, user, template_name, context=None, cc=None, bcc=None):
        self.calls.append(
            {
                "user": user,
                "template": template_name,
                "context": context,
                "bcc": bcc,
            }
        )
        return EmailLog.objects.create(
            user=user,
            email_type=template_name,
            ses_message_id="ses-test",
        )


class OnboardingReminderSweepTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.onboarding_q = Questionnaire.objects.create(
            title="Onboarding", slug="onboarding-reminder-test", purpose="onboarding",
        )

    def _make_member(self, email, tier_slug="main"):
        return User.objects.create_user(
            email=email, password="pw", tier=_tier(tier_slug),
        )

    def _welcome(self, user, days_ago, email_type="cofounder_welcome"):
        """Create a welcome EmailLog dated ``days_ago`` (bypasses auto_now_add)."""
        log = EmailLog.objects.create(
            user=user, email_type=email_type, ses_message_id="ses-welcome",
        )
        sent = timezone.now() - datetime.timedelta(days=days_ago)
        EmailLog.objects.filter(pk=log.pk).update(sent_at=sent)
        log.refresh_from_db()
        return log

    def _submit_onboarding(self, user):
        Response.objects.create(
            respondent=user,
            questionnaire=self.onboarding_q,
            status="submitted",
            submitted_at=timezone.now(),
        )

    def _patched_send(self):
        recorder = _SendRecorder()
        return recorder, patch(
            "email_app.services.email_service.EmailService.send",
            new=recorder,
        )

    # -- Scenario: due member gets a nudge -------------------------------

    def test_due_member_receives_single_reminder_with_link(self):
        member = self._make_member("due@example.com", "main")
        self._welcome(member, days_ago=8, email_type="cofounder_welcome")

        recorder, patcher = self._patched_send()
        with patcher:
            result = remind_onboarding_incomplete()

        self.assertEqual(result["sent"], 1)
        self.assertEqual(len(recorder.calls), 1)
        self.assertEqual(recorder.calls[0]["template"], REMINDER_EMAIL_TYPE)
        self.assertTrue(
            EmailLog.objects.filter(
                user=member, email_type=REMINDER_EMAIL_TYPE,
            ).exists()
        )

    def test_reminder_template_renders_link_and_subject(self):
        from email_app.services.email_service import EmailService

        member = self._make_member("render@example.com", "basic")
        subject, body_html = EmailService()._render_template(
            REMINDER_EMAIL_TYPE, member, {},
        )
        self.assertTrue(subject.strip())
        self.assertIn("/onboarding/", body_html)

    # -- Scenario: already onboarded is left alone -----------------------

    def test_completed_member_not_reminded(self):
        member = self._make_member("done@example.com", "basic")
        self._welcome(member, days_ago=10, email_type="basic_welcome")
        self._submit_onboarding(member)

        recorder, patcher = self._patched_send()
        with patcher:
            result = remind_onboarding_incomplete()

        self.assertEqual(result["sent"], 0)
        self.assertEqual(len(recorder.calls), 0)
        self.assertFalse(
            EmailLog.objects.filter(
                user=member, email_type=REMINDER_EMAIL_TYPE,
            ).exists()
        )

    # -- Scenario: fresh member not reminded prematurely -----------------

    def test_fresh_member_not_yet_due(self):
        member = self._make_member("fresh@example.com", "premium")
        self._welcome(member, days_ago=2, email_type="premium_welcome")

        recorder, patcher = self._patched_send()
        with patcher:
            result = remind_onboarding_incomplete()

        self.assertEqual(result["sent"], 0)
        # Not in the past-cutoff cohort at all: not counted as skipped.
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(len(recorder.calls), 0)

    # -- Scenario: churned member not chased -----------------------------

    def test_churned_member_not_reminded(self):
        member = self._make_member("churned@example.com", "free")
        self._welcome(member, days_ago=9, email_type="cofounder_welcome")

        recorder, patcher = self._patched_send()
        with patcher:
            result = remind_onboarding_incomplete()

        self.assertEqual(result["sent"], 0)
        self.assertEqual(len(recorder.calls), 0)

    def test_expired_override_member_not_reminded(self):
        member = self._make_member("expired@example.com", "free")
        TierOverride.objects.create(
            user=member,
            original_tier=member.tier,
            override_tier=_tier("main"),
            expires_at=timezone.now() - datetime.timedelta(days=1),
            is_active=True,
        )
        self._welcome(member, days_ago=9, email_type="cofounder_welcome")

        recorder, patcher = self._patched_send()
        with patcher:
            result = remind_onboarding_incomplete()

        self.assertEqual(result["sent"], 0)
        self.assertEqual(len(recorder.calls), 0)

    # -- Scenario: never remind the same member twice --------------------

    def test_idempotent_no_double_reminder(self):
        member = self._make_member("once@example.com", "main")
        self._welcome(member, days_ago=8, email_type="cofounder_welcome")
        # Pre-existing reminder log.
        EmailLog.objects.create(
            user=member, email_type=REMINDER_EMAIL_TYPE, ses_message_id="ses-old",
        )

        recorder, patcher = self._patched_send()
        with patcher:
            result = remind_onboarding_incomplete()

        self.assertEqual(result["sent"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(len(recorder.calls), 0)
        self.assertEqual(
            EmailLog.objects.filter(
                user=member, email_type=REMINDER_EMAIL_TYPE,
            ).count(),
            1,
        )

    def test_running_sweep_twice_sends_once(self):
        member = self._make_member("twice@example.com", "main")
        self._welcome(member, days_ago=8, email_type="cofounder_welcome")

        recorder, patcher = self._patched_send()
        with patcher:
            first = remind_onboarding_incomplete()
            second = remind_onboarding_incomplete()

        self.assertEqual(first["sent"], 1)
        self.assertEqual(second["sent"], 0)
        self.assertEqual(
            EmailLog.objects.filter(
                user=member, email_type=REMINDER_EMAIL_TYPE,
            ).count(),
            1,
        )

    # -- Scenario: team gets a copy --------------------------------------

    def test_team_bcc_when_configured(self):
        member = self._make_member("bcc@example.com", "main")
        self._welcome(member, days_ago=8, email_type="cofounder_welcome")

        recorder, patcher = self._patched_send()
        with patcher, override_settings(STAFF_SIGNUP_NOTIFY_EMAIL="team@example.com"):
            remind_onboarding_incomplete()

        self.assertEqual(len(recorder.calls), 1)
        self.assertEqual(recorder.calls[0]["bcc"], "team@example.com")

    def test_no_bcc_when_staff_email_blank(self):
        member = self._make_member("nobcc@example.com", "main")
        self._welcome(member, days_ago=8, email_type="cofounder_welcome")

        recorder, patcher = self._patched_send()
        with patcher, override_settings(STAFF_SIGNUP_NOTIFY_EMAIL=""):
            result = remind_onboarding_incomplete()

        self.assertEqual(result["sent"], 1)
        self.assertEqual(len(recorder.calls), 1)
        self.assertIsNone(recorder.calls[0]["bcc"])

    # -- Scenario: operator disables the sweep ---------------------------

    def test_disabled_flag_makes_sweep_noop(self):
        member = self._make_member("disabled@example.com", "main")
        self._welcome(member, days_ago=8, email_type="cofounder_welcome")

        recorder, patcher = self._patched_send()
        with patcher, override_settings(ONBOARDING_REMINDER_ENABLED="false"):
            result = remind_onboarding_incomplete()

        self.assertEqual(result, {"sent": 0, "skipped": 0})
        self.assertEqual(len(recorder.calls), 0)
        self.assertFalse(
            EmailLog.objects.filter(
                user=member, email_type=REMINDER_EMAIL_TYPE,
            ).exists()
        )

    # -- Scenario: operator shortens the window --------------------------

    def test_shorter_delay_pulls_in_recent_cohort(self):
        member = self._make_member("short@example.com", "main")
        self._welcome(member, days_ago=4, email_type="cofounder_welcome")

        recorder, patcher = self._patched_send()
        # Default 7 days: not due yet.
        with patcher:
            default_result = remind_onboarding_incomplete()
        self.assertEqual(default_result["sent"], 0)

        # Override to 3 days: the 4-day-old member is now due.
        recorder2, patcher2 = self._patched_send()
        with patcher2, override_settings(ONBOARDING_REMINDER_DELAY_DAYS="3"):
            short_result = remind_onboarding_incomplete()
        self.assertEqual(short_result["sent"], 1)
        self.assertEqual(len(recorder2.calls), 1)


@tag("core")
class OnboardingReminderClassificationTest(TestCase):
    """The reminder is transactional and NOT a welcome type."""

    def test_classified_transactional(self):
        self.assertEqual(
            classify_email_type(REMINDER_EMAIL_TYPE), EMAIL_KIND_TRANSACTIONAL,
        )

    def test_not_a_welcome_type(self):
        self.assertNotIn(REMINDER_EMAIL_TYPE, WELCOME_EMAIL_TYPES)

    def test_sender_is_transactional_not_welcome(self):
        sender = get_sender_for_email_type(REMINDER_EMAIL_TYPE)
        self.assertNotEqual(sender, DEFAULT_WELCOME_FROM_EMAIL)


class SendOnboardingRemindersCommandTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.onboarding_q = Questionnaire.objects.create(
            title="Onboarding", slug="onboarding-reminder-test", purpose="onboarding",
        )

    def _due_member(self):
        member = User.objects.create_user(
            email="cmd-due@example.com", password="pw", tier=_tier("main"),
        )
        log = EmailLog.objects.create(
            user=member, email_type="cofounder_welcome", ses_message_id="w",
        )
        EmailLog.objects.filter(pk=log.pk).update(
            sent_at=timezone.now() - datetime.timedelta(days=9),
        )
        return member

    def _fresh_member(self):
        member = User.objects.create_user(
            email="cmd-fresh@example.com", password="pw", tier=_tier("premium"),
        )
        log = EmailLog.objects.create(
            user=member, email_type="premium_welcome", ses_message_id="w",
        )
        EmailLog.objects.filter(pk=log.pk).update(
            sent_at=timezone.now() - datetime.timedelta(days=1),
        )
        return member

    def test_dry_run_lists_due_only_and_sends_nothing(self):
        due = self._due_member()
        self._fresh_member()

        out = StringIO()
        with patch(
            "email_app.services.email_service.EmailService.send",
            new=_SendRecorder(),
        ) as recorder:
            call_command("send_onboarding_reminders", "--dry-run", stdout=out)

        output = out.getvalue()
        self.assertIn(due.email, output)
        self.assertNotIn("cmd-fresh@example.com", output)
        self.assertEqual(len(recorder.calls), 0)
        self.assertFalse(
            EmailLog.objects.filter(email_type=REMINDER_EMAIL_TYPE).exists()
        )

    def test_normal_run_sends_and_reports_summary(self):
        self._due_member()
        self._fresh_member()

        out = StringIO()
        recorder = _SendRecorder()
        with patch(
            "email_app.services.email_service.EmailService.send", new=recorder,
        ):
            call_command("send_onboarding_reminders", stdout=out)

        output = out.getvalue()
        self.assertIn("sent=1", output)
        self.assertEqual(len(recorder.calls), 1)
