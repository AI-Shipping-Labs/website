"""Focused user API Maven preference coverage for issue #1392."""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import EmailAlias, Token
from community.models import CommunityAuditLog
from payments.models import Tier

User = get_user_model()


class UserApiMavenEmailPreferenceTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="api-staff-1392@example.com",
            password="pw",
            is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name="maven-support")
        cls.main = Tier.objects.get(slug="main")

    def auth(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}

    def patch(self, email, value):
        return self.client.patch(
            f"/api/users/{email}",
            data=json.dumps({"maven_emails": value}),
            content_type="application/json",
            **self.auth(),
        )

    def test_get_preserves_email_preferences_payload(self):
        member = User.objects.create_user(
            email="api-get-1392@example.com",
            email_preferences={"newsletter": True, "maven_emails": False},
        )

        response = self.client.get(f"/api/users/{member.email}", **self.auth())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["email_preferences"],
            {"newsletter": True, "maven_emails": False},
        )

    def test_patch_alias_updates_canonical_preference_once_and_audits(self):
        member = User.objects.create_user(
            email="api-canonical-1392@example.com",
            email_preferences={"newsletter": True, "workshop_emails": False},
        )
        EmailAlias.objects.create(user=member, email="api-alias-1392@example.com")

        response = self.patch("api-alias-1392@example.com", False)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["email"], member.email)
        self.assertFalse(response.json()["email_preferences"]["maven_emails"])
        member.refresh_from_db()
        self.assertEqual(
            member.email_preferences,
            {
                "newsletter": True,
                "workshop_emails": False,
                "maven_emails": False,
            },
        )
        audits = CommunityAuditLog.objects.filter(
            user=member, action="maven_email_preference"
        )
        self.assertEqual(audits.count(), 1)
        details = audits.get().details
        self.assertIn("source=api", details)
        self.assertIn("actor_token=maven-support", details)
        self.assertIn(f"member_id={member.pk}", details)
        self.assertIn(f"member_email={member.email}", details)
        self.assertIn("previous=True", details)
        self.assertIn("new=False", details)

    def test_patch_rejects_non_booleans_without_consent_or_access_mutation(self):
        member = User.objects.create_user(
            email="api-invalid-1392@example.com",
            tier=self.main,
            email_verified=True,
            slack_member=True,
            email_preferences={"newsletter": True, "maven_emails": False},
        )
        before = {
            "tier_id": member.tier_id,
            "email_verified": member.email_verified,
            "slack_member": member.slack_member,
            "email_preferences": dict(member.email_preferences),
        }

        for value in ("false", None, 0, 1, []):
            with self.subTest(value=value):
                response = self.patch(member.email, value)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["code"], "validation_error")
                self.assertEqual(response.json()["details"]["field"], "maven_emails")

        member.refresh_from_db()
        self.assertEqual(member.tier_id, before["tier_id"])
        self.assertEqual(member.email_verified, before["email_verified"])
        self.assertEqual(member.slack_member, before["slack_member"])
        self.assertEqual(member.email_preferences, before["email_preferences"])
        self.assertFalse(
            CommunityAuditLog.objects.filter(
                user=member, action="maven_email_preference"
            ).exists()
        )

    def test_patch_boolean_can_reenable_explicit_false_preference(self):
        member = User.objects.create_user(
            email="api-reenable-1392@example.com",
            email_preferences={"newsletter": False, "maven_emails": False},
        )

        response = self.patch(member.email, True)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["email_preferences"]["maven_emails"])
        member.refresh_from_db()
        self.assertEqual(
            member.email_preferences,
            {"newsletter": False, "maven_emails": True},
        )
        audit = CommunityAuditLog.objects.get(
            user=member, action="maven_email_preference"
        )
        self.assertIn("previous=False", audit.details)
        self.assertIn("new=True", audit.details)
