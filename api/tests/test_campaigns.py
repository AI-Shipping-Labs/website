"""Tests for the email campaign draft API (issue #676).

These endpoints are intentionally a flat draft-only surface: API callers can
create, read, list, and patch ``EmailCampaign`` rows but never send, test-
send, duplicate, or delete them. The send path stays operator-only via Studio.
"""

import inspect
import json
import re

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
from api.views import campaigns as campaigns_view
from email_app.models import EmailCampaign

User = get_user_model()


class CampaignsApiTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-campaigns@test.com",
            password="pw",
            is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="member-campaigns@test.com",
            password="pw",
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="campaigns")
        cls.non_staff_token = Token(
            key="non-staff-campaigns-token",
            user=cls.member,
            name="legacy-member-token",
        )
        Token.objects.bulk_create([cls.non_staff_token])

        cls.draft = EmailCampaign.objects.create(
            subject="Draft One",
            body="# hello draft",
            target_min_level=0,
            slack_filter="any",
            status="draft",
        )
        cls.sent = EmailCampaign.objects.create(
            subject="Already Sent",
            body="# sent body",
            target_min_level=10,
            slack_filter="any",
            status="sent",
            sent_count=42,
        )

    def _auth(self, token=None):
        if token is None:
            token = self.staff_token
        return {"HTTP_AUTHORIZATION": f"Token {token.key}"}

    def _post(self, payload, *, token=None):
        return self.client.post(
            "/api/campaigns",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(token),
        )

    def _patch(self, campaign_id, payload, *, token=None):
        return self.client.patch(
            f"/api/campaigns/{campaign_id}",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(token),
        )


class CampaignsAuthAndMethodTest(CampaignsApiTestBase):
    def test_all_endpoints_require_token(self):
        """All four routes return 401 without a valid staff token."""
        cases = [
            ("get", "/api/campaigns"),
            ("post", "/api/campaigns"),
            ("get", f"/api/campaigns/{self.draft.pk}"),
            ("patch", f"/api/campaigns/{self.draft.pk}"),
        ]
        for method, path in cases:
            with self.subTest(method=method, path=path):
                fn = getattr(self.client, method)
                if method in ("post", "patch"):
                    response = fn(
                        path,
                        data=json.dumps({}),
                        content_type="application/json",
                    )
                else:
                    response = fn(path)
                self.assertEqual(response.status_code, 401)

    def test_non_staff_token_rejected_on_all_endpoints(self):
        for method, path in (
            ("get", "/api/campaigns"),
            ("get", f"/api/campaigns/{self.draft.pk}"),
        ):
            with self.subTest(method=method, path=path):
                response = getattr(self.client, method)(
                    path, **self._auth(self.non_staff_token)
                )
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json(), {"error": "Invalid token"})

    def test_delete_returns_405_on_collection_and_detail(self):
        """DELETE is not in the require_methods allowlist."""
        before = EmailCampaign.objects.count()
        for path in ("/api/campaigns", f"/api/campaigns/{self.draft.pk}"):
            with self.subTest(path=path):
                response = self.client.delete(path, **self._auth())
                self.assertEqual(response.status_code, 405)
                self.assertEqual(response.json(), {"error": "Method not allowed"})
        self.assertEqual(EmailCampaign.objects.count(), before)


class CampaignsListTest(CampaignsApiTestBase):
    def test_list_returns_canonical_shape_and_orders_by_created_desc(self):
        response = self.client.get("/api/campaigns", **self._auth())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"campaigns"})

        # Newest first: the sent campaign was created after the draft.
        ids = [c["id"] for c in body["campaigns"]]
        self.assertEqual(ids, [self.sent.pk, self.draft.pk])

        # Canonical key set on every row.
        self.assertEqual(
            set(body["campaigns"][0].keys()),
            {
                "id",
                "subject",
                "body",
                "target_min_level",
                "target_tags_any",
                "target_tags_none",
                "slack_filter",
                "status",
                "is_archived",
                "sent_at",
                "sent_count",
                "created_at",
            },
        )

    def test_list_filters_by_status(self):
        response = self.client.get(
            "/api/campaigns?status=draft", **self._auth()
        )
        self.assertEqual(response.status_code, 200)
        ids = [c["id"] for c in response.json()["campaigns"]]
        self.assertEqual(ids, [self.draft.pk])

    def test_list_rejects_unknown_status_filter(self):
        response = self.client.get(
            "/api/campaigns?status=garbage", **self._auth()
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "validation_error")
        self.assertIn("status", response.json()["details"])

    def test_list_filters_by_archived(self):
        EmailCampaign.objects.filter(pk=self.draft.pk).update(is_archived=True)

        true_response = self.client.get(
            "/api/campaigns?archived=true", **self._auth()
        )
        self.assertEqual(true_response.status_code, 200)
        self.assertEqual(
            [c["id"] for c in true_response.json()["campaigns"]],
            [self.draft.pk],
        )

        false_response = self.client.get(
            "/api/campaigns?archived=false", **self._auth()
        )
        self.assertEqual(false_response.status_code, 200)
        self.assertEqual(
            [c["id"] for c in false_response.json()["campaigns"]],
            [self.sent.pk],
        )

    def test_list_rejects_invalid_archived_filter(self):
        response = self.client.get(
            "/api/campaigns?archived=maybe", **self._auth()
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "validation_error")
        self.assertIn("archived", response.json()["details"])


class CampaignsDetailTest(CampaignsApiTestBase):
    def test_get_detail_returns_full_shape(self):
        response = self.client.get(
            f"/api/campaigns/{self.draft.pk}", **self._auth()
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["id"], self.draft.pk)
        self.assertEqual(body["subject"], "Draft One")
        self.assertEqual(body["status"], "draft")
        self.assertFalse(body["is_archived"])

    def test_get_detail_unknown_id_returns_404_with_code(self):
        response = self.client.get("/api/campaigns/9999", **self._auth())
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "unknown_campaign")

    def test_patch_unknown_id_returns_404_with_code(self):
        response = self._patch(9999, {"subject": "x"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "unknown_campaign")


class CampaignsCreateTest(CampaignsApiTestBase):
    def test_post_creates_draft_with_minimal_fields(self):
        before = EmailCampaign.objects.count()
        response = self._post({"subject": "From API", "body": "# hi"})
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["subject"], "From API")
        self.assertEqual(body["status"], "draft")
        self.assertEqual(body["target_min_level"], 0)
        self.assertEqual(body["target_tags_any"], [])
        self.assertEqual(body["target_tags_none"], [])
        self.assertEqual(body["slack_filter"], "any")
        self.assertFalse(body["is_archived"])
        self.assertEqual(EmailCampaign.objects.count(), before + 1)

    def test_post_forces_status_to_draft_even_when_caller_sends_sent(self):
        """POST {"status": "sent"} is silently overwritten to draft."""
        response = self._post({
            "subject": "Try to skip ahead",
            "body": "# body",
            "status": "sent",
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], "draft")
        campaign = EmailCampaign.objects.get(pk=response.json()["id"])
        self.assertEqual(campaign.status, "draft")

    def test_post_requires_subject_and_body(self):
        no_subject = self._post({"body": "x"})
        self.assertEqual(no_subject.status_code, 422)
        self.assertIn("subject", no_subject.json()["details"])

        empty_subject = self._post({"subject": "   ", "body": "x"})
        self.assertEqual(empty_subject.status_code, 422)
        self.assertIn("subject", empty_subject.json()["details"])

        no_body = self._post({"subject": "x"})
        self.assertEqual(no_body.status_code, 422)
        self.assertIn("body", no_body.json()["details"])

        empty_body = self._post({"subject": "x", "body": "   "})
        self.assertEqual(empty_body.status_code, 422)
        self.assertIn("body", empty_body.json()["details"])

    def test_post_rejects_read_only_fields(self):
        before = EmailCampaign.objects.count()
        for field in ("id", "sent_at", "sent_count", "created_at"):
            with self.subTest(field=field):
                payload = {"subject": "x", "body": "y", field: "whatever"}
                response = self._post(payload)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["code"], "read_only_field")
                self.assertEqual(response.json()["details"]["field"], field)
        self.assertEqual(EmailCampaign.objects.count(), before)

    def test_post_validates_choices_and_types(self):
        response = self._post({
            "subject": "OK",
            "body": "# body",
            "target_min_level": 5,
            "slack_filter": "maybe",
            "target_tags_any": ["ok", 7],
            "target_tags_none": "not-a-list",
            "is_archived": "yes",
        })
        self.assertEqual(response.status_code, 422)
        details = response.json()["details"]
        for field in (
            "target_min_level",
            "slack_filter",
            "target_tags_any",
            "target_tags_none",
            "is_archived",
        ):
            self.assertIn(field, details)

    def test_post_normalizes_tags(self):
        response = self._post({
            "subject": "Tagged",
            "body": "# hi",
            "target_tags_any": ["Early Adopter", "early_adopter", " VIP "],
            "target_tags_none": ["DO NOT EMAIL"],
        })
        self.assertEqual(response.status_code, 201)
        body = response.json()
        # Duplicates collapse; case + separators normalize.
        self.assertEqual(body["target_tags_any"], ["early-adopter", "vip"])
        self.assertEqual(body["target_tags_none"], ["do-not-email"])

    def test_post_response_is_visible_in_subsequent_get(self):
        post = self._post({"subject": "Round trip", "body": "# rt"})
        self.assertEqual(post.status_code, 201)
        new_id = post.json()["id"]

        detail = self.client.get(
            f"/api/campaigns/{new_id}", **self._auth()
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["subject"], "Round trip")

        listing = self.client.get("/api/campaigns", **self._auth())
        ids = [c["id"] for c in listing.json()["campaigns"]]
        self.assertIn(new_id, ids)


class CampaignsPatchTest(CampaignsApiTestBase):
    def test_patch_updates_writable_fields(self):
        response = self._patch(self.draft.pk, {
            "subject": "Renamed",
            "target_min_level": 20,
            "slack_filter": "yes",
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["subject"], "Renamed")
        self.assertEqual(body["target_min_level"], 20)
        self.assertEqual(body["slack_filter"], "yes")
        # body unchanged because we didn't pass it
        self.assertEqual(body["body"], "# hello draft")

    def test_patch_cannot_promote_status(self):
        response = self._patch(self.draft.pk, {"status": "sending"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "validation_error")
        self.assertIn("status", response.json()["details"])
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, "draft")

    def test_patch_status_equal_to_current_is_silent_noop(self):
        response = self._patch(self.draft.pk, {"status": "draft"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "draft")

        sent_response = self._patch(self.sent.pk, {"status": "sent"})
        self.assertEqual(sent_response.status_code, 200)
        self.assertEqual(sent_response.json()["status"], "sent")

    def test_patch_is_archived_flips_field_and_visible_in_get(self):
        response = self._patch(self.draft.pk, {"is_archived": True})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["is_archived"])

        detail = self.client.get(
            f"/api/campaigns/{self.draft.pk}", **self._auth()
        )
        self.assertTrue(detail.json()["is_archived"])

        filtered = self.client.get(
            "/api/campaigns?archived=true", **self._auth()
        )
        ids = [c["id"] for c in filtered.json()["campaigns"]]
        self.assertIn(self.draft.pk, ids)

    def test_patch_rejects_read_only_fields(self):
        for field in ("id", "sent_at", "sent_count", "created_at"):
            with self.subTest(field=field):
                response = self._patch(self.draft.pk, {field: "x"})
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["code"], "read_only_field")
                self.assertEqual(response.json()["details"]["field"], field)

    def test_patch_validation_errors(self):
        response = self._patch(self.draft.pk, {
            "subject": "",
            "target_min_level": 5,
            "slack_filter": "huh",
        })
        self.assertEqual(response.status_code, 422)
        details = response.json()["details"]
        self.assertIn("subject", details)
        self.assertIn("target_min_level", details)
        self.assertIn("slack_filter", details)

    def test_patch_body_required_when_supplied_empty(self):
        response = self._patch(self.draft.pk, {"body": "   "})
        self.assertEqual(response.status_code, 422)
        self.assertIn("body", response.json()["details"])

    def test_patch_normalizes_tags(self):
        response = self._patch(self.draft.pk, {
            "target_tags_any": ["VIP", "vip"],
            "target_tags_none": ["No Email"],
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["target_tags_any"], ["vip"])
        self.assertEqual(body["target_tags_none"], ["no-email"])

    def test_patch_returns_422_on_non_object_body(self):
        response = self.client.patch(
            f"/api/campaigns/{self.draft.pk}",
            data=json.dumps([1, 2, 3]),
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_type")


class CampaignsSendPathSentinelTest(TestCase):
    """The API module MUST NOT reference ``send_campaign``.

    Sending stays operator-only via Studio (``studio/views/campaigns.py``)
    which enqueues ``email_app.tasks.send_campaign.send_campaign``. The API
    surface here is for draft authoring only — it must not import or invoke
    that task in any form, even by string reference.
    """

    def test_module_source_does_not_mention_send_campaign(self):
        source = inspect.getsource(campaigns_view)
        # Use a word-boundary regex so unrelated identifiers (none should
        # exist anyway) wouldn't false-match. ``send_campaign`` is the
        # task module name AND the task function name; either is a leak.
        self.assertIsNone(
            re.search(r"\bsend_campaign\b", source),
            msg=(
                "api/views/campaigns.py must not reference 'send_campaign'. "
                "The send path is Studio-only by design."
            ),
        )
