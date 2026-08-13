"""Focused enrollment staff-notification coverage for issue #1399."""

import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import EmailAlias, TierOverride
from integrations.config import clear_config_cache
from integrations.maven_config import maven_override_duration_days
from integrations.models import IntegrationSetting, MavenEnrollmentEvent
from jobs.tasks.cleanup import retry_maven_enrollment_steps
from payments.models import Tier

User = get_user_model()
SECRET = "notification-test-secret"


def configure(**values):
    defaults = {
        "MAVEN_ENROLLMENT_ENABLED": "true",
        "MAVEN_WEBHOOK_SHARED_SECRET": SECRET,
    }
    defaults.update(values)
    for key, value in defaults.items():
        IntegrationSetting.objects.update_or_create(key=key, defaults={"value": value})
    clear_config_cache()


@patch("integrations.services.maven._invite_to_slack", lambda user, actions: actions.append("slack"))
@patch("integrations.services.maven._send_welcome", lambda user, course, actions: actions.append("welcome"))
class MavenEnrollmentNotificationTest(TestCase):
    def setUp(self):
        configure(STAFF_SIGNUP_NOTIFY_EMAIL="staff@example.com")
        self.addCleanup(clear_config_cache)
        self.main = Tier.objects.get(slug="main")

    def post(self, event="user_cohort.enrolled", email="member@example.com", **extra):
        payload = {
            "event": event,
            "email": email,
            "course": "AI Engineering",
            "cohort": "Autumn 2026",
            **extra,
        }
        return self.client.post(
            "/api/webhooks/maven",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_MAVEN_SECRET=SECRET,
        )

    def test_default_and_explicit_duration_are_occurrence_anchored(self):
        from integrations.settings_registry import get_group_by_name

        duration_setting = next(
            item
            for item in get_group_by_name("maven")["keys"]
            if item["key"] == "MAVEN_OVERRIDE_DURATION_DAYS"
        )
        self.assertEqual(duration_setting["default"], "1825")
        with patch(
            "community.services.staff_notifications.notify_maven_enrollment",
            return_value=True,
        ):
            self.post(email="default@example.com")
        occurrence = MavenEnrollmentEvent.objects.get(email="default@example.com")
        grant = TierOverride.objects.get(user=occurrence.user, source=f"maven:{occurrence.identity_hash}")
        self.assertEqual(maven_override_duration_days(), 1825)
        self.assertEqual(grant.expires_at - occurrence.created_at, timedelta(days=1825))

        configure(MAVEN_OVERRIDE_DURATION_DAYS="41", STAFF_SIGNUP_NOTIFY_EMAIL="staff@example.com")
        with patch(
            "community.services.staff_notifications.notify_maven_enrollment",
            return_value=True,
        ):
            self.post(email="explicit@example.com")
        occurrence = MavenEnrollmentEvent.objects.get(email="explicit@example.com")
        grant = TierOverride.objects.get(user=occurrence.user, source=f"maven:{occurrence.identity_hash}")
        self.assertEqual(maven_override_duration_days(), 41)
        self.assertEqual(grant.expires_at - occurrence.created_at, timedelta(days=41))

    def test_notice_for_new_account_has_safe_actionable_content(self):
        with patch(
            "community.services.staff_notifications._send_staff_maven_enrollment_notification"
        ) as send:
            response = self.post(email="new@example.com")
        self.assertEqual(response.json(), {"status": "onboarded"})
        event = MavenEnrollmentEvent.objects.get()
        self.assertEqual(event.notification_status, event.STEP_SUCCEEDED)
        send.assert_called_once()
        recipient, context = send.call_args.args
        self.assertEqual(recipient, "staff@example.com")
        self.assertEqual(context["enrolled_user_email"], "new@example.com")
        self.assertEqual(context["enrolled_user_id"], str(event.user_id))
        self.assertEqual(context["account_state"], "newly created")
        self.assertEqual(context["course"], "AI Engineering")
        self.assertEqual(context["cohort"], "Autumn 2026")
        self.assertEqual(context["tier_slug"], "main")
        self.assertIn(f"/studio/users/{event.user_id}/", context["studio_user_url"])
        self.assertIn(f"/studio/maven-events/{event.pk}/", context["studio_occurrence_url"])
        rendered = json.dumps(context)
        for forbidden in (SECRET, "password_reset", "opt_out", "payload"):
            self.assertNotIn(forbidden, rendered)

        from community.services.staff_notifications import (
            _build_maven_enrollment_slack_text,
        )
        from email_app.services import EmailService

        subject, body = EmailService()._render_template(
            "maven_enrollment_notification",
            event.user,
            context,
        )
        slack_body = _build_maven_enrollment_slack_text(context)
        for value in (
            "new@example.com",
            str(event.user_id),
            "newly created",
            "AI Engineering",
            "Autumn 2026",
            "main",
            context["entitlement_expiry"],
            context["studio_user_url"],
            context["studio_occurrence_url"],
        ):
            self.assertIn(value, f"{subject}\n{body}")
            self.assertIn(value, slack_body)
        self.assertNotIn(SECRET, f"{subject}\n{body}\n{slack_body}")

    def test_existing_primary_alias_and_active_member_each_get_one_notice(self):
        primary = User.objects.create_user(email="primary@example.com")
        alias_user = User.objects.create_user(email="canonical@example.com")
        EmailAlias.objects.create(user=alias_user, email="alias@example.com")
        active = User.objects.create_user(
            email="active@example.com",
            tier=self.main,
            slack_member=True,
        )
        with patch(
            "community.services.staff_notifications._send_staff_maven_enrollment_notification"
        ) as send:
            self.post(email=primary.email, cohort="Primary")
            self.post(email="alias@example.com", cohort="Alias")
            self.post(email=active.email, cohort="Active")
        self.assertEqual(send.call_count, 3)
        contexts = [call.args[1] for call in send.call_args_list]
        self.assertEqual({ctx["account_state"] for ctx in contexts}, {"already existed"})
        self.assertEqual(
            {event.user_id for event in MavenEnrollmentEvent.objects.all()},
            {primary.pk, alias_user.pk, active.pk},
        )
        active_event = MavenEnrollmentEvent.objects.get(user=active)
        self.assertEqual(active_event.slack_status, active_event.STEP_SKIPPED)
        self.assertEqual(active_event.welcome_status, active_event.STEP_SKIPPED)

    def test_duplicate_and_reenrollment_notify_once_per_active_occurrence(self):
        with patch(
            "community.services.staff_notifications._send_staff_maven_enrollment_notification"
        ) as send, patch(
            "community.services.staff_notifications.notify_maven_cohort_removal",
            return_value=None,
        ):
            first = self.post()
            duplicate = self.post()
            removed = self.post(event="user_cohort.removed")
            reenrolled = self.post()
            second_duplicate = self.post()
        self.assertEqual(first.json()["status"], "onboarded")
        self.assertEqual(duplicate.json()["status"], "already_processed")
        self.assertEqual(removed.json()["status"], "removal_notified")
        self.assertEqual(reenrolled.json()["status"], "onboarded")
        self.assertEqual(second_duplicate.json()["status"], "already_processed")
        self.assertEqual(send.call_count, 2)
        self.assertEqual(MavenEnrollmentEvent.objects.filter(lifecycle="removed").count(), 1)
        self.assertEqual(MavenEnrollmentEvent.objects.filter(lifecycle="active").count(), 1)

    def test_all_delivery_failure_retries_only_notification(self):
        with patch(
            "community.services.staff_notifications._send_staff_maven_enrollment_notification",
            side_effect=[RuntimeError("mail down"), None],
        ) as send, patch("integrations.services.maven._invite_to_slack") as invite, patch(
            "integrations.services.maven._send_welcome"
        ) as welcome:
            self.post(email="retry@example.com")
            event = MavenEnrollmentEvent.objects.get()
            original_expiry = TierOverride.objects.get(user=event.user, source=f"maven:{event.identity_hash}").expires_at
            self.assertEqual(event.notification_status, event.STEP_FAILED)
            self.assertEqual(event.notification_error, "MavenEnrollmentNotificationDeliveryError")
            self.assertEqual(event.notification_attempts, 1)
            retry_maven_enrollment_steps()
            event.refresh_from_db()
            grant = TierOverride.objects.get(user=event.user, source=f"maven:{event.identity_hash}")
        self.assertEqual(event.notification_status, event.STEP_SUCCEEDED)
        self.assertEqual(event.notification_attempts, 2)
        self.assertIsNotNone(event.notification_attempted_at)
        self.assertIsNotNone(event.notification_completed_at)
        self.assertEqual(grant.expires_at, original_expiry)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(invite.call_count, 1)
        self.assertEqual(welcome.call_count, 1)

    def test_one_success_completes_step_and_missing_destinations_skip(self):
        configure(
            STAFF_SIGNUP_NOTIFY_EMAIL="staff@example.com",
            STAFF_SIGNUP_NOTIFY_CHANNEL_ID="C_STAFF",
            SLACK_ENABLED="true",
            SLACK_BOT_TOKEN="test-token",
        )
        with patch(
            "community.services.staff_notifications._send_staff_maven_enrollment_notification",
            side_effect=RuntimeError("mail down"),
        ) as send, patch(
            "community.services.staff_notifications._post_slack_maven_enrollment_notification",
            return_value=True,
        ) as slack:
            self.post(email="mirror@example.com")
            self.post(email="mirror@example.com")
        event = MavenEnrollmentEvent.objects.get()
        self.assertEqual(event.notification_status, event.STEP_SUCCEEDED)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(slack.call_count, 1)

        with patch(
            "community.services.staff_notifications._send_staff_maven_enrollment_notification"
        ) as successful_email, patch(
            "community.services.staff_notifications._post_slack_maven_enrollment_notification",
            return_value=False,
        ) as failed_mirror:
            self.post(email="email-wins@example.com")
            self.post(email="email-wins@example.com")
        email_wins = MavenEnrollmentEvent.objects.get(email="email-wins@example.com")
        self.assertEqual(email_wins.notification_status, email_wins.STEP_SUCCEEDED)
        self.assertEqual(successful_email.call_count, 1)
        self.assertEqual(failed_mirror.call_count, 1)

        configure(
            STAFF_SIGNUP_NOTIFY_EMAIL="",
            STAFF_SIGNUP_NOTIFY_CHANNEL_ID="C_DISABLED",
            SLACK_ENABLED="false",
            SLACK_BOT_TOKEN="",
        )
        with self.assertLogs(
            "community.services.staff_notifications",
            level="INFO",
        ) as logs:
            self.post(email="skip@example.com")
        skipped = MavenEnrollmentEvent.objects.get(email="skip@example.com")
        self.assertEqual(skipped.notification_status, skipped.STEP_SKIPPED)
        self.assertEqual(skipped.notification_attempts, 1)
        self.assertTrue(any("no usable staff destination" in line for line in logs.output))

    def test_studio_retry_recovers_only_failed_notification_step(self):
        with patch(
            "community.services.staff_notifications._send_staff_maven_enrollment_notification",
            side_effect=RuntimeError("mail down"),
        ):
            self.post(email="operator@example.com")
        event = MavenEnrollmentEvent.objects.get()
        self.assertEqual(event.notification_status, event.STEP_FAILED)
        staff = User.objects.create_user(email="staff-operator@example.com", is_staff=True)
        self.client.force_login(staff)
        detail = self.client.get(reverse("studio_maven_event_detail", args=[event.pk]))
        self.assertContains(detail, "/retry/notification")
        self.assertContains(detail, "notification")
        with patch(
            "community.services.staff_notifications._send_staff_maven_enrollment_notification"
        ) as send, patch("integrations.services.maven._invite_to_slack") as invite, patch(
            "integrations.services.maven._send_welcome"
        ) as welcome:
            response = self.client.post(
                reverse(
                    "studio_maven_event_retry",
                    args=[event.pk, "notification"],
                )
            )
        self.assertEqual(response.status_code, 302)
        event.refresh_from_db()
        self.assertEqual(event.notification_status, event.STEP_SUCCEEDED)
        send.assert_called_once()
        invite.assert_not_called()
        welcome.assert_not_called()

    def test_notice_reports_later_retained_entitlement_expiry(self):
        user = User.objects.create_user(email="longer@example.com")
        retained_expiry = timezone.now() + timedelta(days=3000)
        TierOverride.objects.create(
            user=user,
            override_tier=self.main,
            expires_at=retained_expiry,
            source="staff",
        )
        with patch(
            "community.services.staff_notifications._send_staff_maven_enrollment_notification"
        ) as send:
            self.post(email=user.email)
        context = send.call_args.args[1]
        self.assertEqual(context["entitlement_expiry"], retained_expiry.isoformat())
        self.assertEqual(
            TierOverride.objects.get(user=user, source="staff").expires_at,
            retained_expiry,
        )

    def test_disabled_ignored_and_removed_only_do_not_attempt_notice(self):
        with patch(
            "community.services.staff_notifications.notify_maven_enrollment"
        ) as notify, patch(
            "community.services.staff_notifications.notify_maven_cohort_removal"
        ):
            configure(
                MAVEN_ENROLLMENT_ENABLED="false",
                STAFF_SIGNUP_NOTIFY_EMAIL="staff@example.com",
            )
            self.post(email="disabled@example.com")
            configure(STAFF_SIGNUP_NOTIFY_EMAIL="staff@example.com")
            self.post(event="payment.success", email="ignored@example.com")
            self.post(event="user_cohort.removed", email="removed-only@example.com")
        notify.assert_not_called()
        removal = MavenEnrollmentEvent.objects.get(email="removed-only@example.com")
        self.assertEqual(removal.notification_status, removal.STEP_SKIPPED)
