"""Studio operator messages for Slack channel reconciliation (#1572)."""

from unittest.mock import MagicMock, patch

from django.test import Client, TestCase

from accounts.models import User
from payments.models import Tier


class SlackMembershipCheckStudioTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-studio-1572@test.com", password="pw", is_staff=True,
        )
        cls.main = Tier.objects.get(level=20)

    def setUp(self):
        self.client.login(email=self.staff.email, password="pw")

    def service(self, results=None, *, channels=("C_ONE", "C_TWO")):
        service = MagicMock()
        service.channel_ids = list(channels)
        service.check_workspace_membership.return_value = ("member", "U1572")
        service.lookup_user_profile_by_email.return_value = None
        service.add_to_channels.return_value = results or [
            {"channel": "C_ONE", "ok": True},
            {"channel": "C_TWO", "ok": True},
        ]
        return service

    @patch("community.tasks.slack_membership.get_community_service")
    def test_complete_member_message(self, get_service):
        member = User.objects.create_user(email="complete-1572@test.com", tier=self.main)
        get_service.return_value = self.service()
        response = self.client.post(
            f"/studio/users/{member.pk}/slack-membership/check", follow=True,
        )
        self.assertContains(
            response,
            "Slack membership checked: Member. Community channels are connected.",
        )

    @patch("community.tasks.slack_membership.get_community_service")
    def test_partial_member_message(self, get_service):
        member = User.objects.create_user(email="partial-1572@test.com", tier=self.main)
        get_service.return_value = self.service([
            {"channel": "C_ONE", "ok": True},
            {"channel": "C_TWO", "ok": False, "error": "not_allowed"},
        ])
        response = self.client.post(
            f"/studio/users/{member.pk}/slack-membership/check", follow=True,
        )
        self.assertContains(
            response,
            "Slack membership checked: Member, but community channels could not be fully connected. Check the Slack integration and try again.",
        )

    @patch("community.tasks.slack_membership.get_community_service")
    def test_ineligible_member_message(self, get_service):
        member = User.objects.create_user(email="free-1572@test.com")
        get_service.return_value = self.service()
        response = self.client.post(
            f"/studio/users/{member.pk}/slack-membership/check", follow=True,
        )
        self.assertContains(
            response,
            "Slack membership checked: Member. Community channels were not changed because this account does not have community access.",
        )
        get_service.return_value.add_to_channels.assert_not_called()

    def test_csrf_and_staff_guards_have_no_side_effects(self):
        member = User.objects.create_user(email="guard-1572@test.com", tier=self.main)
        url = f"/studio/users/{member.pk}/slack-membership/check"
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        self.assertEqual(csrf_client.post(url).status_code, 403)
        self.assertIsNone(User.objects.get(pk=member.pk).slack_checked_at)

        regular = User.objects.create_user(
            email="regular-studio-1572@test.com", password="pw",
        )
        self.client.logout()
        self.client.login(email=regular.email, password="pw")
        self.assertEqual(self.client.post(url).status_code, 403)
        self.assertIsNone(User.objects.get(pk=member.pk).slack_checked_at)
