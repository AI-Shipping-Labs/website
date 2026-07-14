"""Tests for the Maven cohort auto-onboarding webhook + handler (issue #960)."""

import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import EmailAlias, TierOverride
from community.models import CommunityAuditLog
from integrations.models import IntegrationSetting, MavenEnrollmentEvent
from payments.models import Tier

User = get_user_model()

WEBHOOK_URL = "/api/webhooks/maven"
SECRET = "super-secret-token-123"


def _set(key, value):
    IntegrationSetting.objects.update_or_create(key=key, defaults={"value": value})


def _enable():
    _set("MAVEN_ENROLLMENT_ENABLED", "true")
    _set("MAVEN_WEBHOOK_SHARED_SECRET", SECRET)


def _clear_config_cache():
    from integrations.config import clear_config_cache

    clear_config_cache()


class MavenWebhookAuthTest(TestCase):
    def setUp(self):
        _enable()
        _clear_config_cache()
        self.addCleanup(_clear_config_cache)

    def _post(self, body, **extra):
        return self.client.post(
            WEBHOOK_URL,
            data=json.dumps(body),
            content_type="application/json",
            **extra,
        )

    def test_disabled_returns_disabled_status_and_no_work(self):
        _set("MAVEN_ENROLLMENT_ENABLED", "false")
        _clear_config_cache()
        with patch("integrations.services.maven.handle_maven_event") as handler:
            response = self._post(
                {"event": "user_cohort.enrolled", "email": "x@test.com"},
                QUERY_STRING=f"secret={SECRET}",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "disabled"})
        handler.assert_not_called()
        self.assertEqual(User.objects.filter(email="x@test.com").count(), 0)

    def test_missing_secret_returns_403(self):
        response = self._post({"event": "user_cohort.enrolled", "email": "x@test.com"})
        self.assertEqual(response.status_code, 403)

    def test_wrong_secret_returns_403(self):
        response = self._post(
            {"event": "user_cohort.enrolled", "email": "x@test.com"},
            QUERY_STRING="secret=nope",
        )
        self.assertEqual(response.status_code, 403)

    def test_secret_accepted_in_query(self):
        response = self._post(
            {"event": "user_cohort.unenrolled", "email": "x@test.com"},
            QUERY_STRING=f"secret={SECRET}",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ignored"})

    def test_secret_accepted_in_header(self):
        response = self._post(
            {"event": "user_cohort.unenrolled", "email": "x@test.com"},
            HTTP_X_MAVEN_SECRET=SECRET,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ignored"})

    def test_unset_secret_returns_403_even_when_enabled(self):
        _set("MAVEN_WEBHOOK_SHARED_SECRET", "")
        _clear_config_cache()
        response = self._post(
            {"event": "user_cohort.enrolled", "email": "x@test.com"},
            QUERY_STRING="secret=anything",
        )
        self.assertEqual(response.status_code, 403)

    def test_malformed_json_returns_400(self):
        response = self.client.post(
            WEBHOOK_URL,
            data="not-json",
            content_type="application/json",
            QUERY_STRING=f"secret={SECRET}",
        )
        self.assertEqual(response.status_code, 400)

    def test_missing_email_returns_400(self):
        response = self._post(
            {"event": "user_cohort.enrolled"},
            QUERY_STRING=f"secret={SECRET}",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json().get("error"), "missing_email")

    def test_get_not_allowed(self):
        response = self.client.get(WEBHOOK_URL, QUERY_STRING=f"secret={SECRET}")
        self.assertEqual(response.status_code, 405)


@patch("integrations.services.maven._invite_to_slack", lambda user, actions: actions.append("slack"))
class MavenEnrolledTest(TestCase):
    def setUp(self):
        _enable()
        _clear_config_cache()
        self.addCleanup(_clear_config_cache)
        self.main = Tier.objects.get(slug="main")

    def _post(self, body):
        return self.client.post(
            WEBHOOK_URL,
            data=json.dumps(body),
            content_type="application/json",
            QUERY_STRING=f"secret={SECRET}",
        )

    @patch("integrations.services.maven.EmailService")
    def test_new_email_creates_account_grants_override_invites_and_emails(self, email_service):
        response = self._post(
            {
                "event": "user_cohort.enrolled",
                "email": "New.Enrollee@test.com",
                "cohort": "Spring 2026",
                "course": "LLM Zoomcamp",
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "onboarded"})

        user = User.objects.get(email="new.enrollee@test.com")
        self.assertEqual(user.signup_source, "imported")
        self.assertFalse(user.email_verified)

        override = TierOverride.objects.get(user=user, is_active=True)
        self.assertEqual(override.override_tier, self.main)
        self.assertGreater(override.expires_at, timezone.now() + timedelta(days=3000))

        email_service.return_value.send.assert_called_once()
        sent_args = email_service.return_value.send.call_args
        self.assertEqual(sent_args.args[1], "maven_welcome")

    @patch("integrations.services.maven.EmailService")
    def test_non_enrolled_event_ignored(self, email_service):
        response = self._post(
            {"event": "payment.success", "email": "paid@test.com"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ignored"})
        self.assertFalse(User.objects.filter(email="paid@test.com").exists())
        email_service.return_value.send.assert_not_called()

    @patch("integrations.services.maven.EmailService")
    def test_existing_account_resolved_no_duplicate(self, email_service):
        existing = User.objects.create_user(email="member@test.com", password="x")
        self._post({"event": "user_cohort.enrolled", "email": "MEMBER@test.com"})
        self.assertEqual(User.objects.filter(email__iexact="member@test.com").count(), 1)
        self.assertTrue(
            TierOverride.objects.filter(user=existing, is_active=True).exists()
        )

    @patch("integrations.services.maven.EmailService")
    def test_resolves_via_alias_no_duplicate(self, email_service):
        canonical = User.objects.create_user(email="canon@test.com", password="x")
        EmailAlias.objects.create(user=canonical, email="alias@test.com")
        self._post({"event": "user_cohort.enrolled", "email": "alias@test.com"})
        self.assertFalse(User.objects.filter(email="alias@test.com").exists())
        self.assertTrue(
            TierOverride.objects.filter(user=canonical, is_active=True).exists()
        )

    @patch("integrations.services.maven.EmailService")
    def test_existing_override_extended_not_stacked(self, email_service):
        user = User.objects.create_user(email="ext@test.com", password="x")
        TierOverride.objects.create(
            user=user,
            original_tier=user.tier,
            override_tier=self.main,
            expires_at=timezone.now() + timedelta(days=30),
            is_active=True,
        )
        self._post({"event": "user_cohort.enrolled", "email": "ext@test.com"})

        active = TierOverride.objects.filter(user=user, is_active=True)
        self.assertEqual(active.count(), 1)
        self.assertGreater(
            active.first().expires_at, timezone.now() + timedelta(days=3000)
        )

    @patch("integrations.services.maven.EmailService")
    def test_longer_existing_override_not_shortened(self, email_service):
        user = User.objects.create_user(email="long@test.com", password="x")
        far = timezone.now() + timedelta(days=9000)
        TierOverride.objects.create(
            user=user,
            original_tier=user.tier,
            override_tier=self.main,
            expires_at=far,
            is_active=True,
        )
        self._post({"event": "user_cohort.enrolled", "email": "long@test.com"})
        active = TierOverride.objects.get(user=user, is_active=True)
        self.assertEqual(active.expires_at, far)

    @patch("integrations.services.maven.EmailService")
    def test_duplicate_delivery_already_processed(self, email_service):
        body = {"event": "user_cohort.enrolled", "email": "dup@test.com", "cohort": "C1"}
        first = self._post(body)
        self.assertEqual(first.json(), {"status": "onboarded"})
        second = self._post(body)
        self.assertEqual(second.json(), {"status": "already_processed"})

        self.assertEqual(
            TierOverride.objects.filter(
                user__email="dup@test.com", is_active=True
            ).count(),
            1,
        )
        # Welcome email sent exactly once across both deliveries.
        self.assertEqual(email_service.return_value.send.call_count, 1)
        self.assertEqual(
            MavenEnrollmentEvent.objects.filter(
                email="dup@test.com", event_type="user_cohort.enrolled"
            ).count(),
            1,
        )

    @patch("integrations.services.maven.EmailService")
    def test_override_grant_audited(self, email_service):
        self._post({"event": "user_cohort.enrolled", "email": "audit@test.com"})
        user = User.objects.get(email="audit@test.com")
        self.assertTrue(
            CommunityAuditLog.objects.filter(
                user=user, action="maven_enrollment_override"
            ).exists()
        )

    @patch("integrations.services.maven.EmailService")
    def test_already_member_no_email_no_dup_but_override_refreshed(self, email_service):
        user = User.objects.create_user(
            email="active@test.com", password="x", slack_member=True,
        )
        # Active main access via an expiring override.
        TierOverride.objects.create(
            user=user,
            original_tier=user.tier,
            override_tier=self.main,
            expires_at=timezone.now() + timedelta(days=10),
            is_active=True,
        )
        response = self._post(
            {"event": "user_cohort.enrolled", "email": "active@test.com"}
        )
        self.assertEqual(response.json(), {"status": "already_member"})
        email_service.return_value.send.assert_not_called()
        # Override extended (refreshed) but still single + active.
        active = TierOverride.objects.filter(user=user, is_active=True)
        self.assertEqual(active.count(), 1)
        self.assertGreater(
            active.first().expires_at, timezone.now() + timedelta(days=3000)
        )

    @patch("integrations.services.maven.EmailService")
    def test_lapsed_override_slack_member_is_not_already_member(self, email_service):
        user = User.objects.create_user(
            email="lapsed@test.com", password="x", slack_member=True,
        )
        TierOverride.objects.create(
            user=user,
            original_tier=user.tier,
            override_tier=self.main,
            expires_at=timezone.now() - timedelta(days=1),
            is_active=True,
        )

        response = self._post(
            {"event": "user_cohort.enrolled", "email": "lapsed@test.com"}
        )

        self.assertEqual(response.json(), {"status": "onboarded"})
        email_service.return_value.send.assert_called_once()
        active = TierOverride.objects.filter(user=user, is_active=True)
        self.assertEqual(active.count(), 1)
        self.assertGreater(
            active.first().expires_at, timezone.now() + timedelta(days=3000)
        )

    @patch("integrations.services.maven.EmailService")
    def test_inactive_or_basic_override_slack_member_is_not_already_member(
        self, email_service,
    ):
        basic = Tier.objects.get(slug="basic")
        inactive = User.objects.create_user(
            email="inactive@test.com", password="x", slack_member=True,
        )
        TierOverride.objects.create(
            user=inactive,
            original_tier=inactive.tier,
            override_tier=self.main,
            expires_at=timezone.now() + timedelta(days=30),
            is_active=False,
        )
        basic_only = User.objects.create_user(
            email="basic-only@test.com", password="x", slack_member=True,
        )
        TierOverride.objects.create(
            user=basic_only,
            original_tier=basic_only.tier,
            override_tier=basic,
            expires_at=timezone.now() + timedelta(days=30),
            is_active=True,
        )

        inactive_response = self._post(
            {"event": "user_cohort.enrolled", "email": "inactive@test.com"}
        )
        basic_response = self._post(
            {"event": "user_cohort.enrolled", "email": "basic-only@test.com"}
        )

        self.assertEqual(inactive_response.json(), {"status": "onboarded"})
        self.assertEqual(basic_response.json(), {"status": "onboarded"})
        self.assertEqual(email_service.return_value.send.call_count, 2)

    @patch("integrations.services.maven.EmailService")
    def test_transient_failure_returns_500_and_persists_retryable_step(self, email_service):
        with patch(
            "integrations.services.maven._grant_or_refresh_override",
            side_effect=RuntimeError("boom"),
        ):
            response = self._post(
                {"event": "user_cohort.enrolled", "email": "fail@test.com"}
            )
        self.assertEqual(response.status_code, 500)
        event = MavenEnrollmentEvent.objects.get(email="fail@test.com")
        self.assertEqual(event.override_status, MavenEnrollmentEvent.STEP_FAILED)
        self.assertEqual(event.override_attempts, 1)
        # No half-committed override.
        self.assertFalse(
            TierOverride.objects.filter(user__email="fail@test.com").exists()
        )

    @patch("integrations.services.maven.EmailService")
    def test_new_account_not_in_marketing_audience(self, email_service):
        from email_app.models import EmailCampaign

        self._post({"event": "user_cohort.enrolled", "email": "audience@test.com"})
        user = User.objects.get(email="audience@test.com")
        campaign = EmailCampaign.objects.create(
            subject="Promo", body="b", target_min_level=0,
        )
        audience = campaign.get_eligible_recipients()
        self.assertNotIn(user, audience)


class MavenRemovedTest(TestCase):
    def setUp(self):
        _enable()
        _clear_config_cache()
        self.addCleanup(_clear_config_cache)
        self.main = Tier.objects.get(slug="main")

    def _post(self, body):
        return self.client.post(
            WEBHOOK_URL,
            data=json.dumps(body),
            content_type="application/json",
            QUERY_STRING=f"secret={SECRET}",
        )

    @patch("community.services.staff_notifications.notify_maven_cohort_removal")
    def test_removal_notifies_staff_and_makes_no_access_change(self, notify):
        user = User.objects.create_user(
            email="removed@test.com", password="x", slack_member=True,
        )
        override = TierOverride.objects.create(
            user=user,
            original_tier=user.tier,
            override_tier=self.main,
            expires_at=timezone.now() + timedelta(days=100),
            is_active=True,
        )
        response = self._post(
            {
                "event": "user_cohort.removed",
                "email": "removed@test.com",
                "cohort": "Spring 2026",
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "removal_notified"})
        notify.assert_called_once()

        override.refresh_from_db()
        self.assertTrue(override.is_active)
        user.refresh_from_db()
        self.assertTrue(user.slack_member)

    @patch("community.services.staff_notifications.notify_maven_cohort_removal")
    def test_removal_unknown_email_graceful(self, notify):
        response = self._post(
            {"event": "user_cohort.removed", "email": "ghost@test.com", "cohort": "C"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "removal_notified"})
        # Notifier was called with user=None (graceful), not a 500.
        notify.assert_called_once()
        self.assertIsNone(notify.call_args.args[0])

    @patch("community.services.staff_notifications.notify_maven_cohort_removal")
    def test_removal_duplicate_sends_one_notification(self, notify):
        User.objects.create_user(email="dupremove@test.com", password="x")
        body = {
            "event": "user_cohort.removed",
            "email": "dupremove@test.com",
            "cohort": "C",
        }
        self._post(body)
        second = self._post(body)
        self.assertEqual(second.json(), {"status": "already_processed"})
        self.assertEqual(notify.call_count, 1)


class MavenEmailClassificationTest(TestCase):
    def test_maven_welcome_transactional_and_welcome(self):
        from email_app.services.email_classification import (
            TRANSACTIONAL_EMAIL_TYPES,
            WELCOME_EMAIL_TYPES,
            classify_email_type,
        )

        self.assertEqual(classify_email_type("maven_welcome"), "transactional")
        self.assertIn("maven_welcome", TRANSACTIONAL_EMAIL_TYPES)
        self.assertIn("maven_welcome", WELCOME_EMAIL_TYPES)

    def test_maven_welcome_sends_from_welcome_sender(self):
        from email_app.services.email_classification import (
            DEFAULT_WELCOME_FROM_EMAIL,
            get_sender_for_email_type,
        )

        self.assertEqual(
            get_sender_for_email_type("maven_welcome"), DEFAULT_WELCOME_FROM_EMAIL
        )

    def test_removal_notification_classified_transactional(self):
        from email_app.services.email_classification import classify_email_type

        self.assertEqual(
            classify_email_type("maven_cohort_removal_notification"), "transactional"
        )


class MavenSettingsRegistryTest(TestCase):
    def test_maven_group_registered_with_keys(self):
        from integrations.settings_registry import get_group_by_name

        group = get_group_by_name("maven")
        self.assertIsNotNone(group)
        self.assertEqual(group["label"], "Maven")
        keys = {k["key"] for k in group["keys"]}
        self.assertEqual(
            keys,
            {
                "MAVEN_ENROLLMENT_ENABLED",
                "MAVEN_WEBHOOK_SHARED_SECRET",
                "MAVEN_OVERRIDE_TIER_SLUG",
                "MAVEN_OVERRIDE_DURATION_DAYS",
            },
        )
        for key in group["keys"]:
            self.assertTrue(key.get("description"))
            self.assertTrue(key.get("docs_url"))
