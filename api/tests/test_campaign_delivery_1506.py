"""Staff-token campaign recipient reconciliation API (#1506)."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from email_app.models import CampaignDelivery, EmailCampaign, EmailLog
from tests.fixtures import TierSetupMixin

User = get_user_model()


class CampaignDeliveryApi1506Test(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.staff = User.objects.create_user(
            email="staff-1506@test.com",
            password="pw",
            is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="member-1506@test.com",
            password="pw",
            email_verified=True,
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="campaigns-1506")
        cls.non_staff_token = Token(
            key="non-staff-1506-token",
            user=cls.member,
            name="legacy-member-token",
        )
        Token.objects.bulk_create([cls.non_staff_token])

    def _auth(self, token=None):
        token = token or self.staff_token
        return {"HTTP_AUTHORIZATION": f"Token {token.key}"}

    def _snapshotted(self, *, state, email="recipient-1506@test.com"):
        user = User.objects.create_user(
            email=email,
            tier=self.free_tier,
            email_verified=True,
        )
        campaign = EmailCampaign.objects.create(
            subject="API campaign",
            body="Hi",
            status="needs_attention",
            audience_snapshotted_at=timezone.now(),
        )
        delivery = CampaignDelivery.objects.create(
            campaign=campaign,
            user=user,
            recipient_user_pk=user.pk,
            recipient_email=user.email,
            state=state,
            attempt_count=1,
        )
        return campaign, user, delivery

    def test_detail_includes_snapshot_and_zero_filled_delivery_counts(self):
        draft = EmailCampaign.objects.create(subject="Draft", body="Hi")
        campaign, _user, _delivery = self._snapshotted(
            state=CampaignDelivery.State.FAILED,
        )

        draft_body = self.client.get(
            f"/api/campaigns/{draft.pk}", **self._auth(),
        ).json()
        snapshotted = self.client.get(
            f"/api/campaigns/{campaign.pk}", **self._auth(),
        ).json()
        listing = self.client.get("/api/campaigns", **self._auth()).json()

        self.assertIsNone(draft_body["audience_snapshotted_at"])
        self.assertEqual(
            draft_body["delivery_counts"],
            {
                "pending": 0,
                "dispatching": 0,
                "sent": 0,
                "skipped": 0,
                "failed": 0,
                "ambiguous": 0,
                "assumed_sent": 0,
            },
        )
        self.assertIsNotNone(snapshotted["audience_snapshotted_at"])
        self.assertEqual(snapshotted["delivery_counts"]["failed"], 1)
        listed = next(
            row for row in listing["campaigns"] if row["id"] == campaign.pk
        )
        self.assertEqual(listed["delivery_counts"]["failed"], 1)

    def test_retry_and_assume_sent_require_staff_token(self):
        campaign, _user, delivery = self._snapshotted(
            state=CampaignDelivery.State.FAILED,
        )
        retry_url = (
            f"/api/campaigns/{campaign.pk}/recipients/{delivery.pk}/retry"
        )
        assume_url = (
            f"/api/campaigns/{campaign.pk}/recipients/{delivery.pk}/assume-sent"
        )

        for url in (retry_url, assume_url):
            with self.subTest(url=url):
                anon = self.client.post(url)
                member = self.client.post(url, **self._auth(self.non_staff_token))
                self.assertEqual(anon.status_code, 401)
                self.assertEqual(member.status_code, 401)

    def test_unknown_campaign_or_delivery_returns_404(self):
        campaign, _user, delivery = self._snapshotted(
            state=CampaignDelivery.State.FAILED,
        )

        unknown_campaign = self.client.post(
            f"/api/campaigns/999999/recipients/{delivery.pk}/retry",
            **self._auth(),
        )
        unknown_delivery = self.client.post(
            f"/api/campaigns/{campaign.pk}/recipients/999999/retry",
            **self._auth(),
        )

        self.assertEqual(unknown_campaign.status_code, 404)
        self.assertEqual(unknown_campaign.json()["code"], "unknown_campaign")
        self.assertEqual(unknown_delivery.status_code, 404)
        self.assertEqual(unknown_delivery.json()["code"], "unknown_delivery")

    @patch("jobs.tasks.async_task", return_value="api-retry")
    def test_retry_matches_studio_and_conflicts_on_stale_state(self, enqueue):
        campaign, user, delivery = self._snapshotted(
            state=CampaignDelivery.State.FAILED,
            email="retry-api@test.com",
        )

        response = self.client.post(
            f"/api/campaigns/{campaign.pk}/recipients/{delivery.pk}/retry",
            **self._auth(),
        )
        conflict = self.client.post(
            f"/api/campaigns/{campaign.pk}/recipients/{delivery.pk}/retry",
            **self._auth(),
        )

        delivery.refresh_from_db()
        campaign.refresh_from_db()
        body = response.json()
        self.assertEqual(body["delivery_id"], delivery.pk)
        self.assertEqual(body["delivery_state"], "pending")
        self.assertEqual(body["campaign_status"], "sending")
        self.assertEqual(body["sent_count"], 0)
        self.assertEqual(delivery.resolved_by_id, self.staff.pk)
        self.assertEqual(
            enqueue.call_args.kwargs["campaign_id"],
            campaign.pk,
        )
        self.assertEqual(
            enqueue.call_args.kwargs["delivery_ids"],
            [delivery.pk],
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["code"], "delivery_conflict")

    @patch("email_app.tasks.send_campaign.EmailService.send_prepared")
    def test_assume_sent_never_sends_or_fabricates_log(self, transport):
        campaign, _user, delivery = self._snapshotted(
            state=CampaignDelivery.State.AMBIGUOUS,
            email="assume-api@test.com",
        )

        response = self.client.post(
            f"/api/campaigns/{campaign.pk}/recipients/{delivery.pk}/assume-sent",
            **self._auth(),
        )
        conflict = self.client.post(
            f"/api/campaigns/{campaign.pk}/recipients/{delivery.pk}/assume-sent",
            **self._auth(),
        )

        delivery.refresh_from_db()
        campaign.refresh_from_db()
        body = response.json()
        self.assertEqual(body["delivery_state"], "assumed_sent")
        self.assertEqual(body["campaign_status"], "sent")
        self.assertEqual(body["sent_count"], 0)
        self.assertEqual(campaign.sent_count, 0)
        self.assertFalse(EmailLog.objects.filter(campaign=campaign).exists())
        transport.assert_not_called()
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["code"], "delivery_conflict")
