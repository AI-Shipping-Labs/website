"""Security, retry-state, and abuse regressions for issue #1070."""

import json
import socket
from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, tag
from django.utils import timezone

from accounts.services.privacy import build_user_data_export
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from jobs.tasks.cleanup import cleanup_old_webhook_deliveries
from triggers.destinations import (
    _PinnedHTTPSConnection,
    post_pinned_https,
    validate_outbound_url,
)
from triggers.dispatch import emit_event
from triggers.models import (
    TriggerSubscription,
    WebhookDelivery,
    WebhookDeliveryJob,
)
from triggers.tasks import _claim_attempt, deliver_webhook, resume_due_webhook_deliveries

User = get_user_model()


def _records(address):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443))]


class _Response:
    def __init__(self, status_code, text="ok"):
        self.status_code = status_code
        self.text = text


@tag("core")
class DestinationSecurityTest(TestCase):
    def test_rejects_unsafe_schemes_ports_credentials_and_literal_ips(self):
        values = [
            "http://public.example/hook",
            "https://public.example:8443/hook",
            "https://user:pass@public.example/hook",
            "https://127.0.0.1/hook",
            "https://10.0.0.1/hook",
            "https://169.254.169.254/latest/meta-data/",
            "https://[::1]/hook",
            "https://[fc00::1]/hook",
        ]
        for value in values:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                validate_outbound_url(value)

    @patch("triggers.destinations.socket.getaddrinfo")
    def test_rejects_if_any_dns_answer_is_private(self, resolve):
        resolve.return_value = _records("93.184.216.34") + _records("10.0.0.8")
        with self.assertRaises(ValidationError):
            validate_outbound_url("https://hooks.partner.test/claim")

    @patch("triggers.destinations.socket.getaddrinfo")
    def test_accepts_public_ipv4_and_ipv6_answers(self, resolve):
        resolve.return_value = _records("93.184.216.34") + _records("2606:2800:220:1:248:1893:25c8:1946")
        addresses = validate_outbound_url("https://hooks.partner.test/claim")
        self.assertEqual({str(value) for value in addresses}, {"93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"})

    @patch("triggers.destinations.connection.create_connection")
    def test_actual_socket_uses_pinned_public_ip_not_a_third_dns_lookup(self, connect):
        connect.return_value = Mock()
        connection = _PinnedHTTPSConnection(
            "hooks.partner.test",
            443,
            pinned_ip="93.184.216.34",
        )
        connection._new_conn()
        self.assertEqual(connect.call_args.args[0], ("93.184.216.34", 443))

    @patch("triggers.destinations._PinnedHTTPSConnectionPool")
    def test_transport_preserves_tls_hostname_host_and_disables_redirects(self, pool_cls):
        response = Mock(status=200)
        response.read.return_value = b"ok"
        pool_cls.return_value.urlopen.return_value = response
        result = post_pinned_https(
            "https://hooks.partner.test/claim?q=1",
            pinned_ip="93.184.216.34",
            body=b"{}",
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        self.assertEqual(result.status_code, 200)
        kwargs = pool_cls.call_args.kwargs
        self.assertEqual(kwargs["pinned_ip"], "93.184.216.34")
        self.assertEqual(kwargs["server_hostname"], "hooks.partner.test")
        call = pool_cls.return_value.urlopen.call_args
        self.assertEqual(call.args[:2], ("POST", "/claim?q=1"))
        self.assertFalse(call.kwargs["redirect"])
        self.assertEqual(call.kwargs["headers"]["Host"], "hooks.partner.test")
        response.read.assert_called_once_with(2001, decode_content=True)


@tag("core")
@patch("website.release_phase.R2_BACKGROUND_WORK_ENABLED", True)
class DurableDeliveryTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email="durable@test.com", password="x", email_verified=True)
        cls.subscription = TriggerSubscription.objects.create(
            target_url="https://handler.example.com/hook",
            secret="first-secret",
            property_filter={},
        )

    def setUp(self):
        IntegrationSetting.objects.update_or_create(
            key="TRIGGERS_ENABLED",
            defaults={"value": "true", "group": "triggers"},
        )
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def _emission(self):
        with patch("triggers.dispatch.async_task"):
            emission, _ = emit_event("durable_claim", self.user, {"name": "durable_claim"})
        return emission

    def test_secret_is_encrypted_at_rest_and_rotation_is_versioned(self):
        self.assertNotIn("first-secret", self.subscription.encrypted_secret)
        self.assertEqual(self.subscription.legacy_secret, "first-secret")
        self.assertEqual(self.subscription.secret, "first-secret")
        self.subscription.secret = "second-secret"
        self.subscription.save()
        self.assertEqual(self.subscription.secret_version, 2)
        self.assertEqual(self.subscription.legacy_secret, "second-secret")
        self.assertEqual(self.subscription.secret_candidates()[1], (1, "first-secret"))

    def test_r1_reconciles_a_secret_changed_by_the_production_image(self):
        from triggers.management.commands.reconcile_r1_expand import (
            reconcile_r1_expand,
        )

        TriggerSubscription.objects.filter(pk=self.subscription.pk).update(
            legacy_secret="production-overlap-secret",
        )
        counts = reconcile_r1_expand()
        self.subscription.refresh_from_db()

        self.assertEqual(counts["subscriptions"], 1)
        self.assertEqual(self.subscription.secret, "production-overlap-secret")
        self.assertEqual(
            self.subscription.legacy_secret,
            "production-overlap-secret",
        )
        self.assertEqual(reconcile_r1_expand()["subscriptions"], 0)

    @patch("triggers.tasks.post_pinned_https")
    def test_snapshot_is_immutable_across_subscription_rotation(self, post):
        emission = self._emission()
        job = WebhookDeliveryJob.objects.get(emission=emission)
        original_body = job.request_body
        self.subscription.target_url = "https://new-handler.example.com/hook"
        self.subscription.secret = "rotated"
        self.subscription.save()
        post.return_value = _Response(200)
        deliver_webhook(emission.pk, self.subscription.pk)
        kwargs = post.call_args.kwargs
        self.assertEqual(post.call_args.args[0], "https://handler.example.com/hook")
        self.assertEqual(kwargs["body"].decode(), original_body)
        self.assertEqual(json.loads(original_body)["occurred_at"], emission.occurred_at.isoformat())
        self.assertEqual(kwargs["headers"]["X-AISL-Secret-Version"], "1")

    @patch("triggers.tasks.post_pinned_https")
    def test_emergency_pause_blocks_already_queued_attempt(self, post):
        emission = self._emission()
        self.subscription.is_active = False
        self.subscription.save(update_fields=["is_active", "updated_at"])
        deliver_webhook(emission.pk, self.subscription.pk)
        post.assert_not_called()
        self.assertEqual(WebhookDeliveryJob.objects.get().status, WebhookDeliveryJob.STATUS_PAUSED)

    @patch("triggers.tasks.post_pinned_https")
    def test_exactly_four_db_owned_attempts_then_terminal_failure(self, post):
        emission = self._emission()
        post.return_value = _Response(503, "down")
        for _ in range(4):
            deliver_webhook(emission.pk, self.subscription.pk)
            WebhookDeliveryJob.objects.update(next_attempt_at=timezone.now() - timedelta(seconds=1))
        deliver_webhook(emission.pk, self.subscription.pk)
        job = WebhookDeliveryJob.objects.get()
        self.assertEqual(post.call_count, 4)
        self.assertEqual(job.attempt_count, 4)
        self.assertEqual(job.status, WebhookDeliveryJob.STATUS_FAILED)
        self.assertEqual(WebhookDelivery.objects.count(), 4)

    def test_active_lease_excludes_a_competing_worker(self):
        emission = self._emission()
        job = WebhookDeliveryJob.objects.get(emission=emission)
        first = _claim_attempt(job.pk)
        second = _claim_attempt(job.pk)
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    @patch("triggers.tasks.async_task")
    def test_recovery_schedule_wakes_due_database_job(self, enqueue):
        emission = self._emission()
        WebhookDeliveryJob.objects.filter(emission=emission).update(
            next_attempt_at=timezone.now() - timedelta(seconds=1),
        )
        result = resume_due_webhook_deliveries()
        self.assertEqual(result, {"enqueued": 1})
        self.assertEqual(enqueue.call_args.args[0], "triggers.tasks.deliver_webhook")
        self.assertEqual(enqueue.call_args.kwargs["max_retries"], 0)

    @patch("triggers.tasks.post_pinned_https")
    def test_privacy_export_and_terminal_retention_cover_snapshot_pii(self, post):
        emission = self._emission()
        post.return_value = _Response(200)
        deliver_webhook(emission.pk, self.subscription.pk)
        payload = build_user_data_export(self.user)["communications_activity"]
        self.assertEqual(payload["trigger_event_emissions"][0]["envelope_id"], emission.envelope_id)
        self.assertEqual(payload["trigger_delivery_jobs"][0]["status"], "succeeded")
        self.assertEqual(payload["trigger_webhook_deliveries"][0]["attempt"], 1)

        old = timezone.now() - timedelta(days=31)
        WebhookDeliveryJob.objects.update(updated_at=old)
        result = cleanup_old_webhook_deliveries(days=30)
        self.assertEqual(result["deleted_jobs"], 1)
        self.assertEqual(result["deleted"], 1)
        self.assertFalse(WebhookDeliveryJob.objects.exists())
        self.assertFalse(WebhookDelivery.objects.exists())

    def test_account_deletion_cascades_snapshot_and_attempt_data(self):
        self._emission()
        self.user.delete()
        self.assertFalse(WebhookDeliveryJob.objects.exists())
