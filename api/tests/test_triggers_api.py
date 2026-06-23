"""Tests for the authenticated triggers API (issue #1070).

Covers staff-token gating, the non-staff denial, secret-never-returned on
create/read, the is_active toggle, and the read-only emission/delivery
logs. No DELETE endpoint exists (deactivate via is_active).
"""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from accounts.models import Token
from triggers.models import (
    EventEmission,
    EventWidget,
    TriggerSubscription,
    WebhookDelivery,
)

User = get_user_model()


@tag("core")
class TriggersApiTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-triggers@test.com", password="pw", is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="member-triggers@test.com", password="pw",
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="triggers")
        cls.non_staff_token = Token(
            key="non-staff-triggers-token",
            user=cls.member,
            name="legacy-member-token",
        )
        Token.objects.bulk_create([cls.non_staff_token])

    def _auth(self, token=None):
        if token is None:
            token = self.staff_token
        return {"HTTP_AUTHORIZATION": f"Token {token.key}"}


class SubscriptionApiTest(TriggersApiTestBase):
    def test_requires_token(self):
        resp = self.client.get("/api/triggers/subscriptions")
        self.assertEqual(resp.status_code, 401)

    def test_non_staff_token_denied(self):
        resp = self.client.get(
            "/api/triggers/subscriptions", **self._auth(self.non_staff_token),
        )
        self.assertEqual(resp.status_code, 401)

    def test_create_does_not_echo_secret(self):
        payload = {
            "property_filter": {"name": "v0_workshop"},
            "target_url": "https://handler.example.com/hook",
            "secret": "topsecret",
        }
        resp = self.client.post(
            "/api/triggers/subscriptions",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertNotIn("secret", body)
        self.assertTrue(body["has_secret"])
        # The secret is stored even though it isn't returned.
        sub = TriggerSubscription.objects.get(pk=body["id"])
        self.assertEqual(sub.secret, "topsecret")

    def test_create_requires_secret_and_target(self):
        resp = self.client.post(
            "/api/triggers/subscriptions",
            data=json.dumps({}),
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(resp.status_code, 422)
        details = resp.json()["details"]
        self.assertIn("secret", details)
        self.assertIn("target_url", details)

    def test_detail_omits_secret(self):
        sub = TriggerSubscription.objects.create(
            event_type="custom",
            property_filter={},
            target_url="https://h.example.com/a",
            secret="s3cr3t",
        )
        resp = self.client.get(
            f"/api/triggers/subscriptions/{sub.id}", **self._auth(),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("secret", resp.json())

    def test_patch_toggles_is_active(self):
        sub = TriggerSubscription.objects.create(
            event_type="custom",
            property_filter={},
            target_url="https://h.example.com/a",
            secret="s",
        )
        resp = self.client.patch(
            f"/api/triggers/subscriptions/{sub.id}",
            data=json.dumps({"is_active": False}),
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(resp.status_code, 200)
        sub.refresh_from_db()
        self.assertFalse(sub.is_active)

    def test_delete_returns_405(self):
        sub = TriggerSubscription.objects.create(
            event_type="custom",
            property_filter={},
            target_url="https://h.example.com/a",
            secret="s",
        )
        resp = self.client.delete(
            f"/api/triggers/subscriptions/{sub.id}", **self._auth(),
        )
        self.assertEqual(resp.status_code, 405)


class WidgetApiTest(TriggersApiTestBase):
    def test_create_and_patch_widget(self):
        resp = self.client.post(
            "/api/triggers/widgets",
            data=json.dumps({"slug": "v0-claim", "event_name": "v0_workshop"}),
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(resp.status_code, 201)
        widget_id = resp.json()["id"]

        resp = self.client.patch(
            f"/api/triggers/widgets/{widget_id}",
            data=json.dumps({"is_active": False}),
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(EventWidget.objects.get(pk=widget_id).is_active)

    def test_duplicate_slug_rejected(self):
        EventWidget.objects.create(slug="dup", event_name="x")
        resp = self.client.post(
            "/api/triggers/widgets",
            data=json.dumps({"slug": "dup", "event_name": "y"}),
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(resp.status_code, 422)


class EmissionAndDeliveryApiTest(TriggersApiTestBase):
    def test_emissions_filterable_read_only(self):
        EventEmission.objects.create(
            user=self.member, event_name="v0_workshop",
            properties={}, envelope_id="evt_1",
        )
        EventEmission.objects.create(
            user=self.staff, event_name="other",
            properties={}, envelope_id="evt_2",
        )
        resp = self.client.get(
            "/api/triggers/emissions?event_name=v0_workshop", **self._auth(),
        )
        self.assertEqual(resp.status_code, 200)
        events = [e["event_name"] for e in resp.json()["emissions"]]
        self.assertEqual(events, ["v0_workshop"])

    def test_deliveries_filterable_by_success(self):
        em = EventEmission.objects.create(
            user=self.member, event_name="v0_workshop",
            properties={}, envelope_id="evt_d",
        )
        sub = TriggerSubscription.objects.create(
            event_type="custom", property_filter={},
            target_url="https://h.example.com/a", secret="s",
        )
        WebhookDelivery.objects.create(
            emission=em, subscription=sub, target_url=sub.target_url,
            succeeded=True, response_status=200,
        )
        WebhookDelivery.objects.create(
            emission=em, subscription=sub, target_url=sub.target_url,
            succeeded=False, response_status=500,
        )
        resp = self.client.get(
            "/api/triggers/deliveries?succeeded=false", **self._auth(),
        )
        statuses = [d["response_status"] for d in resp.json()["deliveries"]]
        self.assertEqual(statuses, [500])
