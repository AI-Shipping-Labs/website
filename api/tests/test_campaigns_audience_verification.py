"""API tests for ``audience_verification`` round-trip (issue #692).

Builds on the shared base in ``api/tests/test_campaigns.py``. The endpoints
under test are documented there; this module only verifies the new field's
serialization, validation, and round-trip on POST + PATCH.
"""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
from email_app.models import EmailCampaign

User = get_user_model()


class CampaignsApiAudienceVerificationBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-av@test.com",
            password="pw",
            is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name="av")
        cls.draft = EmailCampaign.objects.create(
            subject="AV Draft",
            body="# body",
            target_min_level=0,
        )

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}

    def _post(self, payload):
        return self.client.post(
            "/api/campaigns",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(),
        )

    def _patch(self, campaign_id, payload):
        return self.client.patch(
            f"/api/campaigns/{campaign_id}",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(),
        )


class CampaignsApiSerializationTest(CampaignsApiAudienceVerificationBase):
    def test_list_includes_audience_verification(self):
        response = self.client.get("/api/campaigns", **self._auth())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        first = body["campaigns"][0]
        self.assertIn("audience_verification", first)
        self.assertEqual(first["audience_verification"], "verified_only")

    def test_detail_includes_audience_verification(self):
        response = self.client.get(
            f"/api/campaigns/{self.draft.pk}", **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["audience_verification"], "verified_only")


class CampaignsApiPostAudienceVerificationTest(
    CampaignsApiAudienceVerificationBase,
):
    def test_post_defaults_to_verified_only_when_omitted(self):
        response = self._post({"subject": "No AV", "body": "# b"})
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["audience_verification"], "verified_only")
        campaign = EmailCampaign.objects.get(pk=body["id"])
        self.assertEqual(campaign.audience_verification, "verified_only")

    def test_post_accepts_everyone(self):
        response = self._post({
            "subject": "Broadcast",
            "body": "# b",
            "audience_verification": "everyone",
        })
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["audience_verification"], "everyone")
        campaign = EmailCampaign.objects.get(pk=body["id"])
        self.assertEqual(campaign.audience_verification, "everyone")

    def test_post_rejects_unknown_value_with_422(self):
        response = self._post({
            "subject": "Bogus",
            "body": "# b",
            "audience_verification": "everybody",
        })
        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertEqual(payload["code"], "validation_error")
        self.assertEqual(
            payload["details"]["audience_verification"],
            "Unknown audience verification.",
        )


class CampaignsApiPatchAudienceVerificationTest(
    CampaignsApiAudienceVerificationBase,
):
    def test_patch_updates_audience_verification(self):
        response = self._patch(
            self.draft.pk, {"audience_verification": "everyone"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["audience_verification"], "everyone")
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.audience_verification, "everyone")

    def test_patch_rejects_unknown_value_with_422(self):
        response = self._patch(
            self.draft.pk, {"audience_verification": "nope"},
        )
        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertEqual(payload["code"], "validation_error")
        self.assertEqual(
            payload["details"]["audience_verification"],
            "Unknown audience verification.",
        )
        # Field value untouched on the row.
        self.draft.refresh_from_db()
        self.assertEqual(
            self.draft.audience_verification, "verified_only",
        )

    def test_patch_round_trips_through_subsequent_get(self):
        self._patch(self.draft.pk, {"audience_verification": "everyone"})
        detail = self.client.get(
            f"/api/campaigns/{self.draft.pk}", **self._auth(),
        )
        self.assertEqual(detail.json()["audience_verification"], "everyone")
