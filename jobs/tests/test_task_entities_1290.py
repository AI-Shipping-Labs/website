"""Affected-entity resolver coverage for Worker triage (issue #1290)."""

from datetime import time
from types import SimpleNamespace
from uuid import uuid4

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from content.models import Article, Course, Download, Project, Workshop
from email_app.models import EmailCampaign
from events.models import Event, EventSeries
from integrations.models import ContentSource
from jobs.task_entities import (
    BANNER_FUNC,
    CAMPAIGN_FUNCS,
    CONTENT_SYNC_FUNC,
    EVENT_FUNCS,
    _kind_config,
    resolve_task_affected_entity,
    resolve_tasks_affected_entities,
)


def _task(func, *, args=(), kwargs=None, task_id="task"):
    return SimpleNamespace(id=task_id, func=func, args=args, kwargs=kwargs or {})


class TaskAffectedEntityResolverTest(TestCase):
    def test_every_banner_slug_uses_the_named_canonical_studio_route(self):
        expected = {
            "article": "studio_article_edit",
            "course": "studio_course_edit",
            "project": "studio_project_review",
            "download": "studio_download_edit",
            "workshop": "studio_workshop_detail",
            "event": "studio_event_edit",
            "event_series": "studio_event_series_detail",
        }
        self.assertEqual({kind: _kind_config(kind)[3] for kind in expected}, expected)

    def test_every_banner_kind_resolves_through_the_public_resolver(self):
        today = timezone.localdate()
        event = Event.objects.create(
            title="Resolver event",
            slug="resolver-event",
            start_datetime=timezone.now(),
        )
        series = EventSeries.objects.create(
            name="Resolver series",
            slug="resolver-series",
            start_time=time(10, 0),
        )
        entities = {
            "article": Article.objects.create(title="Resolver article", slug="resolver-article", date=today),
            "course": Course.objects.create(title="Resolver course", slug="resolver-course"),
            "project": Project.objects.create(title="Resolver project", slug="resolver-project", date=today),
            "download": Download.objects.create(
                title="Resolver download",
                slug="resolver-download",
                file_url="https://example.com/resolver.pdf",
            ),
            "workshop": Workshop.objects.create(
                title="Resolver workshop",
                slug="resolver-workshop",
                date=today,
            ),
            "event": event,
            "event_series": series,
        }
        expected_routes = {
            "article": ("studio_article_edit", "article_id"),
            "course": ("studio_course_edit", "course_id"),
            "project": ("studio_project_review", "project_id"),
            "download": ("studio_download_edit", "download_id"),
            "workshop": ("studio_workshop_detail", "workshop_id"),
            "event": ("studio_event_edit", "event_id"),
            "event_series": ("studio_event_series_detail", "series_id"),
        }
        for kind, entity in entities.items():
            with self.subTest(kind=kind):
                resolved = resolve_task_affected_entity(
                    _task(BANNER_FUNC, args=(kind, entity.pk))
                )
                self.assertEqual(resolved["state"], "available")
                self.assertEqual(resolved["kind"], kind)
                route, kwarg = expected_routes[kind]
                self.assertEqual(
                    resolved["studio_url"],
                    reverse(route, kwargs={kwarg: entity.pk}),
                )

    def test_banner_existing_missing_and_unsupported_near_match(self):
        article = Article.objects.create(
            title="Shipping <agents>",
            slug="shipping-agents",
            date=timezone.localdate(),
        )
        available = resolve_task_affected_entity(_task(BANNER_FUNC, args=("article", article.pk)))
        self.assertEqual(available["state"], "available")
        self.assertEqual(available["label"], f"Article #{article.pk} — Shipping <agents>")
        self.assertEqual(
            available["studio_url"],
            reverse("studio_article_edit", kwargs={"article_id": article.pk}),
        )

        article_id = article.pk
        article.delete()
        missing = resolve_task_affected_entity(_task(BANNER_FUNC, args=("article", article_id)))
        self.assertEqual(missing["state"], "missing")
        self.assertEqual(missing["label"], f"Article {article_id} (not found)")
        self.assertIsNone(missing["studio_url"])
        self.assertIsNone(
            resolve_task_affected_entity(_task(BANNER_FUNC + ".near_match", args=("article", article_id)))
        )

    def test_banner_rejects_malformed_identifiers_and_content_types(self):
        for args in (
            ("article", 0),
            ("article", -1),
            ("article", True),
            ("article", "1"),
            ("not_canonical", 1),
            ("article",),
        ):
            with self.subTest(args=args):
                self.assertIsNone(resolve_task_affected_entity(_task(BANNER_FUNC, args=args)))

    def test_content_source_is_requeried_and_stale_display_text_is_not_trusted(self):
        source_id = uuid4()
        source = ContentSource.objects.create(id=source_id, repo_name="AI-Shipping-Labs/content")
        available = resolve_task_affected_entity(_task(CONTENT_SYNC_FUNC, args=(source,)))
        self.assertEqual(available["id"], str(source_id))
        self.assertEqual(available["label"], "Content source — AI-Shipping-Labs/content")
        self.assertEqual(
            available["studio_url"],
            reverse("studio_sync_dashboard") + f"#content-source-{source_id}",
        )

        source.delete()
        stale = ContentSource(id=source_id, repo_name="private/repo-name-must-not-leak")
        missing = resolve_task_affected_entity(_task(CONTENT_SYNC_FUNC, args=(stale,)))
        self.assertEqual(missing["state"], "missing")
        self.assertEqual(missing["label"], f"Content source {source_id} (not found)")
        self.assertNotIn("private", missing["label"])

    def test_content_source_rejects_wrong_model_type(self):
        self.assertIsNone(resolve_task_affected_entity(_task(CONTENT_SYNC_FUNC, args=(object(),))))

    def test_every_event_function_resolves_only_its_parent_event_id(self):
        event = Event.objects.create(
            title="Notification parent",
            slug="notification-parent",
            start_datetime=timezone.now(),
        )
        for func in EVENT_FUNCS:
            with self.subTest(func=func):
                result = resolve_task_affected_entity(_task(func, args=(event.pk, 123)))
                self.assertEqual(result["kind"], "event")
                self.assertEqual(result["id"], event.pk)
                self.assertEqual(result["state"], "available")
                self.assertEqual(
                    result["studio_url"],
                    reverse("studio_event_edit", kwargs={"event_id": event.pk}),
                )

    def test_event_and_campaign_functions_reject_every_invalid_typed_identifier(self):
        invalid_ids = (0, -1, True, "1", None)
        for func in EVENT_FUNCS:
            for identifier in invalid_ids:
                with self.subTest(func=func, identifier=identifier):
                    self.assertIsNone(resolve_task_affected_entity(_task(func, args=(identifier, 123))))
            with self.subTest(func=func, identifier="missing"):
                self.assertIsNone(resolve_task_affected_entity(_task(func)))

        for func in CAMPAIGN_FUNCS:
            for identifier in invalid_ids:
                with self.subTest(func=func, source="positional", identifier=identifier):
                    self.assertIsNone(resolve_task_affected_entity(_task(func, args=(identifier,))))
                with self.subTest(func=func, source="keyword", identifier=identifier):
                    self.assertIsNone(
                        resolve_task_affected_entity(_task(func, kwargs={"campaign_id": identifier}))
                    )
            with self.subTest(func=func, source="missing"):
                self.assertIsNone(resolve_task_affected_entity(_task(func)))

    def test_campaign_keyword_positional_compatibility_and_conflict(self):
        campaign = EmailCampaign.objects.create(subject="Incident update", body="Body")
        for func in CAMPAIGN_FUNCS:
            with self.subTest(func=func, source="keyword"):
                result = resolve_task_affected_entity(
                    _task(func, kwargs={"campaign_id": campaign.pk, "user_ids": [99]})
                )
                self.assertEqual(result["studio_url"], reverse("studio_campaign_detail", args=[campaign.pk]))
            with self.subTest(func=func, source="legacy"):
                result = resolve_task_affected_entity(_task(func, args=(campaign.pk, [99])))
                self.assertEqual(result["kind"], "campaign")
            with self.subTest(func=func, source="conflict"):
                self.assertIsNone(
                    resolve_task_affected_entity(
                        _task(func, args=(campaign.pk,), kwargs={"campaign_id": campaign.pk + 1})
                    )
                )

    def test_malformed_payload_property_never_raises_or_logs_payload(self):
        class BrokenTask:
            id = "broken"
            func = BANNER_FUNC

            @property
            def args(self):
                raise ValueError("SECRET-PAYLOAD")

            kwargs = {}

        with self.assertLogs("jobs.task_entities", level="WARNING") as captured:
            self.assertIsNone(resolve_task_affected_entity(BrokenTask()))
        self.assertNotIn("SECRET-PAYLOAD", " ".join(captured.output))

    def test_bulk_resolution_deduplicates_identical_model_lookups(self):
        article = Article.objects.create(
            title="One lookup",
            slug="one-lookup",
            date=timezone.localdate(),
        )
        tasks = [_task(BANNER_FUNC, args=("article", article.pk), task_id=str(index)) for index in range(50)]
        with self.assertNumQueries(1):
            resolved = resolve_tasks_affected_entities(tasks)
        self.assertEqual(len(resolved), 50)
        self.assertTrue(all(entity["id"] == article.pk for entity in resolved.values()))
