"""Tests for ``POST /api/integrations/slack/plan-sprints/ingest`` (issue #904).

The Slack ingest itself is enqueued on the worker; these tests patch
``async_task`` at the view boundary so the endpoint's auth, validation,
and argument forwarding are exercised without running the real task (and
without hitting Slack).
"""

import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from accounts.models import Token

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

    def test_get_not_allowed(self):
        response = self.client.get(
            URL, HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )
        self.assertEqual(response.status_code, 405)


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
