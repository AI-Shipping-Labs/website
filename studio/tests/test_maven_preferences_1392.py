"""Focused Studio Maven preference coverage for issue #1392."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from community.models import CommunityAuditLog
from integrations.models import MavenEnrollmentEvent

User = get_user_model()
FAST_PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


def _occurrence(user, *, lifecycle="active"):
    return MavenEnrollmentEvent.objects.create(
        dedupe_key=f"studio-1392-{user.pk}",
        identity_hash=f"studio-1392-{user.pk}",
        user=user,
        email=user.email,
        lifecycle=lifecycle,
    )


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class StudioMavenEmailPreferenceTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email="studio-staff-1392@example.com",
            password="pw",
            is_staff=True,
        )
        self.member = User.objects.create_user(
            email="studio-member-1392@example.com",
            password="pw",
        )
        self.client.login(email=self.staff.email, password="pw")

    def detail_url(self, user=None):
        return reverse("studio_user_detail", args=[(user or self.member).pk])

    def action_url(self, user=None):
        return reverse(
            "studio_user_maven_email_preference",
            args=[(user or self.member).pk],
        )

    def test_unrelated_member_has_no_maven_deliverability_row(self):
        response = self.client.get(self.detail_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Deliverability")
        self.assertNotContains(response, "Maven course emails")
        self.assertNotContains(response, "user-detail-maven-emails-action")

    def test_historical_member_has_existing_deliverability_row_and_one_action(self):
        _occurrence(self.member, lifecycle="removed")

        response = self.client.get(self.detail_url())

        self.assertContains(response, 'data-testid="user-detail-maven-emails-row"')
        self.assertContains(
            response,
            '<span data-testid="user-detail-maven-emails-state">On</span>',
            html=True,
        )
        self.assertContains(
            response, 'data-testid="user-detail-maven-emails-action"', count=1
        )
        self.assertContains(response, 'name="enabled" value="false"')

    def test_staff_action_updates_only_maven_preference_and_audits_transition(self):
        _occurrence(self.member)
        self.member.email_preferences = {
            "newsletter": True,
            "workshop_emails": False,
        }
        self.member.save(update_fields=["email_preferences"])

        response = self.client.post(
            self.action_url(), {"enabled": "false"}, follow=True
        )

        self.assertContains(response, "Maven course emails turned off.")
        self.member.refresh_from_db()
        self.assertEqual(
            self.member.email_preferences,
            {
                "newsletter": True,
                "workshop_emails": False,
                "maven_emails": False,
            },
        )
        audit = CommunityAuditLog.objects.get(
            user=self.member, action="maven_email_preference"
        )
        self.assertIn("source=studio", audit.details)
        self.assertIn(f"actor={self.staff.email}", audit.details)
        self.assertIn(f"member_id={self.member.pk}", audit.details)
        self.assertIn(f"member_email={self.member.email}", audit.details)
        self.assertIn("previous=True", audit.details)
        self.assertIn("new=False", audit.details)

    def test_action_rejects_missing_boolean_target_without_mutation_or_audit(self):
        _occurrence(self.member)
        original = dict(self.member.email_preferences)

        response = self.client.post(self.action_url(), {"enabled": "1"})

        self.assertRedirects(response, self.detail_url())
        self.member.refresh_from_db()
        self.assertEqual(self.member.email_preferences, original)
        self.assertFalse(
            CommunityAuditLog.objects.filter(
                user=self.member, action="maven_email_preference"
            ).exists()
        )

    def test_action_rejects_unrelated_member_without_mutation_or_audit(self):
        response = self.client.post(self.action_url(), {"enabled": "false"})

        self.assertRedirects(response, self.detail_url())
        self.member.refresh_from_db()
        self.assertNotIn("maven_emails", self.member.email_preferences)
        self.assertFalse(
            CommunityAuditLog.objects.filter(
                user=self.member, action="maven_email_preference"
            ).exists()
        )

    def test_non_staff_cannot_mutate_preference_or_write_success_audit(self):
        _occurrence(self.member)
        non_staff = User.objects.create_user(
            email="studio-nonstaff-1392@example.com",
            password="pw",
        )
        self.client.login(email=non_staff.email, password="pw")

        response = self.client.post(self.action_url(), {"enabled": "false"})

        self.assertEqual(response.status_code, 403)
        self.member.refresh_from_db()
        self.assertNotIn("maven_emails", self.member.email_preferences)
        self.assertFalse(
            CommunityAuditLog.objects.filter(
                user=self.member, action="maven_email_preference"
            ).exists()
        )
