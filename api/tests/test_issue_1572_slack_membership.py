"""Operator API contract for Slack membership/channel reconciliation (#1572)."""

from unittest.mock import MagicMock, patch

from django.test import TestCase

from accounts.models import EmailAlias, Token, User
from api.openapi import build_spec
from api.urls import urlpatterns
from community.models import CommunityAuditLog
from payments.models import Tier


class SlackMembershipCheckApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-1572@test.com", is_staff=True,
        )
        cls.staff_token = Token.objects.create(
            user=cls.staff, name="slack-operator",
        )
        cls.nonstaff = User.objects.create_user(
            email="member-token-1572@test.com", is_staff=True,
        )
        cls.nonstaff_token = Token.objects.create(user=cls.nonstaff, name="member")
        cls.nonstaff.is_staff = False
        cls.nonstaff.save(update_fields=["is_staff"])
        cls.main = Tier.objects.get(level=20)
        cls.member = User.objects.create_user(
            email="canonical-1572@test.com", tier=cls.main,
        )
        cls.alias = EmailAlias.objects.create(
            user=cls.member,
            email="alias-1572@test.com",
            source=EmailAlias.SOURCE_MANUAL,
            created_by=cls.staff,
        )

    def auth(self, token=None):
        token = token or self.staff_token
        return {"HTTP_AUTHORIZATION": f"Token {token.key}"}

    def service(self, results=None):
        service = MagicMock()
        service.channel_ids = ["C_ONE", "C_TWO"]
        service.check_workspace_membership.return_value = ("member", "U1572")
        service.lookup_user_profile_by_email.return_value = None
        service.add_to_channels.return_value = results or [
            {"channel": "C_ONE", "ok": True},
            {"channel": "C_TWO", "ok": False, "error": "not_allowed"},
        ]
        return service

    @patch("community.tasks.slack_membership.get_community_service")
    def test_alias_returns_canonical_member_and_partial_channel_summary(self, get_service):
        get_service.return_value = self.service()

        response = self.client.post(
            f"/api/users/{self.alias.email}/slack-membership/check",
            **self.auth(),
        )

        self.assertEqual(response.json(), {
            "email": self.member.email,
            "outcome": "member",
            "slack_member": True,
            "slack_user_id": "U1572",
            "slack_checked_at": response.json()["slack_checked_at"],
            "channel_reconciliation": {
                "status": "partial",
                "configured_count": 2,
                "added_count": 1,
                "already_present_count": 0,
                "failed_count": 1,
            },
        })
        audit = CommunityAuditLog.objects.get(user=self.member, action="link")
        self.assertNotIn(self.member.email, audit.details)
        self.assertIn('"actor_token": "slack-operator"', audit.details)

    def test_openapi_documents_exact_response_contract(self):
        operation = build_spec(urlpatterns)["paths"][
            "/api/users/{email}/slack-membership/check"
        ]["post"]
        responses = operation["responses"]

        self.assertEqual(set(responses), {"200", "401", "404", "405", "503"})
        success = responses["200"]["content"]["application/json"]
        self.assertIn("channel_reconciliation", success["example"])
        self.assertNotIn("channels", success["example"])
        self.assertEqual(
            set(success["example"]["channel_reconciliation"]),
            {
                "status",
                "configured_count",
                "added_count",
                "already_present_count",
                "failed_count",
            },
        )
        for status, code in (
            ("401", "authentication_required"),
            ("405", "method_not_allowed"),
        ):
            content = responses[status]["content"]["application/json"]
            self.assertEqual(
                content["schema"],
                {"$ref": "#/components/schemas/ErrorResponse"},
            )
            self.assertEqual(content["example"]["code"], code)

    def test_authentication_and_method_checks_precede_user_lookup(self):
        url = "/api/users/private-missing-1572@test.com/slack-membership/check"
        missing = self.client.post(url)
        malformed = self.client.post(url, HTTP_AUTHORIZATION="Bearer nope")
        invalid = self.client.post(url, HTTP_AUTHORIZATION="Token invalid")
        nonstaff = self.client.post(url, **self.auth(self.nonstaff_token))
        method = self.client.get(url, **self.auth())

        self.assertEqual(missing.json(), {
            "error": "Authentication token required",
            "code": "authentication_required",
        })
        self.assertEqual(malformed.json(), missing.json())
        self.assertEqual(invalid.json(), {"error": "Invalid token", "code": "invalid_token"})
        self.assertEqual(nonstaff.json(), invalid.json())
        self.assertEqual(method.status_code, 405)
        self.assertEqual(method.json()["code"], "method_not_allowed")
        self.assertEqual(CommunityAuditLog.objects.count(), 0)

    @patch("community.tasks.slack_membership.get_community_service")
    def test_unknown_is_503_and_mutation_free(self, get_service):
        service = self.service()
        service.check_workspace_membership.return_value = ("unknown", None)
        get_service.return_value = service

        response = self.client.post(
            f"/api/users/{self.member.email}/slack-membership/check",
            **self.auth(),
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "slack_membership_unavailable")
        self.member.refresh_from_db()
        self.assertIsNone(self.member.slack_checked_at)
        self.assertFalse(self.member.slack_member)
        service.add_to_channels.assert_not_called()

    def test_unknown_primary_or_alias_is_404(self):
        response = self.client.post(
            "/api/users/missing-1572@test.com/slack-membership/check",
            **self.auth(),
        )
        self.assertEqual(response.status_code, 404)
