"""Corrective acceptance coverage for Maven enrollment issue #960."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, close_old_connections, transaction
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import ImportBatch, TierOverride
from community.models import CommunityAuditLog
from content.access import get_user_level
from integrations.models import IntegrationSetting, MavenEnrollmentEvent
from integrations.services.maven import _run_step, _welcome_context
from payments.models import Tier

User = get_user_model()
SECRET = "correction-secret"


def enable_maven():
    IntegrationSetting.objects.update_or_create(key="MAVEN_ENROLLMENT_ENABLED", defaults={"value": "true"})
    IntegrationSetting.objects.update_or_create(key="MAVEN_WEBHOOK_SHARED_SECRET", defaults={"value": SECRET})
    from integrations.config import clear_config_cache
    clear_config_cache()


@patch("integrations.services.maven._invite_to_slack", lambda user, actions: actions.append("slack"))
@patch("integrations.services.maven.EmailService")
class MavenCorrectionsTest(TestCase):
    def setUp(self):
        enable_maven()
        self.main = Tier.objects.get(slug="main")
        self.premium = Tier.objects.get(slug="premium")

    def post(self, event, email="member@example.com", course=None, cohort=None, **extra):
        payload = {"event": event, "email": email}
        if course is not None:
            payload["course"] = course
        if cohort is not None:
            payload["cohort"] = cohort
        payload.update(extra)
        return self.client.post(
            "/api/webhooks/maven", data=json.dumps(payload), content_type="application/json",
            HTTP_X_MAVEN_SECRET=SECRET,
        )

    def test_new_user_is_durably_marketing_excluded_existing_choice_preserved(self, email_service):
        existing = User.objects.create_user(email="existing@example.com", unsubscribed=False, email_verified=True)
        self.post("user_cohort.enrolled", email="new@example.com")
        self.post("user_cohort.enrolled", email=existing.email)
        new = User.objects.get(email="new@example.com")
        self.assertTrue(new.unsubscribed)
        self.assertFalse(new.email_preferences["newsletter"])
        self.assertTrue(new.email_preferences["maven_emails"])
        new.email_verified = True
        new.save(update_fields=["email_verified"])
        self.assertTrue(User.objects.get(pk=new.pk).unsubscribed)
        existing.refresh_from_db()
        self.assertFalse(existing.unsubscribed)

    def test_provider_ids_dedupe_label_changes_but_courses_do_not_collide(self, email_service):
        self.post("user_cohort.enrolled", course={"id": "course-a", "name": "Old"}, cohort={"id": "spring", "name": "Spring"})
        self.post("user_cohort.enrolled", course={"id": "course-a", "name": "Renamed"}, cohort={"id": "spring", "name": "2026 Spring"})
        self.post("user_cohort.enrolled", course={"id": "course-b", "name": "Other"}, cohort={"id": "spring", "name": "Spring"})
        self.assertEqual(MavenEnrollmentEvent.objects.filter(lifecycle="active").count(), 2)

    def test_top_level_provider_ids_override_nested_labels(self, email_service):
        self.post(
            "user_cohort.enrolled",
            course={"name": "Old course label"},
            cohort={"name": "Old cohort label"},
            course_id="stable-course-123",
            cohort_id="stable-cohort-456",
        )
        self.post(
            "user_cohort.enrolled",
            course={"name": "Renamed course"},
            cohort={"name": "Renamed cohort"},
            course_id="stable-course-123",
            cohort_id="stable-cohort-456",
        )
        event = MavenEnrollmentEvent.objects.get(lifecycle="active")
        self.assertEqual(event.course_key, "stable-course-123")
        self.assertEqual(event.cohort_key, "stable-cohort-456")
        self.assertEqual(MavenEnrollmentEvent.objects.filter(lifecycle="active").count(), 1)

    def test_removal_closes_without_revoke_and_reenrollment_is_new_occurrence(self, email_service):
        course = {"id": "course-a", "name": "Course"}
        cohort = {"id": "spring", "name": "Spring"}
        self.post("user_cohort.enrolled", course=course, cohort=cohort)
        grant = TierOverride.objects.get(source__startswith="maven:")
        self.post("user_cohort.removed", course=course, cohort=cohort)
        grant.refresh_from_db()
        self.assertTrue(grant.is_active)
        self.post("user_cohort.enrolled", course=course, cohort=cohort)
        self.assertEqual(MavenEnrollmentEvent.objects.filter(lifecycle="removed").count(), 1)
        self.assertEqual(MavenEnrollmentEvent.objects.filter(lifecycle="active").count(), 1)

    def test_maven_entitlement_coexists_with_stronger_temporary_grant(self, email_service):
        user = User.objects.create_user(email="member@example.com")
        higher = TierOverride.objects.create(
            user=user, override_tier=self.premium, expires_at=timezone.now() + timedelta(days=2), source="staff",
        )
        self.post("user_cohort.enrolled")
        self.assertEqual(TierOverride.objects.filter(user=user, is_active=True).count(), 2)
        self.assertEqual(get_user_level(user), self.premium.level)
        higher.expires_at = timezone.now() - timedelta(seconds=1)
        higher.save(update_fields=["expires_at"])
        self.assertEqual(get_user_level(user), self.main.level)

    def test_later_studio_grant_preserves_maven_fallback(self, email_service):
        user = User.objects.create_user(email="member@example.com")
        self.post("user_cohort.enrolled")
        maven_grant = TierOverride.objects.get(user=user, source__startswith="maven:")

        staff = User.objects.create_user(email="staff@example.com", is_staff=True)
        self.client.force_login(staff)
        response = self.client.post(
            reverse("studio_user_tier_override_create", args=[user.pk]),
            {"tier_id": self.premium.pk, "duration": "14 days"},
        )
        self.assertEqual(response.status_code, 302)
        maven_grant.refresh_from_db()
        self.assertTrue(maven_grant.is_active)
        staff_grant = TierOverride.objects.get(user=user, source="staff")
        self.assertEqual(get_user_level(user), self.premium.level)
        staff_grant.expires_at = timezone.now() - timedelta(seconds=1)
        staff_grant.save(update_fields=["expires_at"])
        self.assertEqual(get_user_level(user), self.main.level)

    def test_contact_import_grant_preserves_maven_fallback(self, email_service):
        from studio.services.contacts_import import _apply_tier_override

        user = User.objects.create_user(email="contact-import@example.com")
        self.post("user_cohort.enrolled", email=user.email)
        maven_grant = TierOverride.objects.get(
            user=user, source__startswith="maven:"
        )
        staff = User.objects.create_user(
            email="contact-import-staff@example.com", is_staff=True
        )
        _apply_tier_override(user, self.premium, staff)
        maven_grant.refresh_from_db()
        self.assertTrue(maven_grant.is_active)
        self.assertTrue(
            TierOverride.objects.filter(user=user, source="staff").exists()
        )

    def test_shared_import_grant_preserves_maven_fallback(self, email_service):
        from accounts.services.import_users import _apply_tier_override

        user = User.objects.create_user(email="shared-import@example.com")
        self.post("user_cohort.enrolled", email=user.email)
        maven_grant = TierOverride.objects.get(
            user=user, source__startswith="maven:"
        )
        staff = User.objects.create_user(
            email="shared-import-staff@example.com", is_staff=True
        )
        batch = ImportBatch.objects.create(source="manual", actor=staff)
        _apply_tier_override(
            user,
            self.premium,
            timezone.now() + timedelta(days=14),
            staff,
            batch=batch,
            row_number=1,
            email=user.email,
            source="manual",
            dry_run=False,
        )
        maven_grant.refresh_from_db()
        self.assertTrue(maven_grant.is_active)
        self.assertTrue(
            TierOverride.objects.filter(
                user=user, source="import:manual"
            ).exists()
        )

    def test_payload_is_minimized_and_dedupe_key_contains_no_email(self, email_service):
        self.post("user_cohort.enrolled", course={"id": "c1", "name": "Course"}, cohort={"id": "h1", "name": "Cohort"})
        event = MavenEnrollmentEvent.objects.get()
        self.assertNotIn("email", event.payload)
        self.assertNotIn("member@example.com", event.dedupe_key)
        self.assertEqual(len(event.dedupe_key), 64)

    def test_one_active_occurrence_database_constraint(self, email_service):
        self.post("user_cohort.enrolled")
        event = MavenEnrollmentEvent.objects.get()
        with self.assertRaises(IntegrityError), transaction.atomic():
            MavenEnrollmentEvent.objects.create(
                dedupe_key="different", identity_hash=event.identity_hash,
                lifecycle=MavenEnrollmentEvent.LIFECYCLE_ACTIVE,
            )


class MavenRetryPreferenceAndOpsTest(TestCase):
    def setUp(self):
        enable_maven()

    def post(self, payload):
        return self.client.post(
            "/api/webhooks/maven", data=json.dumps(payload), content_type="application/json",
            HTTP_X_MAVEN_SECRET=SECRET,
        )

    def test_failed_welcome_retries_without_repeating_successful_slack(self):
        with patch("integrations.services.maven._invite_to_slack") as slack, patch("integrations.services.maven.EmailService") as service:
            service.return_value.send.side_effect = [RuntimeError("provider detail must not persist"), None]
            self.post({"event": "user_cohort.enrolled", "email": "retry@example.com"})
            event = MavenEnrollmentEvent.objects.get()
            self.assertEqual(event.slack_status, event.STEP_SUCCEEDED)
            self.assertEqual(event.welcome_status, event.STEP_FAILED)
            self.assertEqual(event.welcome_error, "RuntimeError")
            self.post({"event": "user_cohort.enrolled", "email": "retry@example.com"})
            event.refresh_from_db()
            self.assertEqual(event.welcome_status, event.STEP_SUCCEEDED)
            self.assertEqual(slack.call_count, 1)
            self.assertEqual(service.return_value.send.call_count, 2)

    def test_scheduled_retry_is_bounded_and_records_step_times(self):
        from jobs.tasks.cleanup import retry_maven_enrollment_steps

        with patch("integrations.services.maven._invite_to_slack") as slack, patch(
            "integrations.services.maven.EmailService"
        ) as service:
            service.return_value.send.side_effect = [
                RuntimeError("first"),
                RuntimeError("second"),
                None,
            ]
            response = self.post(
                {"event": "user_cohort.enrolled", "email": "auto-retry@example.com"}
            )
            self.assertEqual(response.status_code, 200)
            event = MavenEnrollmentEvent.objects.get()
            self.assertEqual(event.welcome_status, event.STEP_FAILED)
            self.assertIsNotNone(event.welcome_attempted_at)
            self.assertIsNotNone(event.welcome_completed_at)

            retry_maven_enrollment_steps()
            event.refresh_from_db()
            self.assertEqual(event.welcome_attempts, 2)
            self.assertEqual(event.welcome_status, event.STEP_FAILED)
            retry_maven_enrollment_steps()
            retry_maven_enrollment_steps()
            event.refresh_from_db()
            self.assertEqual(event.welcome_attempts, 3)
            self.assertEqual(event.welcome_status, event.STEP_SUCCEEDED)
            self.assertEqual(slack.call_count, 1)
            self.assertEqual(service.return_value.send.call_count, 3)

    def test_scheduled_retry_stops_after_three_failed_attempts(self):
        from jobs.tasks.cleanup import retry_maven_enrollment_steps

        with patch("integrations.services.maven._invite_to_slack"), patch(
            "integrations.services.maven.EmailService"
        ) as service:
            service.return_value.send.side_effect = RuntimeError("down")
            self.post({"event": "user_cohort.enrolled", "email": "bounded@example.com"})
            retry_maven_enrollment_steps()
            retry_maven_enrollment_steps()
            retry_maven_enrollment_steps()
            event = MavenEnrollmentEvent.objects.get()
            self.assertEqual(event.welcome_attempts, 3)
            self.assertEqual(event.welcome_status, event.STEP_FAILED)
            self.assertEqual(service.return_value.send.call_count, 3)

    def test_scheduled_retry_recovers_slack_and_removal_independently(self):
        from jobs.tasks.cleanup import retry_maven_enrollment_steps

        with patch(
            "integrations.services.maven._invite_to_slack",
            side_effect=[RuntimeError("slack down"), None],
        ) as slack, patch("integrations.services.maven.EmailService") as service:
            self.post(
                {"event": "user_cohort.enrolled", "email": "slack-retry@example.com"}
            )
            event = MavenEnrollmentEvent.objects.get(email="slack-retry@example.com")
            self.assertEqual(event.slack_status, event.STEP_FAILED)
            self.assertEqual(event.welcome_status, event.STEP_SUCCEEDED)
            retry_maven_enrollment_steps()
            event.refresh_from_db()
            self.assertEqual(event.slack_status, event.STEP_SUCCEEDED)
            self.assertEqual(slack.call_count, 2)
            self.assertEqual(service.return_value.send.call_count, 1)

        with patch(
            "community.services.staff_notifications.notify_maven_cohort_removal",
            side_effect=[RuntimeError("notify down"), None],
        ) as notify:
            self.post(
                {"event": "user_cohort.removed", "email": "unknown-retry@example.com"}
            )
            removal = MavenEnrollmentEvent.objects.get(email="unknown-retry@example.com")
            self.assertEqual(removal.removal_status, removal.STEP_FAILED)
            retry_maven_enrollment_steps()
            removal.refresh_from_db()
            self.assertEqual(removal.removal_status, removal.STEP_SUCCEEDED)
            self.assertEqual(notify.call_count, 2)

    @patch("integrations.services.maven._invite_to_slack", lambda user, actions: None)
    @patch("integrations.services.maven.EmailService")
    def test_scoped_opt_out_preserves_access_and_account_can_reenable(self, email_service):
        self.post({"event": "user_cohort.enrolled", "email": "opt@example.com"})
        user = User.objects.get(email="opt@example.com")
        level = get_user_level(user)
        token = parse_qs(urlparse(_welcome_context(user, "Course")["opt_out_url"]).query)["token"][0]
        response = self.client.get(f"/api/maven-email-opt-out?token={token}")
        self.assertContains(response, "access are unchanged")
        user.refresh_from_db()
        self.assertFalse(user.email_preferences["maven_emails"])
        self.assertEqual(get_user_level(user), level)
        self.client.force_login(user)
        response = self.client.post(
            "/account/api/email-preferences", data=json.dumps({"maven_emails": True}), content_type="application/json",
        )
        self.assertEqual(response.json()["maven_emails"], True)

    def test_retention_command_redacts_email_and_legacy_payload(self):
        event = MavenEnrollmentEvent.objects.create(
            dedupe_key="retention", identity_hash="retention", email="pii@example.com",
            payload={"email": "pii@example.com", "secret": "bad"}, lifecycle="removed",
        )
        MavenEnrollmentEvent.objects.filter(pk=event.pk).update(created_at=timezone.now() - timedelta(days=31))
        call_command("redact_maven_enrollment_pii", stdout=StringIO())
        event.refresh_from_db()
        self.assertEqual(event.email, "")
        self.assertEqual(event.payload, {})
        self.assertIsNotNone(event.payload_redacted_at)

    def test_studio_is_staff_only_and_retry_is_audited(self):
        member = User.objects.create_user(email="member@example.com")
        event = MavenEnrollmentEvent.objects.create(
            dedupe_key="ops", identity_hash="ops", user=member, email=member.email,
            lifecycle="active", override_status="skipped", slack_status="skipped",
            welcome_status="failed", welcome_attempts=1,
        )
        self.client.force_login(member)
        self.assertEqual(self.client.get(f"/studio/maven-events/{event.pk}/").status_code, 403)
        staff = User.objects.create_user(email="staff@example.com", is_staff=True)
        self.client.force_login(staff)
        detail = self.client.get(f"/studio/maven-events/{event.pk}/")
        self.assertContains(detail, f'/studio/users/{member.pk}/')
        self.assertContains(detail, "Canonical member")
        self.assertContains(detail, "Last attempted")
        with patch("integrations.services.maven._send_welcome"):
            response = self.client.post(f"/studio/maven-events/{event.pk}/retry/welcome")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(CommunityAuditLog.objects.filter(user=member, action="maven_step_retry").exists())

    def test_manual_retry_does_not_duplicate_a_fresh_running_step(self):
        member = User.objects.create_user(email="running@example.com")
        event = MavenEnrollmentEvent.objects.create(
            dedupe_key="running-ops",
            identity_hash="running-ops",
            user=member,
            email=member.email,
            lifecycle="active",
            override_status="succeeded",
            slack_status="succeeded",
            welcome_status="running",
            welcome_attempts=1,
            welcome_attempted_at=timezone.now(),
        )
        actions = []
        with patch("integrations.services.maven._send_welcome") as send:
            _run_step(event.pk, "welcome", actions, force=True)
        send.assert_not_called()
        event.refresh_from_db()
        self.assertEqual(event.welcome_attempts, 1)
        self.assertIn("already running", actions[0])

    def test_unknown_user_retry_is_audited_against_actor(self):
        event = MavenEnrollmentEvent.objects.create(
            dedupe_key="unknown-ops",
            identity_hash="unknown-ops",
            lifecycle="removed",
            override_status="skipped",
            slack_status="skipped",
            welcome_status="skipped",
            removal_status="failed",
            removal_attempts=1,
        )
        staff = User.objects.create_user(email="staff-unknown@example.com", is_staff=True)
        self.client.force_login(staff)
        with patch("community.services.staff_notifications.notify_maven_cohort_removal"):
            response = self.client.post(
                f"/studio/maven-events/{event.pk}/retry/removal"
            )
        self.assertEqual(response.status_code, 302)
        audit = CommunityAuditLog.objects.get(action="maven_step_retry")
        self.assertEqual(audit.user, staff)
        self.assertIn("member_user_id=unknown", audit.details)

    def test_rejected_webhook_emits_operational_warning(self):
        with self.assertLogs("integrations.views.maven_webhook", level="WARNING") as logs:
            response = self.client.post(
                "/api/webhooks/maven",
                data=json.dumps({"event": "user_cohort.enrolled", "email": "x@example.com"}),
                content_type="application/json",
                HTTP_X_MAVEN_SECRET="wrong",
            )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(any("authentication failed" in line for line in logs.output))


class MavenConcurrentDeliveryTest(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        enable_maven()
        Tier.objects.get_or_create(
            slug="main",
            defaults={"name": "Main", "level": 20},
        )

    def test_simultaneous_identical_deliveries_run_side_effects_once(self):
        barrier = threading.Barrier(2)
        side_effect_lock = threading.Lock()
        calls = {"slack": 0, "welcome": 0}
        payload = json.dumps(
            {
                "event": "user_cohort.enrolled",
                "email": "concurrent@example.com",
                "course_id": "course-concurrent",
                "cohort_id": "cohort-concurrent",
                "course": {"name": "Course"},
                "cohort": {"name": "Cohort"},
            }
        )

        def count(name):
            def inner(*args, **kwargs):
                with side_effect_lock:
                    calls[name] += 1
            return inner

        def deliver():
            close_old_connections()
            client = Client()
            barrier.wait()
            response = client.post(
                "/api/webhooks/maven",
                data=payload,
                content_type="application/json",
                HTTP_X_MAVEN_SECRET=SECRET,
            )
            result = (response.status_code, response.json())
            close_old_connections()
            return result

        with patch("integrations.services.maven._invite_to_slack", side_effect=count("slack")), patch(
            "integrations.services.maven._send_welcome",
            side_effect=count("welcome"),
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: deliver(), range(2)))

        self.assertEqual(
            [status for status, _ in results],
            [200, 200],
            results,
        )
        self.assertCountEqual(
            [body.get("status") for _, body in results],
            ["onboarded", "already_processed"],
        )
        self.assertEqual(User.objects.filter(email="concurrent@example.com").count(), 1)
        self.assertEqual(MavenEnrollmentEvent.objects.filter(lifecycle="active").count(), 1)
        self.assertEqual(TierOverride.objects.filter(source__startswith="maven:").count(), 1)
        self.assertEqual(calls, {"slack": 1, "welcome": 1})
