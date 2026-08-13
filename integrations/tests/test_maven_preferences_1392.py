"""Focused member-facing Maven preference coverage for issue #1392."""

import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import TierOverride
from accounts.utils.tokens import generate_user_action_token
from content.access import get_user_level
from content.models import Course, Enrollment
from integrations.models import MavenEnrollmentEvent
from integrations.services.maven_preferences import is_maven_relevant
from payments.models import Tier

User = get_user_model()


def _occurrence(user, *, lifecycle="active", suffix="event"):
    return MavenEnrollmentEvent.objects.create(
        dedupe_key=f"1392-{user.pk}-{suffix}",
        identity_hash=f"1392-{user.pk}-{suffix}",
        user=user,
        email=user.email,
        lifecycle=lifecycle,
    )


class MavenRelevanceTest(TestCase):
    def test_unrelated_member_is_not_relevant(self):
        user = User.objects.create_user(email="unrelated-1392@example.com")

        self.assertFalse(is_maven_relevant(user))

    def test_explicit_false_preference_is_relevant(self):
        user = User.objects.create_user(email="opted-out-1392@example.com")
        user.email_preferences = {"maven_emails": False}
        user.save(update_fields=["email_preferences"])

        self.assertTrue(is_maven_relevant(user))

    def test_any_occurrence_lifecycle_is_relevant(self):
        for lifecycle in ("active", "removed", "legacy"):
            with self.subTest(lifecycle=lifecycle):
                user = User.objects.create_user(
                    email=f"{lifecycle}-1392@example.com"
                )
                _occurrence(user, lifecycle=lifecycle, suffix=lifecycle)
                self.assertTrue(is_maven_relevant(user))


class MavenAccountPreferenceTest(TestCase):
    def test_unrelated_member_sees_general_preferences_without_maven_copy(self):
        user = User.objects.create_user(email="plain-account-1392@example.com")
        self.client.force_login(user)

        response = self.client.get("/account/")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["show_maven_email_preference"])
        self.assertNotContains(response, "Maven course emails")
        self.assertNotContains(response, 'data-testid="maven-emails-toggle"')
        self.assertContains(response, "Newsletter")
        self.assertContains(response, "Workshop announcements")
        self.assertContains(response, "Sprint reminders")
        self.assertContains(response, "Book Club summary emails")

    def test_removed_occurrence_shows_default_on_control_while_integration_is_off(self):
        user = User.objects.create_user(email="historical-1392@example.com")
        _occurrence(user, lifecycle="removed")
        self.client.force_login(user)

        response = self.client.get("/account/")

        self.assertTrue(response.context["show_maven_email_preference"])
        self.assertTrue(response.context["maven_emails_enabled"])
        self.assertContains(response, 'data-testid="maven-emails-toggle"')
        self.assertContains(response, 'role="switch" aria-checked="true"')

    def test_explicit_false_keeps_control_visible_and_accessible(self):
        user = User.objects.create_user(email="explicit-false-1392@example.com")
        user.email_preferences = {"maven_emails": False}
        user.save(update_fields=["email_preferences"])
        self.client.force_login(user)

        response = self.client.get("/account/")

        self.assertTrue(response.context["show_maven_email_preference"])
        self.assertFalse(response.context["maven_emails_enabled"])
        self.assertContains(response, 'role="switch" aria-checked="false"')
        self.assertContains(response, 'aria-live="polite"')

    def test_member_toggle_changes_only_maven_consent_and_preserves_access(self):
        free = Tier.objects.get(slug="free")
        main = Tier.objects.get(slug="main")
        user = User.objects.create_user(
            email="access-1392@example.com",
            tier=free,
            email_verified=True,
            unsubscribed=False,
            slack_member=True,
            slack_user_id="U1392ACCESS",
        )
        user.email_preferences = {
            "newsletter": True,
            "workshop_emails": False,
            "sprint_cadence_emails": True,
            "bookclub_emails": False,
        }
        user.save(update_fields=["email_preferences"])
        occurrence = _occurrence(user)
        override = TierOverride.objects.create(
            user=user,
            original_tier=free,
            override_tier=main,
            expires_at=timezone.now() + timedelta(days=365),
            source=f"maven:{occurrence.identity_hash}",
        )
        course = Course.objects.create(
            title="Maven preference invariant course",
            slug="maven-preference-invariant-1392",
            status="published",
        )
        enrollment = Enrollment.objects.create(user=user, course=course)
        before = {
            "tier_id": user.tier_id,
            "level": get_user_level(user),
            "override": (override.override_tier_id, override.expires_at, override.is_active),
            "slack": (user.slack_member, user.slack_user_id),
            "unsubscribed": user.unsubscribed,
            "other_preferences": dict(user.email_preferences),
            "enrollment_id": enrollment.pk,
        }
        self.client.force_login(user)

        response = self.client.post(
            "/account/api/email-preferences",
            data=json.dumps({"maven_emails": False}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        override.refresh_from_db()
        self.assertFalse(user.email_preferences["maven_emails"])
        self.assertEqual(user.tier_id, before["tier_id"])
        self.assertEqual(get_user_level(user), before["level"])
        self.assertEqual(
            (override.override_tier_id, override.expires_at, override.is_active),
            before["override"],
        )
        self.assertEqual((user.slack_member, user.slack_user_id), before["slack"])
        self.assertEqual(user.unsubscribed, before["unsubscribed"])
        for key, value in before["other_preferences"].items():
            self.assertEqual(user.email_preferences[key], value)
        self.assertTrue(Enrollment.objects.filter(pk=before["enrollment_id"]).exists())

    def test_signed_opt_out_preserves_state_and_exposes_account_reenable(self):
        user = User.objects.create_user(
            email="signed-opt-out-1392@example.com",
            unsubscribed=False,
            slack_member=True,
        )
        user.email_preferences = {"newsletter": True, "workshop_emails": False}
        user.save(update_fields=["email_preferences"])
        _occurrence(user)
        before = {
            "tier_id": user.tier_id,
            "level": get_user_level(user),
            "slack_member": user.slack_member,
            "unsubscribed": user.unsubscribed,
            "newsletter": user.email_preferences["newsletter"],
            "workshop_emails": user.email_preferences["workshop_emails"],
        }
        token = generate_user_action_token(user.pk, "maven_email_opt_out")

        response = self.client.get(f"/api/maven-email-opt-out?token={token}")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your course and community access are unchanged")
        self.assertContains(response, "turn them on again from Account")
        user.refresh_from_db()
        self.assertFalse(user.email_preferences["maven_emails"])
        self.assertEqual(user.tier_id, before["tier_id"])
        self.assertEqual(get_user_level(user), before["level"])
        self.assertEqual(user.slack_member, before["slack_member"])
        self.assertEqual(user.unsubscribed, before["unsubscribed"])
        self.assertEqual(user.email_preferences["newsletter"], before["newsletter"])
        self.assertEqual(
            user.email_preferences["workshop_emails"], before["workshop_emails"]
        )

        self.client.force_login(user)
        account = self.client.get("/account/")
        self.assertContains(account, 'role="switch" aria-checked="false"')
        self.assertContains(account, 'data-testid="maven-emails-toggle"')
