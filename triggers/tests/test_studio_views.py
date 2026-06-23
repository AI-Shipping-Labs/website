"""Tests for the Studio triggers screens (issue #1070).

Covers staff gating, subscription create with secret masking on the list,
the widget embed shortcode display, activate/deactivate (no delete), and
the read-only emission/delivery logs.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from triggers.models import (
    EventEmission,
    EventWidget,
    TriggerSubscription,
    WebhookDelivery,
)

User = get_user_model()


@tag("core")
class StudioTriggersTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@test.com", password="pw", is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="member@test.com", password="pw",
        )
        cls.subscription = TriggerSubscription.objects.create(
            event_type="custom",
            property_filter={"name": "v0_workshop"},
            target_url="https://handler.example.com/hook",
            secret="topsecret",
        )
        cls.widget = EventWidget.objects.create(
            slug="v0-claim", event_name="v0_workshop",
        )

    def test_non_staff_redirected_from_subscription_list(self):
        self.client.force_login(self.member)
        resp = self.client.get("/studio/triggers/subscriptions/")
        self.assertEqual(resp.status_code, 403)

    def test_subscription_list_masks_secret(self):
        self.client.force_login(self.staff)
        resp = self.client.get("/studio/triggers/subscriptions/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "topsecret")
        self.assertContains(resp, 'data-testid="subscription-secret-masked"')

    def test_create_subscription(self):
        self.client.force_login(self.staff)
        resp = self.client.post(
            "/studio/triggers/subscriptions/new/",
            data={
                "event_type": "custom",
                "property_filter": '{"name": "v0_workshop"}',
                "target_url": "https://new.example.com/hook",
                "secret": "newsecret",
                "description": "",
                "is_active": "on",
            },
        )
        self.assertEqual(resp.status_code, 302)
        sub = TriggerSubscription.objects.get(target_url="https://new.example.com/hook")
        self.assertEqual(sub.secret, "newsecret")
        self.assertEqual(sub.property_filter, {"name": "v0_workshop"})

    def test_edit_blank_secret_preserves_existing(self):
        self.client.force_login(self.staff)
        resp = self.client.post(
            f"/studio/triggers/subscriptions/{self.subscription.id}/edit/",
            data={
                "event_type": "custom",
                "property_filter": "{}",
                "target_url": "https://handler.example.com/hook",
                "secret": "",
                "description": "updated",
                "is_active": "on",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.secret, "topsecret")
        self.assertEqual(self.subscription.description, "updated")

    def test_toggle_deactivates_without_deleting(self):
        self.client.force_login(self.staff)
        resp = self.client.post(
            f"/studio/triggers/subscriptions/{self.subscription.id}/toggle/",
        )
        self.assertEqual(resp.status_code, 302)
        self.subscription.refresh_from_db()
        self.assertFalse(self.subscription.is_active)
        # Row still exists (deactivate, never delete).
        self.assertTrue(
            TriggerSubscription.objects.filter(pk=self.subscription.pk).exists()
        )

    def test_widget_list_shows_embed_shortcode(self):
        self.client.force_login(self.staff)
        resp = self.client.get("/studio/triggers/widgets/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-testid="widget-embed-shortcode"')
        self.assertContains(resp, "slug: v0-claim")

    def test_widget_toggle(self):
        self.client.force_login(self.staff)
        self.client.post(f"/studio/triggers/widgets/{self.widget.id}/toggle/")
        self.widget.refresh_from_db()
        self.assertFalse(self.widget.is_active)

    def test_emission_log_read_only(self):
        EventEmission.objects.create(
            user=self.member, event_name="v0_workshop",
            properties={}, envelope_id="evt_x",
        )
        self.client.force_login(self.staff)
        resp = self.client.get("/studio/triggers/emissions/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "evt_x")

    def test_delivery_log_filterable(self):
        em = EventEmission.objects.create(
            user=self.member, event_name="v0_workshop",
            properties={}, envelope_id="evt_y",
        )
        WebhookDelivery.objects.create(
            emission=em, subscription=self.subscription,
            target_url=self.subscription.target_url,
            succeeded=False, response_status=500,
        )
        self.client.force_login(self.staff)
        resp = self.client.get("/studio/triggers/deliveries/?succeeded=false")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-testid="delivery-failed"')
