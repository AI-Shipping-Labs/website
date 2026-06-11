"""Tests for ``/api/integrations/slack/plan-sprints/ingest`` (issues #904, #925).

POST triggers a worker ingest; GET (issue #925) lists recent
``SlackChannelIngest`` runs with counts. The POST tests patch
``async_task`` at the view boundary so the endpoint's auth, validation,
and argument forwarding are exercised without running the real task (and
without hitting Slack). The GET tests seed ``SlackChannelIngest`` rows
directly and assert on the read-only JSON contract.
"""

import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import Token
from crm.models.slack_update import SlackChannelIngest

User = get_user_model()

URL = "/api/integrations/slack/plan-sprints/ingest"

SLACK_ON = dict(
    SLACK_ENABLED=True,
    SLACK_BOT_TOKEN="xoxb-test",
    SLACK_ENVIRONMENT="test",
    SLACK_TEST_PLAN_SPRINTS_CHANNEL_ID="C_TEST_PLANSPRINTS",
)


@override_settings(**SLACK_ON)
class PlanSprintsIngestApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email="admin@test.com", password="x", is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.admin, name="test")

    def _post(self, payload=None, *, auth=True):
        kwargs = {"content_type": "application/json"}
        if payload is not None:
            kwargs["data"] = json.dumps(payload)
        if auth:
            kwargs["HTTP_AUTHORIZATION"] = f"Token {self.token.key}"
        return self.client.post(URL, **kwargs)

    def test_requires_token(self):
        response = self._post({"since": "2026-01-01"}, auth=False)
        self.assertEqual(response.status_code, 401)

    def test_trigger_enqueues_with_since_and_dry_run(self):
        with mock.patch(
            "api.views.plan_sprints_ingest.async_task", return_value="task-123",
        ) as enqueue:
            response = self._post({"since": "2026-01-01", "dry_run": True})
        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(body["status"], "queued")
        self.assertEqual(body["task_id"], "task-123")
        self.assertEqual(body["since"], "2026-01-01")
        self.assertTrue(body["dry_run"])
        # The task was enqueued with the parsed since date + dry_run flag.
        _args, kwargs = enqueue.call_args
        self.assertEqual(kwargs["since"].isoformat(), "2026-01-01")
        self.assertTrue(kwargs["dry_run"])

    def test_trigger_with_empty_body_uses_watermark_defaults(self):
        with mock.patch(
            "api.views.plan_sprints_ingest.async_task", return_value="t1",
        ) as enqueue:
            response = self._post()
        self.assertEqual(response.status_code, 202)
        _args, kwargs = enqueue.call_args
        self.assertIsNone(kwargs["since"])
        self.assertFalse(kwargs["dry_run"])

    def test_invalid_since_returns_400(self):
        with mock.patch(
            "api.views.plan_sprints_ingest.async_task",
        ) as enqueue:
            response = self._post({"since": "nope"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_since")
        enqueue.assert_not_called()

    def test_future_since_returns_400(self):
        with mock.patch("api.views.plan_sprints_ingest.async_task") as enqueue:
            response = self._post({"since": "2999-01-01"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_since")
        enqueue.assert_not_called()

    def test_non_boolean_dry_run_returns_400(self):
        with mock.patch("api.views.plan_sprints_ingest.async_task") as enqueue:
            response = self._post({"dry_run": "yes"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_dry_run")
        enqueue.assert_not_called()

    def test_put_not_allowed(self):
        # GET + POST are the only methods; PUT still 405s.
        response = self.client.put(
            URL, HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )
        self.assertEqual(response.status_code, 405)


class PlanSprintsIngestListApiTest(TestCase):
    """GET ``/api/integrations/slack/plan-sprints/ingest`` (issue #925)."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email="reader@test.com", password="x", is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.admin, name="test")

    def _get(self, query="", *, auth=True):
        kwargs = {}
        if auth:
            kwargs["HTTP_AUTHORIZATION"] = f"Token {self.token.key}"
        return self.client.get(f"{URL}{query}", **kwargs)

    def test_requires_token(self):
        SlackChannelIngest.objects.create(channel_id="C1", status="success")
        response = self._get(auth=False)
        self.assertEqual(response.status_code, 401)

    def test_empty_returns_empty_list(self):
        response = self._get()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["runs"], [])
        self.assertEqual(body["count"], 0)
        self.assertEqual(body["limit"], 10)

    def test_returns_runs_with_counts(self):
        finished = timezone.now()
        run = SlackChannelIngest.objects.create(
            channel_id="C0123ABC",
            status="success",
            messages_seen=137,
            threads_persisted=24,
            replies_added=9,
            members_matched=21,
            oldest_ts="1748390400.000100",
            latest_ts="1749600000.000200",
            error="",
        )
        SlackChannelIngest.objects.filter(pk=run.pk).update(finished_at=finished)

        response = self._get()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        row = body["runs"][0]
        # Every count field the model stores is surfaced under its real name.
        self.assertEqual(row["id"], run.pk)
        self.assertEqual(row["channel_id"], "C0123ABC")
        self.assertEqual(row["status"], "success")
        self.assertEqual(row["messages_seen"], 137)
        self.assertEqual(row["threads_persisted"], 24)
        self.assertEqual(row["replies_added"], 9)
        self.assertEqual(row["members_matched"], 21)
        self.assertEqual(row["oldest_ts"], "1748390400.000100")
        self.assertEqual(row["latest_ts"], "1749600000.000200")
        self.assertEqual(row["error"], "")
        self.assertIsNotNone(row["started_at"])
        self.assertIsNotNone(row["finished_at"])

    def test_running_run_has_null_finished_at(self):
        SlackChannelIngest.objects.create(channel_id="C1", status="running")
        row = self._get().json()["runs"][0]
        self.assertEqual(row["status"], "running")
        self.assertIsNone(row["finished_at"])

    def test_ordering_is_newest_first(self):
        old = SlackChannelIngest.objects.create(channel_id="OLD")
        new = SlackChannelIngest.objects.create(channel_id="NEW")
        # started_at is auto_now_add; force a deterministic ordering.
        SlackChannelIngest.objects.filter(pk=old.pk).update(
            started_at=timezone.now() - timezone.timedelta(hours=2),
        )
        SlackChannelIngest.objects.filter(pk=new.pk).update(
            started_at=timezone.now(),
        )
        runs = self._get().json()["runs"]
        self.assertEqual([r["id"] for r in runs], [new.pk, old.pk])

    def test_limit_caps_returned_runs(self):
        for i in range(3):
            SlackChannelIngest.objects.create(channel_id=f"C{i}")
        body = self._get("?limit=2").json()
        self.assertEqual(body["count"], 2)
        self.assertEqual(body["limit"], 2)

    def test_default_limit_is_ten(self):
        for i in range(12):
            SlackChannelIngest.objects.create(channel_id=f"C{i}")
        body = self._get().json()
        self.assertEqual(body["count"], 10)
        self.assertEqual(body["limit"], 10)

    def test_invalid_limit_returns_422(self):
        response = self._get("?limit=abc")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "validation_error")

    def test_does_not_expose_slack_message_text(self):
        # The GET contract is counts/metadata only — the serialized keys
        # must not include any message-content field.
        SlackChannelIngest.objects.create(channel_id="C1")
        row = self._get().json()["runs"][0]
        self.assertNotIn("text", row)
        self.assertNotIn("messages", row)


class PlanSprintsIngestUnavailableTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email="admin2@test.com", password="x", is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.admin, name="test")

    def _post(self):
        return self.client.post(
            URL,
            data=json.dumps({"since": "2026-01-01"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )

    @override_settings(SLACK_ENABLED=False)
    def test_slack_disabled_returns_409_without_enqueue(self):
        with mock.patch("api.views.plan_sprints_ingest.async_task") as enqueue:
            response = self._post()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "ingest_unavailable")
        enqueue.assert_not_called()

    @override_settings(
        SLACK_ENABLED=True,
        SLACK_BOT_TOKEN="xoxb-test",
        SLACK_ENVIRONMENT="test",
        SLACK_TEST_PLAN_SPRINTS_CHANNEL_ID="",
    )
    def test_no_channel_returns_409_without_enqueue(self):
        with mock.patch("api.views.plan_sprints_ingest.async_task") as enqueue:
            response = self._post()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "ingest_unavailable")
        enqueue.assert_not_called()
