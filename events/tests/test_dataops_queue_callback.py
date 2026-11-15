"""AISL -> DataOps ad-hoc queue callback on event create/change/cancel."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from events.models import Event
from events.services import dataops_queue

QUEUE = {"DATAOPS_QUEUE_URL": "https://dataops.test/api/v1/queue/items", "DATAOPS_QUEUE_TOKEN": "synthetic-token"}


@override_settings(**QUEUE)
class DataOpsQueueCallbackTest(TestCase):
    def _create(self, **extra):
        values = {
            "title": "Evals in CI",
            "slug": "evals-in-ci-dataops-test",
            "start_datetime": timezone.now() + timedelta(days=3),
            "status": "upcoming",
        }
        values.update(extra)
        return Event.objects.create(**values)

    def _items(self, enqueue):
        return [call.args[0] for call in enqueue.call_args_list]

    def test_create_move_rename_and_cancel_each_post_one_item(self):
        with patch.object(dataops_queue, "enqueue_delivery") as enqueue, self.captureOnCommitCallbacks(execute=True):
            event = self._create()
        with patch.object(dataops_queue, "enqueue_delivery") as move, self.captureOnCommitCallbacks(execute=True):
            event.start_datetime = event.start_datetime + timedelta(days=7)
            event.title = "Evals in CI (moved)"
            event.save()
        with patch.object(dataops_queue, "enqueue_delivery") as cancel, self.captureOnCommitCallbacks(execute=True):
            event.status = "cancelled"
            event.save()

        created = self._items(enqueue)
        self.assertEqual([i["kind"] for i in created], ["event.created"])
        self.assertEqual(created[0]["source"], "aisl")
        self.assertEqual(created[0]["payload"]["eventId"], event.pk)

        moved = self._items(move)
        self.assertEqual([i["kind"] for i in moved], ["event.updated"])
        self.assertEqual(set(moved[0]["payload"]["changes"]), {"start_datetime", "title"})
        self.assertTrue(any(link["label"] == "Event page" for link in moved[0]["links"]))

        self.assertEqual([i["kind"] for i in self._items(cancel)], ["event.cancelled"])

    def test_untracked_change_posts_nothing(self):
        event = self._create()
        with patch.object(dataops_queue, "enqueue_delivery") as enqueue, self.captureOnCommitCallbacks(execute=True):
            event.save()
        enqueue.assert_not_called()

    def test_same_change_has_stable_idempotency_key(self):
        event = self._create()
        before = dataops_queue.snapshot(event)
        event.title = "Renamed"
        first = dataops_queue.build_item(event, before, created=False)
        second = dataops_queue.build_item(event, before, created=False)
        self.assertEqual(first["idempotencyKey"], second["idempotencyKey"])

    def test_enqueue_failure_never_breaks_the_save_and_alerts(self):
        with patch("jobs.tasks.async_task", side_effect=RuntimeError("queue down")), \
                patch.object(dataops_queue, "alert_failure") as alert, \
                self.captureOnCommitCallbacks(execute=True):
            event = self._create()
        self.assertTrue(Event.objects.filter(pk=event.pk).exists())
        self.assertEqual(alert.call_count, 1)

    @override_settings(DATAOPS_QUEUE_URL="", DATAOPS_QUEUE_TOKEN="")
    def test_unconfigured_environment_skips(self):
        with patch.object(dataops_queue, "enqueue_delivery") as enqueue, self.captureOnCommitCallbacks(execute=True):
            self._create(slug="evals-unconfigured")
        enqueue.assert_not_called()


@override_settings(**QUEUE)
class DataOpsQueueDeliveryTest(TestCase):
    item = {"source": "aisl", "kind": "event.updated", "title": "t", "payload": {"eventId": 1}, "links": [], "idempotencyKey": "k"}

    def test_delivery_posts_with_bearer_token(self):
        ok = MagicMock(status_code=201)
        with patch.object(dataops_queue.requests, "post", return_value=ok) as post:
            self.assertTrue(dataops_queue.deliver(self.item, sleep=lambda _s: None))
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer synthetic-token")
        self.assertEqual(post.call_args.args[0], QUEUE["DATAOPS_QUEUE_URL"])

    def test_transient_failure_is_retried_then_succeeds(self):
        responses = [MagicMock(status_code=503, text="down"), MagicMock(status_code=201)]
        with patch.object(dataops_queue.requests, "post", side_effect=responses) as post, \
                patch.object(dataops_queue, "alert_failure") as alert:
            self.assertTrue(dataops_queue.deliver(self.item, sleep=lambda _s: None))
        self.assertEqual(post.call_count, 2)
        alert.assert_not_called()

    def test_persistent_failure_alerts_loudly_and_raises(self):
        with patch.object(dataops_queue.requests, "post", return_value=MagicMock(status_code=503, text="down")) as post, \
                patch.object(dataops_queue, "alert_failure") as alert:
            with self.assertRaises(RuntimeError):
                dataops_queue.deliver(self.item, sleep=lambda _s: None)
        self.assertEqual(post.call_count, 1 + len(dataops_queue.RETRY_DELAYS_SECONDS))
        self.assertEqual(alert.call_count, 1)

    def test_client_error_is_not_retried(self):
        with patch.object(dataops_queue.requests, "post", return_value=MagicMock(status_code=401, text="bad token")) as post, \
                patch.object(dataops_queue, "alert_failure") as alert:
            with self.assertRaises(RuntimeError):
                dataops_queue.deliver(self.item, sleep=lambda _s: None)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(alert.call_count, 1)


class DataOpsQueueSettingsTest(TestCase):
    def test_isolated_dev_runtime_never_posts_to_production_queue(self):
        from website.settings import _dataops_queue_setting

        env = {"DATAOPS_QUEUE_URL": "https://dataops.test/api/v1/queue/items"}
        self.assertEqual(_dataops_queue_setting("DATAOPS_QUEUE_URL", env), env["DATAOPS_QUEUE_URL"])
        self.assertEqual(_dataops_queue_setting("DATAOPS_QUEUE_URL", {**env, "AISL_DATABASE_NAME": "aisl_dev"}), "")
