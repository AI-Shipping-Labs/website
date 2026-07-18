"""Additive Worker API triage contract (issue #1290)."""

import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django_q.models import Task

from accounts.models import Token
from api.openapi import build_spec
from api.urls import urlpatterns
from content.models import Article, Project
from email_app.models import EmailCampaign
from events.models import Event
from integrations.models import ContentSource
from jobs.task_entities import BANNER_FUNC, CAMPAIGN_FUNCS, CONTENT_SYNC_FUNC, EVENT_FUNCS

User = get_user_model()


def _task(
    *,
    name,
    func="jobs.example",
    started=None,
    success=True,
    group="ops",
    args=(),
    kwargs=None,
):
    started = started or timezone.now()
    return Task.objects.create(
        id=uuid.uuid4().hex,
        name=name,
        func=func,
        group=group,
        args=args,
        kwargs=kwargs or {},
        started=started,
        stopped=started + timedelta(seconds=2),
        success=success,
        result=None if success else "RuntimeError: failed",
    )


class WorkerTaskTriageApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="worker-api-1290@test.com",
            password="pw",
            is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name="worker-1290")

    def _get(self, path, params=None):
        return self.client.get(
            path,
            params or {},
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )

    def test_collection_composes_filters_then_applies_offset_and_limit(self):
        now = timezone.now()
        _task(name="incident-three", started=now, success=False)
        _task(name="other", func="ops.incident.worker", started=now - timedelta(minutes=1), success=False)
        _task(name="incident-success", started=now - timedelta(minutes=2), success=True)
        _task(name="incident-wrong-group", started=now - timedelta(minutes=3), success=False, group="other")

        response = self._get(
            "/api/worker/tasks",
            {
                "q": "  INCIDENT ",
                "status": "failed",
                "group": "ops",
                "date_from": timezone.localdate().isoformat(),
                "date_to": timezone.localdate().isoformat(),
                "offset": "1",
                "limit": "1",
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["total_count"], 2)
        self.assertEqual(data["offset"], 1)
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["limit"], 1)
        self.assertEqual(data["tasks"][0]["name"], "other")
        self.assertIn("affected_entity", data["tasks"][0])
        self.assertIsNone(data["tasks"][0]["affected_entity"])

    def test_new_collection_validation_uses_422_envelope_and_field(self):
        cases = (
            ({"offset": "-1"}, "offset"),
            ({"offset": "abc"}, "offset"),
            ({"date_from": "not-a-date"}, "date_from"),
            ({"date_from": "2026-07-20", "date_to": "2026-07-18"}, "date_from"),
            ({"status": "queued"}, "status"),
        )
        for params, field in cases:
            with self.subTest(params=params):
                response = self._get("/api/worker/tasks", params)
                self.assertEqual(response.status_code, 422)
                data = response.json()
                self.assertEqual(data["code"], "validation_error")
                self.assertEqual(data["details"]["field"], field)

    def test_available_entity_is_shared_by_collection_failed_and_detail(self):
        article = Article.objects.create(
            title="API entity",
            slug="api-entity",
            date=timezone.localdate(),
        )
        task = _task(
            name="banner-failure",
            func=BANNER_FUNC,
            args=("article", article.pk),
            success=False,
        )
        paths = (
            "/api/worker/tasks",
            "/api/worker/tasks/failed",
            f"/api/worker/tasks/{task.id}",
        )
        for path in paths:
            with self.subTest(path=path):
                payload = self._get(path).json()
                row = payload["tasks"][0] if "tasks" in payload else payload
                self.assertEqual(
                    row["affected_entity"],
                    {
                        "kind": "article",
                        "id": article.pk,
                        "label": f"Article #{article.pk} — API entity",
                        "state": "available",
                        "studio_url": f"/studio/articles/{article.pk}/edit",
                    },
                )

    def test_deleted_and_unsupported_entities_are_safe_and_reads_do_not_write(self):
        article = Article.objects.create(
            title="Delete me",
            slug="delete-me",
            date=timezone.localdate(),
        )
        article_id = article.pk
        _task(name="missing", func=BANNER_FUNC, args=("article", article_id))
        _task(name="unsupported", func=BANNER_FUNC + ".near", args=("article", article_id))
        article.delete()
        before = Task.objects.count()
        data = self._get("/api/worker/tasks", {"limit": 2}).json()
        by_name = {row["name"]: row for row in data["tasks"]}
        self.assertEqual(by_name["missing"]["affected_entity"]["state"], "missing")
        self.assertIsNone(by_name["missing"]["affected_entity"]["studio_url"])
        self.assertIsNone(by_name["unsupported"]["affected_entity"])
        self.assertEqual(Task.objects.count(), before)

    def test_mixed_50_row_slice_bounds_queries_and_excludes_off_slice_model(self):
        now = timezone.now()
        article = Article.objects.create(
            title="API bounded article",
            slug="api-bounded-article",
            date=timezone.localdate(),
        )
        event = Event.objects.create(
            title="API bounded event",
            slug="api-bounded-event",
            start_datetime=now,
        )
        campaign = EmailCampaign.objects.create(subject="API bounded campaign", body="Body")
        source = ContentSource.objects.create(repo_name="api/bounded-source")
        project = Project.objects.create(
            title="API off-slice project",
            slug="api-off-slice-project",
            date=timezone.localdate(),
        )
        visible_specs = (
            (BANNER_FUNC, ("article", article.pk), {}),
            (next(iter(EVENT_FUNCS)), (event.pk, 99), {}),
            (next(iter(CAMPAIGN_FUNCS)), (), {"campaign_id": campaign.pk}),
            (CONTENT_SYNC_FUNC, (source,), {}),
            ("jobs.unsupported.visible", (), {}),
        )
        for index in range(50):
            func, args, kwargs = visible_specs[index % len(visible_specs)]
            _task(
                name=f"api-visible-{index:02d}",
                func=func,
                args=args,
                kwargs=kwargs,
                started=now,
            )
        _task(
            name="api-off-slice-project",
            func=BANNER_FUNC,
            args=("project", project.pk),
            started=now - timedelta(days=1),
        )

        with CaptureQueriesContext(connection) as captured:
            response = self._get("/api/worker/tasks", {"limit": 50})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 50)
        self.assertEqual(response.json()["total_count"], 51)
        sql = [query["sql"].lower() for query in captured.captured_queries]
        for table in (
            "content_article",
            "events_event",
            "email_app_emailcampaign",
            "integrations_contentsource",
        ):
            self.assertEqual(sum(f'from "{table}"' in query for query in sql), 1, table)
        self.assertFalse(any('from "content_project"' in query for query in sql))
        self.assertLessEqual(len(sql), 9, sql)

    def test_generated_openapi_pins_filters_pagination_and_affected_entity_states(self):
        operation = build_spec(urlpatterns)["paths"]["/api/worker/tasks"]["get"]
        parameters = {item["name"]: item["schema"] for item in operation["parameters"]}
        self.assertEqual(
            set(parameters),
            {"status", "group", "limit", "since", "q", "date_from", "date_to", "offset"},
        )
        response = operation["responses"]["200"]["content"]["application/json"]
        schema = response["schema"]
        self.assertEqual(
            schema["required"],
            ["tasks", "count", "limit", "total_count", "offset"],
        )
        affected = schema["properties"]["tasks"]["items"]["properties"]["affected_entity"]
        object_shape = affected["oneOf"][0]
        self.assertEqual(
            object_shape["required"],
            ["kind", "id", "label", "state", "studio_url"],
        )
        self.assertEqual(object_shape["properties"]["state"]["enum"], ["available", "missing"])
        self.assertEqual(
            object_shape["properties"]["id"]["oneOf"],
            [{"type": "integer", "minimum": 1}, {"type": "string", "format": "uuid"}],
        )
        examples = response["example"]["tasks"]
        self.assertEqual([row["affected_entity"]["state"] for row in examples], ["available", "missing"])
        self.assertIsInstance(examples[0]["affected_entity"]["id"], int)
        self.assertIsInstance(examples[1]["affected_entity"]["id"], str)
        self.assertIsNone(examples[1]["affected_entity"]["studio_url"])
        self.assertEqual(
            examples[0]["function"],
            "integrations.services.banner_generator.tasks.render_banner_for_content",
        )
        self.assertEqual(examples[0]["affected_entity"]["kind"], "article")
        self.assertEqual(
            examples[1]["function"],
            "integrations.services.github.sync_content_source",
        )
        self.assertEqual(examples[1]["affected_entity"]["kind"], "content_source")
