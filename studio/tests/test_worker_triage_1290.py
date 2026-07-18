"""Worker history triage and entity affordances (issue #1290)."""

import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django_q.models import Task

from content.models import Article, Project
from email_app.models import EmailCampaign
from events.models import Event
from integrations.models import ContentSource
from jobs.task_entities import (
    BANNER_FUNC,
    CAMPAIGN_FUNCS,
    CONTENT_SYNC_FUNC,
    EVENT_FUNCS,
)

User = get_user_model()


def _task(*, name, func="jobs.example", started=None, success=True, args=(), kwargs=None):
    started = started or timezone.now()
    return Task.objects.create(
        id=uuid.uuid4().hex,
        name=name,
        func=func,
        args=args,
        kwargs=kwargs or {},
        started=started,
        stopped=started + timedelta(seconds=1),
        success=success,
        result=None if success else "RuntimeError: failed",
    )


class WorkerHistoryTriageTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="worker-triage@test.com",
            password="pw",
            is_staff=True,
        )

    def setUp(self):
        self.client.force_login(self.staff)
        health = patch("studio.views.worker.get_worker_status")
        self.get_worker_status = health.start()
        self.get_worker_status.return_value = {
            "alive": False,
            "idle": False,
            "last_heartbeat_age": None,
            "cluster_count": 0,
            "clusters": [],
        }
        self.addCleanup(health.stop)

    def test_name_function_status_and_operator_date_filters_compose(self):
        with timezone.override("Europe/Berlin"):
            in_range = timezone.make_aware(datetime(2026, 7, 18, 23, 30))
            target = _task(name="incident-target", started=in_range, success=False)
            _task(name="incident-success", started=in_range, success=True)
            other = _task(name="other", func="ops.incident.target", started=in_range, success=False)
            _task(name="incident-old", started=in_range - timedelta(days=2), success=False)

            response = self.client.get(
                reverse("studio_worker"),
                {
                    "q": "  INCIDENT  ",
                    "status": "failed",
                    "date_from": "2026-07-18",
                    "date_to": "2026-07-18",
                },
            )

        names = [item["task"].name for item in response.context["tasks_with_duration"]]
        expected = [task.name for task in sorted((target, other), key=lambda task: task.id, reverse=True)]
        self.assertEqual(names, expected)
        self.assertEqual(response.context["task_filtered_total"], 2)
        self.assertEqual(response.context["success_count"], 1)
        self.assertEqual(response.context["failure_count"], 3)
        self.assertContains(response, "2 matching tasks; showing 1–2")

    def test_history_pages_by_50_and_preserves_pending_page_and_filters(self):
        same_started = timezone.now()
        for index in range(51):
            _task(name=f"needle-{index:02d}", started=same_started)
        response = self.client.get(
            reverse("studio_worker"),
            {"q": "needle", "status": "success", "pending_page": "3"},
        )
        self.assertEqual(len(response.context["tasks_with_duration"]), 50)
        self.assertEqual(response.context["task_filtered_total"], 51)
        next_url = response.context["task_pager_next_url"]
        self.assertIn("q=needle", next_url)
        self.assertIn("status=success", next_url)
        self.assertIn("pending_page=3", next_url)
        self.assertIn("task_page=2", next_url)
        fragment_url = response.context["pending_fragment_url"]
        self.assertIn("fragment=pending", fragment_url)
        self.assertIn("pending_page=3", fragment_url)
        self.assertNotIn("q=", fragment_url)
        self.assertNotIn("status=", fragment_url)

    def test_invalid_dates_preserve_values_and_show_only_validation(self):
        _task(name="must-not-render")
        response = self.client.get(
            reverse("studio_worker"),
            {"date_from": "2026-07-20", "date_to": "2026-07-18", "pending_page": "2"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["tasks_with_duration"], [])
        self.assertContains(response, "From date must be on or before To date.")
        self.assertContains(response, 'value="2026-07-20"')
        self.assertNotContains(response, "No tasks match these filters")
        self.assertNotContains(response, "No tasks recorded yet")
        self.assertEqual(response.context["task_clear_url"], reverse("studio_worker") + "?pending_page=2")

    def test_malformed_date_uses_visible_correctable_text_fallback(self):
        response = self.client.get(
            reverse("studio_worker"),
            {"date_from": "not-a-date", "date_to": "2026-07-18"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'type="text" name="date_from" value="not-a-date"')
        self.assertContains(response, 'aria-invalid="true"')
        self.assertContains(response, 'aria-describedby="task-filter-error"')
        self.assertContains(response, "Enter dates in YYYY-MM-DD format.")
        self.assertNotContains(response, "From date must be on or before To date.")
        self.assertContains(response, 'type="date" name="date_to" value="2026-07-18"')
        self.assertNotContains(response, 'type="date" name="date_from"')

    def test_mixed_visible_slice_bounds_entity_queries_and_excludes_off_page_models(self):
        now = timezone.now()
        article = Article.objects.create(
            title="Bounded article",
            slug="bounded-article",
            date=timezone.localdate(),
        )
        event = Event.objects.create(
            title="Bounded event",
            slug="bounded-event",
            start_datetime=now,
        )
        campaign = EmailCampaign.objects.create(subject="Bounded campaign", body="Body")
        source = ContentSource.objects.create(repo_name="bounded/source")
        project = Project.objects.create(
            title="Off-page project",
            slug="off-page-project",
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
                name=f"visible-{index:02d}",
                func=func,
                args=args,
                kwargs=kwargs,
                started=now,
            )
        _task(
            name="off-page-project",
            func=BANNER_FUNC,
            args=("project", project.pk),
            started=now - timedelta(days=1),
        )

        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(reverse("studio_worker"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["tasks_with_duration"]), 50)
        sql = [query["sql"].lower() for query in captured.captured_queries]
        for table in (
            "content_article",
            "events_event",
            "email_app_emailcampaign",
            "integrations_contentsource",
        ):
            self.assertEqual(sum(f'from "{table}"' in query for query in sql), 1, table)
        self.assertFalse(any('from "content_project"' in query for query in sql))
        self.assertLessEqual(len(sql), 16, sql)

    def test_invalid_status_defaults_to_all_and_filtered_empty_is_distinct(self):
        _task(name="success-row", success=True)
        response = self.client.get(reverse("studio_worker"), {"status": "obsolete"})
        self.assertEqual(response.context["task_status"], "all")
        self.assertContains(response, "success-row")

        empty = self.client.get(reverse("studio_worker"), {"q": "no-such-task"})
        self.assertContains(empty, "No tasks match these filters")
        self.assertNotContains(empty, "No tasks recorded yet")
        self.assertContains(empty, 'data-testid="studio-empty-state-filter"')
        self.assertContains(empty, "Affected entity")
        self.assertContains(empty, "Clear filters")

    def test_unfiltered_empty_uses_canonical_fresh_studio_owner(self):
        response = self.client.get(reverse("studio_worker"))
        self.assertContains(response, 'data-testid="studio-empty-state-fresh"')
        self.assertContains(response, "No tasks recorded yet.")
        self.assertNotContains(response, 'data-testid="studio-empty-state-filter"')

    def test_available_missing_and_null_entities_render_on_completed_surfaces(self):
        article = Article.objects.create(
            title="A <safe> title",
            slug="safe-title",
            date=timezone.localdate(),
        )
        available_task = _task(
            name="banner-available",
            func=BANNER_FUNC,
            args=("article", article.pk),
            success=False,
        )
        missing_task = _task(
            name="banner-missing",
            func=BANNER_FUNC,
            args=("article", article.pk + 1000),
        )
        _task(name="unsupported", func="near." + BANNER_FUNC)

        response = self.client.get(reverse("studio_worker"))
        self.assertContains(response, f"Article #{article.pk} — A &lt;safe&gt; title")
        self.assertContains(response, f"Article {article.pk + 1000} (not found)")
        self.assertContains(response, "No recognized affected entity")
        entity_url = reverse("studio_article_edit", kwargs={"article_id": article.pk})
        self.assertContains(response, entity_url)

        detail = self.client.get(reverse("studio_worker_task_detail", args=[available_task.id]))
        self.assertContains(detail, entity_url)
        self.assertContains(detail, "A &lt;safe&gt; title")
        missing_detail = self.client.get(reverse("studio_worker_task_detail", args=[missing_task.id]))
        self.assertNotContains(missing_detail, entity_url)
