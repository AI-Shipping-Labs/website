"""Terminal retry and shared attention-state coverage for issue #1566."""

import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting, MavenEnrollmentEvent
from integrations.services.maven import MAX_STEP_ATTEMPTS, STEP_NAMES
from integrations.services.maven_attention import (
    failed_occurrences,
    needs_attention_occurrences,
    occurrence_attention_reasons,
)
from payments.models import Tier

User = get_user_model()
SECRET = "maven-terminal-secret"
WEBHOOK_URL = "/api/webhooks/maven"


def _enable_maven():
    IntegrationSetting.objects.update_or_create(
        key="MAVEN_ENROLLMENT_ENABLED", defaults={"value": "true"}
    )
    IntegrationSetting.objects.update_or_create(
        key="MAVEN_WEBHOOK_SHARED_SECRET", defaults={"value": SECRET}
    )
    clear_config_cache()


class MavenAttentionQueryTest(TestCase):
    def _event(self, key, **fields):
        return MavenEnrollmentEvent.objects.create(
            dedupe_key=key,
            identity_hash=key,
            **fields,
        )

    def test_each_step_can_make_one_occurrence_need_attention(self):
        for index, name in enumerate(STEP_NAMES):
            with self.subTest(step=name):
                event = self._event(
                    f"step-{index}",
                    **{
                        f"{name}_status": MavenEnrollmentEvent.STEP_FAILED,
                        f"{name}_attempts": MAX_STEP_ATTEMPTS,
                    },
                )
                self.assertTrue(
                    needs_attention_occurrences().filter(pk=event.pk).exists()
                )
                self.assertEqual(
                    occurrence_attention_reasons(event), {name: "exhausted"}
                )

    def test_exact_stalled_boundary_and_fresh_failure(self):
        now = timezone.now()
        boundary = now - timedelta(minutes=15)
        stale = self._event(
            "boundary",
            welcome_status=MavenEnrollmentEvent.STEP_FAILED,
            welcome_attempts=2,
            welcome_attempted_at=boundary - timedelta(seconds=1),
            welcome_completed_at=boundary,
        )
        fresh = self._event(
            "fresh",
            welcome_status=MavenEnrollmentEvent.STEP_FAILED,
            welcome_attempts=2,
            welcome_attempted_at=now - timedelta(minutes=5),
            welcome_completed_at=now - timedelta(minutes=5),
        )
        matches = needs_attention_occurrences(now=now)
        self.assertTrue(matches.filter(pk=stale.pk).exists())
        self.assertFalse(matches.filter(pk=fresh.pk).exists())
        self.assertTrue(failed_occurrences().filter(pk=stale.pk).exists())
        self.assertTrue(failed_occurrences().filter(pk=fresh.pk).exists())

    def test_stale_running_qualifies_but_completed_states_never_do(self):
        now = timezone.now()
        running = self._event(
            "running",
            slack_status=MavenEnrollmentEvent.STEP_RUNNING,
            slack_attempts=1,
            slack_attempted_at=now - timedelta(minutes=15),
        )
        succeeded = self._event(
            "succeeded",
            slack_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
            slack_attempts=MAX_STEP_ATTEMPTS,
            slack_attempted_at=now - timedelta(hours=1),
            slack_completed_at=now - timedelta(hours=1),
        )
        skipped = self._event(
            "skipped",
            removal_status=MavenEnrollmentEvent.STEP_SKIPPED,
            removal_attempts=MAX_STEP_ATTEMPTS,
            removal_attempted_at=now - timedelta(hours=1),
            removal_completed_at=now - timedelta(hours=1),
        )
        matches = needs_attention_occurrences(now=now)
        self.assertTrue(matches.filter(pk=running.pk).exists())
        self.assertFalse(matches.filter(pk__in=[succeeded.pk, skipped.pk]).exists())

    def test_multiple_qualifying_steps_count_the_occurrence_once(self):
        self._event(
            "multiple",
            override_status=MavenEnrollmentEvent.STEP_FAILED,
            override_attempts=MAX_STEP_ATTEMPTS,
            welcome_status=MavenEnrollmentEvent.STEP_FAILED,
            welcome_attempts=MAX_STEP_ATTEMPTS,
        )
        self.assertEqual(needs_attention_occurrences().count(), 1)


class MavenTerminalWebhookTest(TestCase):
    def setUp(self):
        _enable_maven()
        self.addCleanup(clear_config_cache)

    def _post(self, event="user_cohort.enrolled", email="terminal@example.com"):
        return self.client.post(
            WEBHOOK_URL,
            data=json.dumps(
                {
                    "event": event,
                    "email": email,
                    "name": "Sensitive Name",
                    "course": "Sensitive Course",
                    "cohort": "Sensitive Cohort",
                    "payload_secret": "provider-payload-value",
                }
            ),
            content_type="application/json",
            HTTP_X_MAVEN_SECRET=SECRET,
        )

    def test_override_transitions_from_500_to_terminal_200_and_stays_bounded(self):
        Tier.objects.get_or_create(
            slug="main", defaults={"name": "Main", "level": 20}
        )
        with patch(
            "integrations.services.maven._grant_or_refresh_override",
            side_effect=RuntimeError("pii@example.com provider-payload-value"),
        ) as provider:
            with self.assertLogs(level="WARNING") as captured:
                self.assertEqual(self._post().status_code, 500)
                self.assertEqual(self._post().status_code, 500)
                third = self._post()
                fourth = self._post()

        self.assertEqual(
            third.json(), {"status": "manual_intervention_required"}
        )
        self.assertEqual(
            fourth.json(), {"status": "manual_intervention_required"}
        )
        occurrence = MavenEnrollmentEvent.objects.get()
        self.assertEqual(occurrence.override_attempts, MAX_STEP_ATTEMPTS)
        self.assertEqual(provider.call_count, MAX_STEP_ATTEMPTS)
        output = "\n".join(captured.output)
        acknowledgement = next(
            line for line in captured.output if "Acknowledged exhausted" in line
        )
        self.assertIn(f"occurrence={occurrence.pk}", acknowledgement)
        self.assertIn("steps=override", acknowledgement)
        for private_value in (
            "terminal@example.com",
            "Sensitive Name",
            "Sensitive Course",
            "Sensitive Cohort",
            "provider-payload-value",
            SECRET,
            "@",
        ):
            self.assertNotIn(private_value, output)
        self.assertNotIn(occurrence.override_error, acknowledgement)

    @patch(
        "integrations.services.maven._invite_to_slack",
        return_value=(MavenEnrollmentEvent.STEP_SUCCEEDED, ""),
    )
    def test_exhausted_enrollment_delivery_is_acknowledged_without_repetition(
        self, _invite
    ):
        Tier.objects.get_or_create(slug="main", defaults={"name": "Main", "level": 20})
        with patch(
            "integrations.services.maven._send_welcome",
            side_effect=RuntimeError("mail down"),
        ) as send:
            first = self._post(email="welcome-terminal@example.com")
            second = self._post(email="welcome-terminal@example.com")
            third = self._post(email="welcome-terminal@example.com")
            fourth = self._post(email="welcome-terminal@example.com")
        self.assertEqual(first.json(), {"status": "onboarded"})
        self.assertEqual(second.json(), {"status": "onboarded"})
        self.assertEqual(third.json(), {"status": "manual_intervention_required"})
        self.assertEqual(fourth.json(), {"status": "manual_intervention_required"})
        event = MavenEnrollmentEvent.objects.get()
        self.assertEqual(event.welcome_attempts, MAX_STEP_ATTEMPTS)
        self.assertEqual(send.call_count, MAX_STEP_ATTEMPTS)

    def test_exhausted_removal_is_acknowledged_without_repetition(self):
        with patch(
            "community.services.staff_notifications.notify_maven_cohort_removal",
            side_effect=RuntimeError("notification down"),
        ) as notify:
            self._post("user_cohort.removed", "removed-terminal@example.com")
            self._post("user_cohort.removed", "removed-terminal@example.com")
            third = self._post("user_cohort.removed", "removed-terminal@example.com")
            fourth = self._post("user_cohort.removed", "removed-terminal@example.com")
        self.assertEqual(third.json(), {"status": "manual_intervention_required"})
        self.assertEqual(fourth.json(), {"status": "manual_intervention_required"})
        event = MavenEnrollmentEvent.objects.get()
        self.assertEqual(event.removal_attempts, MAX_STEP_ATTEMPTS)
        self.assertEqual(notify.call_count, MAX_STEP_ATTEMPTS)
