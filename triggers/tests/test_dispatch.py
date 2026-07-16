"""Tests for the emit_event dispatch pipeline (issue #1070).

Covers: emission recording, the TRIGGERS_ENABLED short-circuit, the
exact-match property filter, the empty-filter match-all behaviour, and the
unique (user, event_name) dedup that makes a duplicate claim a no-op.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from triggers.dispatch import emit_event
from triggers.models import EventEmission, TriggerSubscription

User = get_user_model()


def _enable_triggers():
    IntegrationSetting.objects.update_or_create(
        key="TRIGGERS_ENABLED",
        defaults={"value": "true", "group": "triggers"},
    )
    clear_config_cache()


class _FakeDeliveryResponse:
    """Minimal stand-in for a ``requests`` response in delivery tests."""

    def __init__(self, status_code, text="ok"):
        self.status_code = status_code
        self.text = text


@tag("core")
class EmitEventTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="member@example.com", password="x", email_verified=True,
        )

    def setUp(self):
        _enable_triggers()

    def tearDown(self):
        clear_config_cache()

    def test_emit_records_emission_and_returns_created(self):
        with patch("triggers.dispatch.async_task") as mock_task:
            emission, created = emit_event(
                "v0_workshop", self.user, {"name": "v0_workshop"},
            )
        self.assertTrue(created)
        self.assertIsNotNone(emission)
        self.assertEqual(emission.event_name, "v0_workshop")
        self.assertTrue(emission.envelope_id.startswith("evt_"))
        self.assertEqual(EventEmission.objects.count(), 1)
        # No subscriptions exist, so nothing dispatched.
        mock_task.assert_not_called()

    def test_min_level_kwarg_is_persisted_into_emission_properties(self):
        # Regression (#1070 review): emit_event ignored the min_level kwarg, so
        # the recorded emission never carried it and the wire envelope sent
        # data.min_level=null. It must persist into the emission properties.
        with patch("triggers.dispatch.async_task"):
            emission, _ = emit_event(
                "v0_workshop",
                self.user,
                {"name": "v0_workshop"},
                min_level=5,
            )
        self.assertEqual(emission.properties["min_level"], 5)

    def test_explicit_min_level_in_properties_wins_over_kwarg(self):
        with patch("triggers.dispatch.async_task"):
            emission, _ = emit_event(
                "v0_workshop",
                self.user,
                {"name": "v0_workshop", "min_level": 20},
                min_level=5,
            )
        self.assertEqual(emission.properties["min_level"], 20)

    def test_dispatched_envelope_carries_real_min_level(self):
        # End-to-end through emit_event -> deliver_webhook: the wire envelope's
        # data.min_level must equal the value passed to emit_event, not null.
        import json

        from triggers.tasks import deliver_webhook

        TriggerSubscription.objects.create(
            event_type="custom",
            property_filter={},
            target_url="https://handler.example.com/hook",
            secret="s",
        )
        with patch("triggers.dispatch.async_task"):
            emission, _ = emit_event(
                "v0_workshop",
                self.user,
                {"name": "v0_workshop"},
                min_level=5,
            )

        subscription = TriggerSubscription.objects.get()
        with patch("triggers.tasks.post_pinned_https") as mock_post:
            mock_post.return_value = _FakeDeliveryResponse(200)
            deliver_webhook(emission.id, subscription.id)

        _args, kwargs = mock_post.call_args
        envelope = json.loads(kwargs["body"].decode("utf-8"))
        self.assertEqual(envelope["data"]["min_level"], 5)

    def test_flag_off_records_nothing_and_dispatches_nothing(self):
        IntegrationSetting.objects.update_or_create(
            key="TRIGGERS_ENABLED",
            defaults={"value": "false", "group": "triggers"},
        )
        clear_config_cache()
        with patch("triggers.dispatch.async_task") as mock_task:
            emission, created = emit_event("v0_workshop", self.user, {})
        self.assertIsNone(emission)
        self.assertFalse(created)
        self.assertEqual(EventEmission.objects.count(), 0)
        mock_task.assert_not_called()

    def test_matching_filter_dispatches_non_matching_does_not(self):
        match = TriggerSubscription.objects.create(
            event_type="custom",
            property_filter={"name": "v0_workshop"},
            target_url="https://handler.example.com/a",
            secret="s",
        )
        other = TriggerSubscription.objects.create(
            event_type="custom",
            property_filter={"name": "other_campaign"},
            target_url="https://handler.example.com/b",
            secret="s",
        )
        with patch("triggers.dispatch.async_task") as mock_task:
            emission, _ = emit_event(
                "v0_workshop", self.user, {"name": "v0_workshop"},
            )
        dispatched_sub_ids = {call.args[2] for call in mock_task.call_args_list}
        self.assertIn(match.id, dispatched_sub_ids)
        self.assertNotIn(other.id, dispatched_sub_ids)
        self.assertEqual(mock_task.call_count, 1)

    def test_empty_filter_matches_every_event(self):
        catch_all = TriggerSubscription.objects.create(
            event_type="custom",
            property_filter={},
            target_url="https://handler.example.com/all",
            secret="s",
        )
        with patch("triggers.dispatch.async_task") as mock_task:
            emit_event("v0_workshop", self.user, {"name": "anything"})
        dispatched_sub_ids = {call.args[2] for call in mock_task.call_args_list}
        self.assertIn(catch_all.id, dispatched_sub_ids)

    def test_queue_outage_leaves_recoverable_job_without_failing_emit(self):
        from triggers.models import WebhookDeliveryJob

        TriggerSubscription.objects.create(
            event_type="custom",
            property_filter={},
            target_url="https://handler.example.com/all",
            secret="s",
        )
        with patch(
            "website.release_phase.R2_BACKGROUND_WORK_ENABLED", True,
        ), patch("triggers.dispatch.async_task", side_effect=RuntimeError("queue down")):
            emission, created = emit_event("v0_workshop", self.user, {"name": "anything"})
        self.assertTrue(created)
        self.assertEqual(
            WebhookDeliveryJob.objects.get(emission=emission).status,
            WebhookDeliveryJob.STATUS_PENDING,
        )

    def test_r1_publishes_legacy_task_without_durable_job(self):
        from triggers.models import WebhookDeliveryJob

        subscription = TriggerSubscription.objects.create(
            event_type="custom",
            property_filter={},
            target_url="https://handler.example.com/r1",
            secret="s",
        )
        with patch("triggers.dispatch.async_task") as enqueue:
            emission, created = emit_event(
                "v0_workshop", self.user, {"name": "anything"},
            )

        self.assertTrue(created)
        enqueue.assert_called_once()
        self.assertEqual(enqueue.call_args.args[:3], (
            "triggers.tasks.deliver_webhook", emission.pk, subscription.pk,
        ))
        self.assertEqual(enqueue.call_args.kwargs["max_retries"], 3)
        self.assertFalse(WebhookDeliveryJob.objects.filter(emission=emission).exists())

    def test_inactive_subscription_does_not_fire(self):
        inactive = TriggerSubscription.objects.create(
            event_type="custom",
            property_filter={},
            target_url="https://handler.example.com/x",
            secret="s",
            is_active=False,
        )
        with patch("triggers.dispatch.async_task") as mock_task:
            emit_event("v0_workshop", self.user, {})
        dispatched_sub_ids = {call.args[2] for call in mock_task.call_args_list}
        self.assertNotIn(inactive.id, dispatched_sub_ids)

    def test_duplicate_claim_is_noop_returns_existing(self):
        sub = TriggerSubscription.objects.create(
            event_type="custom",
            property_filter={},
            target_url="https://handler.example.com/a",
            secret="s",
        )
        with patch("triggers.dispatch.async_task") as mock_task:
            first, created1 = emit_event(
                "v0_workshop", self.user, {"name": "v0_workshop"},
            )
            second, created2 = emit_event(
                "v0_workshop", self.user, {"name": "v0_workshop"},
            )
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(EventEmission.objects.count(), 1)
        # Only the first emit dispatched; the dedup no-op did not.
        dispatched_for_sub = [
            call for call in mock_task.call_args_list if call.args[2] == sub.id
        ]
        self.assertEqual(len(dispatched_for_sub), 1)
