"""Studio Maven operational recovery coverage for issue #1566."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from community.models import CommunityAuditLog
from integrations.models import MavenEnrollmentEvent
from integrations.services.maven import MAX_STEP_ATTEMPTS
from integrations.services.maven_attention import needs_attention_occurrences
from payments.models import Tier

User = get_user_model()


class MavenStudioListAndDashboardTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="maven-list-staff@example.com", password="pw", is_staff=True
        )

    def setUp(self):
        self.client.force_login(self.staff)

    def _event(self, key, **fields):
        return MavenEnrollmentEvent.objects.create(
            dedupe_key=key, identity_hash=key, course=key, **fields
        )

    def test_failed_attention_and_unknown_filters_select_the_right_occurrences(self):
        now = timezone.now()
        success = self._event(
            "success",
            override_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
            notification_status=MavenEnrollmentEvent.STEP_SKIPPED,
            slack_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
            welcome_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
            removal_status=MavenEnrollmentEvent.STEP_SKIPPED,
        )
        fresh = self._event(
            "fresh-failure",
            welcome_status=MavenEnrollmentEvent.STEP_FAILED,
            welcome_attempts=2,
            welcome_attempted_at=now - timedelta(minutes=5),
            welcome_completed_at=now - timedelta(minutes=5),
        )
        stalled = self._event(
            "stalled-failure",
            welcome_status=MavenEnrollmentEvent.STEP_FAILED,
            welcome_attempts=2,
            welcome_attempted_at=now - timedelta(minutes=16),
            welcome_completed_at=now - timedelta(minutes=16),
        )

        failed = self.client.get(reverse("studio_maven_event_list"), {"status": "failed"})
        self.assertEqual(
            {event.pk for event in failed.context["events"]},
            {fresh.pk, stalled.pk},
        )
        attention = self.client.get(
            reverse("studio_maven_event_list"), {"status": "needs_attention"}
        )
        self.assertEqual(
            [event.pk for event in attention.context["events"]], [stalled.pk]
        )
        unknown = self.client.get(
            reverse("studio_maven_event_list"), {"status": "unexpected"}
        )
        self.assertEqual(unknown.context["status_filter"], "all")
        self.assertEqual(
            {event.pk for event in unknown.context["events"]},
            {success.pk, fresh.pk, stalled.pk},
        )

    def test_list_shows_all_steps_attention_state_and_canonical_view_action(self):
        event = self._event(
            "all-columns",
            override_status=MavenEnrollmentEvent.STEP_FAILED,
            override_attempts=MAX_STEP_ATTEMPTS,
            notification_status=MavenEnrollmentEvent.STEP_RUNNING,
            slack_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
            welcome_status=MavenEnrollmentEvent.STEP_SKIPPED,
            removal_status=MavenEnrollmentEvent.STEP_PENDING,
        )
        response = self.client.get(reverse("studio_maven_event_list"))
        for heading in (
            "State",
            "Entitlement",
            "Notification",
            "Slack",
            "Welcome",
            "Removal",
            "Actions",
        ):
            self.assertContains(response, heading)
        self.assertContains(response, "Needs attention")
        self.assertContains(response, "Failed")
        self.assertContains(response, "Running")
        self.assertContains(response, "Succeeded")
        self.assertContains(response, "Skipped")
        self.assertContains(response, "Pending")
        self.assertContains(
            response, reverse("studio_maven_event_detail", args=[event.pk])
        )
        self.assertContains(response, ">View<", html=False)

    def test_filtered_empty_keeps_table_and_fresh_empty_uses_fresh_state(self):
        fresh_empty = self.client.get(reverse("studio_maven_event_list"))
        self.assertContains(fresh_empty, "No Maven occurrences yet")
        self.assertNotContains(fresh_empty, "<table", html=False)

        self._event("only-success", welcome_status=MavenEnrollmentEvent.STEP_SUCCEEDED)
        filtered = self.client.get(
            reverse("studio_maven_event_list"), {"status": "failed"}
        )
        self.assertContains(filtered, "<table", html=False)
        self.assertContains(filtered, "No Maven occurrences match your filters.")
        self.assertContains(filtered, "Clear filters")

    def test_full_ledger_and_attention_results_are_paginated_with_filter_preserved(self):
        MavenEnrollmentEvent.objects.bulk_create(
            [
                MavenEnrollmentEvent(
                    dedupe_key=f"success-{index}",
                    identity_hash=f"success-{index}",
                    course=f"Success {index}",
                    override_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
                )
                for index in range(201)
            ]
        )
        old_failure = self._event(
            "older-than-cap",
            override_status=MavenEnrollmentEvent.STEP_FAILED,
            override_attempts=MAX_STEP_ATTEMPTS,
        )
        attention = self.client.get(
            reverse("studio_maven_event_list"), {"status": "needs_attention"}
        )
        self.assertContains(attention, f"#{old_failure.pk}")

        for index in range(26):
            self._event(
                f"attention-{index}",
                welcome_status=MavenEnrollmentEvent.STEP_FAILED,
                welcome_attempts=MAX_STEP_ATTEMPTS,
            )
        first_page = self.client.get(
            reverse("studio_maven_event_list"), {"status": "needs_attention"}
        )
        self.assertEqual(len(first_page.context["events"]), 25)
        self.assertIn("status=needs_attention&amp;page=2", first_page.content.decode())
        second_page = self.client.get(
            reverse("studio_maven_event_list"),
            {"status": "needs_attention", "page": 2},
        )
        self.assertEqual(len(second_page.context["events"]), 2)

    def test_dashboard_counts_occurrences_and_hides_zero_state(self):
        clean = self.client.get(reverse("studio_dashboard"))
        self.assertNotContains(clean, "Maven enrollments need attention")
        event = self._event(
            "dashboard-multiple",
            override_status=MavenEnrollmentEvent.STEP_FAILED,
            override_attempts=MAX_STEP_ATTEMPTS,
            welcome_status=MavenEnrollmentEvent.STEP_FAILED,
            welcome_attempts=MAX_STEP_ATTEMPTS,
        )
        response = self.client.get(reverse("studio_dashboard"))
        item = next(
            item
            for item in response.context["attention_items"]
            if item["label"] == "Maven enrollments need attention"
        )
        self.assertEqual(item["count"], 1)
        self.assertEqual(item["tone"], "critical")
        self.assertEqual(
            item["url"],
            f"{reverse('studio_maven_event_list')}?status=needs_attention",
        )
        self.assertTrue(needs_attention_occurrences().filter(pk=event.pk).exists())


class MavenStudioRetryTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email="maven-retry-staff@example.com", password="pw", is_staff=True
        )
        self.member = User.objects.create_user(email="maven-member@example.com")
        self.client.force_login(self.staff)

    def _event(self, **fields):
        defaults = {
            "dedupe_key": "manual-retry",
            "identity_hash": "manual-retry",
            "user": self.member,
            "override_status": MavenEnrollmentEvent.STEP_SUCCEEDED,
            "notification_status": MavenEnrollmentEvent.STEP_SKIPPED,
            "slack_status": MavenEnrollmentEvent.STEP_SKIPPED,
            "welcome_status": MavenEnrollmentEvent.STEP_SKIPPED,
            "removal_status": MavenEnrollmentEvent.STEP_SKIPPED,
        }
        defaults.update(fields)
        return MavenEnrollmentEvent.objects.create(**defaults)

    def _retry_url(self, event, step):
        return reverse("studio_maven_event_retry", args=[event.pk, step])

    def test_detail_explains_exhausted_and_stalled_steps(self):
        event = self._event(
            override_status=MavenEnrollmentEvent.STEP_FAILED,
            override_attempts=MAX_STEP_ATTEMPTS,
            welcome_status=MavenEnrollmentEvent.STEP_FAILED,
            welcome_attempts=2,
            welcome_attempted_at=timezone.now() - timedelta(minutes=16),
            welcome_completed_at=timezone.now() - timedelta(minutes=16),
        )
        response = self.client.get(
            reverse("studio_maven_event_detail", args=[event.pk])
        )
        self.assertContains(response, "Automatic retries are exhausted")
        self.assertContains(response, "stalled for at least 15 minutes")
        self.assertContains(response, "Fix the underlying cause")

    def test_force_retry_beyond_ceiling_reports_persisted_success_and_audits(self):
        event = self._event(
            welcome_status=MavenEnrollmentEvent.STEP_FAILED,
            welcome_attempts=MAX_STEP_ATTEMPTS,
        )
        with patch("integrations.services.maven._send_welcome") as send:
            response = self.client.post(
                self._retry_url(event, "welcome"), follow=True
            )
        event.refresh_from_db()
        self.assertEqual(event.welcome_status, MavenEnrollmentEvent.STEP_SUCCEEDED)
        self.assertEqual(event.welcome_attempts, MAX_STEP_ATTEMPTS + 1)
        self.assertEqual(send.call_count, 1)
        self.assertContains(response, "Maven welcome step recovered.")
        audit = CommunityAuditLog.objects.get(action="maven_step_retry")
        self.assertEqual(audit.user, self.member)
        self.assertIn(f"occurrence={event.pk}", audit.details)
        self.assertIn(f"actor_staff_id={self.staff.pk}", audit.details)

    def test_failed_and_running_retries_show_truthful_feedback(self):
        failed = self._event(
            welcome_status=MavenEnrollmentEvent.STEP_FAILED,
            welcome_attempts=MAX_STEP_ATTEMPTS,
        )
        with patch(
            "integrations.services.maven._send_welcome",
            side_effect=RuntimeError("still down"),
        ):
            failed_response = self.client.post(
                self._retry_url(failed, "welcome"), follow=True
            )
        self.assertContains(failed_response, "Maven welcome step failed again")

        CommunityAuditLog.objects.all().delete()
        failed.delete()
        running = self._event(
            welcome_status=MavenEnrollmentEvent.STEP_RUNNING,
            welcome_attempts=2,
            welcome_attempted_at=timezone.now(),
        )
        with patch("integrations.services.maven._send_welcome") as send:
            running_response = self.client.post(
                self._retry_url(running, "welcome"), follow=True
            )
        running.refresh_from_db()
        send.assert_not_called()
        self.assertEqual(running.welcome_attempts, 2)
        self.assertContains(running_response, "already running and was not repeated")

    def test_successful_override_resumes_downstream_once_in_order(self):
        Tier.objects.get_or_create(
            slug="main", defaults={"name": "Main", "level": 20}
        )
        event = self._event(
            override_status=MavenEnrollmentEvent.STEP_FAILED,
            override_attempts=MAX_STEP_ATTEMPTS,
            notification_status=MavenEnrollmentEvent.STEP_PENDING,
            slack_status=MavenEnrollmentEvent.STEP_PENDING,
            welcome_status=MavenEnrollmentEvent.STEP_PENDING,
        )
        calls = []

        def grant(*args, **kwargs):
            calls.append("override")
            return "Granted"

        def notify(*args, **kwargs):
            calls.append("notification")
            return True

        def invite(*args, **kwargs):
            calls.append("slack")
            return MavenEnrollmentEvent.STEP_SUCCEEDED, ""

        def welcome(*args, **kwargs):
            calls.append("welcome")

        with patch(
            "integrations.services.maven._grant_or_refresh_override",
            side_effect=grant,
        ), patch(
            "integrations.services.maven._enrollment_notification_entitlement",
            return_value=(Tier.objects.get(slug="main"), timezone.now() + timedelta(days=1)),
        ), patch(
            "community.services.staff_notifications.notify_maven_enrollment",
            side_effect=notify,
        ), patch(
            "integrations.services.maven._invite_to_slack", side_effect=invite
        ), patch(
            "integrations.services.maven._send_welcome", side_effect=welcome
        ):
            response = self.client.post(
                self._retry_url(event, "override"), follow=True
            )
        event.refresh_from_db()
        self.assertEqual(calls, ["override", "notification", "slack", "welcome"])
        self.assertEqual(event.override_attempts, MAX_STEP_ATTEMPTS + 1)
        self.assertEqual(event.notification_attempts, 1)
        self.assertEqual(event.slack_attempts, 1)
        self.assertEqual(event.welcome_attempts, 1)
        self.assertFalse(needs_attention_occurrences().filter(pk=event.pk).exists())
        self.assertContains(response, "Maven override step recovered.")

    def test_retry_is_staff_post_csrf_and_known_step_only(self):
        event = self._event(
            welcome_status=MavenEnrollmentEvent.STEP_FAILED,
            welcome_attempts=MAX_STEP_ATTEMPTS,
        )
        url = self._retry_url(event, "welcome")
        self.assertEqual(self.client.get(url).status_code, 405)

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.login(email=self.staff.email, password="pw")
        self.assertEqual(csrf_client.post(url).status_code, 403)

        unknown = reverse("studio_maven_event_retry", args=[event.pk, "bogus"])
        self.assertEqual(self.client.post(unknown).status_code, 302)
        event.refresh_from_db()
        self.assertEqual(event.welcome_attempts, MAX_STEP_ATTEMPTS)
        self.assertFalse(CommunityAuditLog.objects.exists())

        non_staff = Client()
        non_staff.force_login(self.member)
        detail = reverse("studio_maven_event_detail", args=[event.pk])
        self.assertEqual(non_staff.get(detail).status_code, 403)
        with patch("integrations.services.maven._send_welcome") as send:
            self.assertEqual(non_staff.post(url).status_code, 403)
        send.assert_not_called()
        event.refresh_from_db()
        self.assertEqual(event.welcome_attempts, MAX_STEP_ATTEMPTS)
        self.assertFalse(CommunityAuditLog.objects.exists())
