"""API coverage for issue #1194 campaign recipients and bounce clear."""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import EmailAlias, Token
from community.models import CommunityAuditLog
from email_app.models import EmailCampaign, EmailLog

User = get_user_model()


class Issue1194ApiBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="issue1194-api-staff@test.com",
            password="pw",
            is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="issue1194-api-member@test.com",
            password="pw",
            email_verified=True,
        )
        cls.non_staff = User.objects.create_user(
            email="issue1194-api-nonstaff@test.com",
            password="pw",
            email_verified=True,
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="issue1194")
        cls.non_staff_token = Token(
            key="issue1194-nonstaff-token",
            user=cls.non_staff,
            name="issue1194-nonstaff",
        )
        Token.objects.bulk_create([cls.non_staff_token])

    def _auth(self, token=None):
        token = token or self.staff_token
        return {"HTTP_AUTHORIZATION": f"Token {token.key}"}


class CampaignRecipientsApiTest(Issue1194ApiBase):
    def test_unauthorized_and_non_staff_cannot_read_recipient_emails(self):
        campaign = EmailCampaign.objects.create(
            subject="Secret Recipients",
            body="Hi",
            status="draft",
        )

        anon = self.client.get(f"/api/campaigns/{campaign.pk}/recipients")
        non_staff = self.client.get(
            f"/api/campaigns/{campaign.pk}/recipients",
            **self._auth(self.non_staff_token),
        )

        self.assertEqual(anon.status_code, 401)
        self.assertEqual(non_staff.status_code, 401)
        self.assertNotIn(self.member.email, anon.content.decode())
        self.assertNotIn(self.member.email, non_staff.content.decode())

    def test_unknown_campaign_returns_404(self):
        response = self.client.get(
            "/api/campaigns/999999/recipients",
            **self._auth(),
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "unknown_campaign")

    def test_draft_recipient_preview_uses_campaign_audience(self):
        campaign = EmailCampaign.objects.create(
            subject="Draft Recipients",
            body="Hi",
            status="draft",
            target_min_level=0,
        )

        response = self.client.get(
            f"/api/campaigns/{campaign.pk}/recipients",
            **self._auth(),
        )

        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["mode"], "preview")
        emails = {row["email"] for row in body["recipients"]}
        self.assertIn(self.member.email, emails)
        self.assertEqual(body["count"], len(body["recipients"]))

    def test_sent_recipient_logs_include_bounce_disposition(self):
        campaign = EmailCampaign.objects.create(
            subject="Sent Recipients",
            body="Hi",
            status="sent",
        )
        EmailLog.objects.create(
            campaign=campaign,
            user=self.member,
            email_type="campaign",
            opens=3,
            clicks=1,
            bounced_at=timezone.now(),
            bounce_type="Permanent",
            bounce_subtype="General",
            bounce_diagnostic="smtp; 550 mailbox missing",
        )

        response = self.client.get(
            f"/api/campaigns/{campaign.pk}/recipients",
            **self._auth(),
        )

        body = response.json()
        self.assertEqual(body["mode"], "sent")
        row = body["recipients"][0]
        self.assertEqual(row["email"], self.member.email)
        self.assertEqual(row["opens"], 3)
        self.assertEqual(row["clicks"], 1)
        self.assertEqual(row["disposition"], "bounced")
        self.assertEqual(row["bounce_diagnostic"], "smtp; 550 mailbox missing")


class ClearBounceAndAliasParityApiTest(Issue1194ApiBase):
    def test_staff_token_clears_bounce_without_resubscribing_and_audits_once(self):
        self.member.bounce_state = User.BounceState.PERMANENT
        self.member.unsubscribed = True
        self.member.last_bounce_diagnostic = "old"
        self.member.soft_bounce_count = 2
        self.member.save(update_fields=[
            "bounce_state",
            "unsubscribed",
            "last_bounce_diagnostic",
            "soft_bounce_count",
        ])

        response = self.client.post(
            f"/api/users/{self.member.email}/clear-bounce",
            data=json.dumps({"reason": "fixed"}),
            content_type="application/json",
            **self._auth(),
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["bounce_state"], "none")
        self.assertTrue(body["unsubscribed"])
        self.member.refresh_from_db()
        self.assertEqual(self.member.bounce_state, User.BounceState.NONE)
        self.assertTrue(self.member.unsubscribed)
        log = CommunityAuditLog.objects.get(
            user=self.member,
            action="api_mark_bounced",
        )
        self.assertIn("previous_state='permanent'", log.details)
        self.assertIn("new_state='none'", log.details)

    def test_clear_bounce_unknown_user_and_unknown_field_errors(self):
        missing = self.client.post(
            "/api/users/missing@example.com/clear-bounce",
            data=json.dumps({"reason": "fixed"}),
            content_type="application/json",
            **self._auth(),
        )
        unknown_field = self.client.post(
            f"/api/users/{self.member.email}/clear-bounce",
            data=json.dumps({"resubscribe": True}),
            content_type="application/json",
            **self._auth(),
        )

        self.assertEqual(missing.status_code, 404)
        self.assertEqual(unknown_field.status_code, 422)
        self.assertEqual(unknown_field.json()["code"], "unknown_field")

    def test_alias_collision_conflict_code_is_unchanged(self):
        User.objects.create_user(email="taken-api@example.com", password="pw")

        response = self.client.post(
            f"/api/users/{self.member.email}/aliases",
            data=json.dumps({"alias_email": "taken-api@example.com"}),
            content_type="application/json",
            **self._auth(),
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "alias_is_primary_email")
        self.assertFalse(EmailAlias.objects.filter(user=self.member).exists())
