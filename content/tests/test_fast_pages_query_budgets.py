"""Query-budget regression tests for fast public pages (issue #2014).

Each key public page must keep a constant query count as catalog volume
grows (no N+1), and anonymous responses must be served from the
per-view cache on repeat hits.
"""

from datetime import date, timedelta

from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from content.models import Article, Course, Workshop
from tests.fixtures import TierSetupMixin


def _make_article(slug, tags=None):
    """Create one published blog article."""
    return Article.objects.create(
        title=slug,
        slug=slug,
        date=date(2025, 1, 1),
        published=True,
        page_type="blog",
        tags=tags or [],
    )


def _make_workshop(slug):
    """Create one published workshop without an event link."""
    return Workshop.objects.create(
        slug=slug,
        title=slug,
        status="published",
        date=date(2026, 4, 21),
        tags=[],
    )


def _measured_get(client, url):
    """GET url with a cold anon cache, returning the query count."""
    cache.clear()
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(url)
    return response, len(ctx.captured_queries)


class BlogListQueryBudgetTest(TierSetupMixin, TestCase):
    """`/blog` must not issue more queries as article volume grows."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        for i in range(3):
            _make_article(f"blog-budget-{i}")

    def test_query_count_does_not_grow_with_article_count(self):
        self.client.get("/blog")
        _, small_count = _measured_get(self.client, "/blog")
        for i in range(3, 9):
            _make_article(f"blog-budget-{i}")
        response, large_count = _measured_get(self.client, "/blog")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            small_count,
            large_count,
            msg=(f"blog_list query count grew with article volume ({small_count} -> {large_count})"),
        )


@override_settings(PUBLIC_PAGE_CACHE_ENABLED=True)
class BlogDetailQueryBudgetTest(TierSetupMixin, TestCase):
    """`/blog/<slug>` rail must be cached: a repeat hit costs no rail queries."""

    def setUp(self):
        super().setUp()
        cache.clear()

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.article = _make_article("rail-budget-main", tags=["python"])
        for i in range(4):
            _make_article(f"rail-budget-other-{i}", tags=["python"])

    def test_repeat_hit_serves_rail_from_cache(self):
        first, first_count = _measured_get(self.client, "/blog/rail-budget-main")
        self.assertEqual(first.status_code, 200)
        with CaptureQueriesContext(connection) as ctx:
            second = self.client.get("/blog/rail-budget-main")
        self.assertEqual(second.status_code, 200)
        self.assertLess(
            len(ctx.captured_queries),
            first_count,
            msg="repeat blog_detail hit should skip rail queries via cache",
        )


class HomeAnonQueryBudgetTest(TierSetupMixin, TestCase):
    """`/` anonymous render must stay constant as content grows."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        _make_article("home-budget-0")
        _make_workshop("home-budget-ws-0")

    def test_query_count_does_not_grow_with_content(self):
        self.client.get("/")
        _, small_count = _measured_get(self.client, "/")
        _make_article("home-budget-1")
        _make_article("home-budget-2")
        _make_workshop("home-budget-ws-1")
        response, large_count = _measured_get(self.client, "/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            small_count,
            large_count,
            msg=(f"homepage query count grew with content volume ({small_count} -> {large_count})"),
        )


class CoursesListQueryBudgetTest(TierSetupMixin, TestCase):
    """`/courses` must stay constant as course volume grows."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        for i in range(2):
            Course.objects.create(
                title=f"Budget Course {i}",
                slug=f"budget-course-{i}",
                status="published",
            )

    def test_query_count_does_not_grow_with_course_count(self):
        self.client.get("/courses")
        _, small_count = _measured_get(self.client, "/courses")
        for i in range(2, 6):
            Course.objects.create(
                title=f"Budget Course {i}",
                slug=f"budget-course-{i}",
                status="published",
            )
        response, large_count = _measured_get(self.client, "/courses")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            small_count,
            large_count,
            msg=(f"courses_list query count grew with course volume ({small_count} -> {large_count})"),
        )


class WorkshopsListQueryBudgetTest(TierSetupMixin, TestCase):
    """`/workshops` must stay constant as workshop volume grows."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        for i in range(2):
            _make_workshop(f"ws-budget-{i}")

    def test_query_count_does_not_grow_with_workshop_count(self):
        self.client.get("/workshops")
        _, small_count = _measured_get(self.client, "/workshops")
        for i in range(2, 6):
            _make_workshop(f"ws-budget-{i}")
        response, large_count = _measured_get(self.client, "/workshops")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            small_count,
            large_count,
            msg=(f"workshops_list query count grew with workshop volume ({small_count} -> {large_count})"),
        )


@override_settings(PUBLIC_PAGE_CACHE_ENABLED=True)
class AnonPageCacheTest(TierSetupMixin, TestCase):
    """Anonymous public responses are cached; logged-in pages are not."""

    def setUp(self):
        super().setUp()
        cache.clear()

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        _make_article("cache-budget-first")

    def test_anonymous_blog_list_is_cached(self):
        cache.clear()
        first = self.client.get("/blog")
        self.assertEqual(first.status_code, 200)
        self.assertContains(first, "cache-budget-first")
        _make_article("cache-budget-second")
        second = self.client.get("/blog")
        self.assertEqual(second.status_code, 200)
        self.assertNotContains(second, "cache-budget-second")
        cache.clear()
        third = self.client.get("/blog")
        self.assertContains(third, "cache-budget-second")

    def test_authenticated_responses_bypass_anon_cache(self):
        from django.contrib.auth import get_user_model

        cache.clear()
        anon = self.client.get("/blog")
        self.assertEqual(anon.status_code, 200)
        self.assertContains(anon, "cache-budget-first")
        user = get_user_model().objects.create_user(
            email="cache-budget@test.com",
            password="pw",
            email_verified=True,
        )
        _make_article("cache-budget-second")
        self.client.force_login(user)
        authed = self.client.get("/blog")
        self.assertEqual(authed.status_code, 200)
        self.assertContains(
            authed,
            "cache-budget-second",
            msg_prefix="authenticated hits must bypass the anon page cache",
        )


class FaqQueryBudgetTest(TierSetupMixin, TestCase):
    """`/faq` renders with a small bounded query count."""

    def test_faq_stays_within_query_budget(self):
        _, count = _measured_get(self.client, "/faq")
        self.assertLessEqual(
            count,
            12,
            msg=f"/faq took {count} queries (expected <= 12)",
        )


@override_settings(PUBLIC_PAGE_CACHE_ENABLED=True)
class EventsListQueryBudgetTest(TierSetupMixin, TestCase):
    """`/events` anonymous render stays within budget and is cached."""

    def setUp(self):
        super().setUp()
        cache.clear()

    def test_anonymous_events_list_is_cached(self):
        cache.clear()
        first = self.client.get("/events")
        self.assertIn(first.status_code, (200, 302))
        with CaptureQueriesContext(connection) as ctx:
            second = self.client.get("/events")
        self.assertIn(second.status_code, (200, 302))
        self.assertLessEqual(
            len(ctx.captured_queries),
            3,
            msg="repeat /events hit should come from the anon cache",
        )


class WorkshopDetailQueryBudgetTest(TierSetupMixin, TestCase):
    """Workshop detail resolves its event without an extra lazy query."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = _make_workshop("ws-detail-budget")

    def test_event_is_fetched_with_workshop(self):
        from django.test.utils import CaptureQueriesContext

        cache.clear()
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get("/workshops/ws-detail-budget")
        self.assertEqual(response.status_code, 200)
        event_queries = [q for q in ctx.captured_queries if "events_event" in q["sql"]]
        self.assertLessEqual(
            len(event_queries),
            1,
            msg="workshop detail must not lazy-load the event per render",
        )


@override_settings(PUBLIC_PAGE_CACHE_ENABLED=True)
class RelatedRailCacheTest(TierSetupMixin, TestCase):
    """The related-content rail is cached per object."""

    def setUp(self):
        super().setUp()
        cache.clear()

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.article = _make_article("related-cache-main", tags=["ai"])
        _make_article("related-cache-other", tags=["ai"])

    def test_second_rail_build_hits_cache(self):
        from content.services.related_content import (
            build_related_content_rail,
        )

        cache.clear()
        with CaptureQueriesContext(connection) as first_ctx:
            build_related_content_rail(self.article)
        first_count = len(first_ctx.captured_queries)
        self.assertGreater(first_count, 0)
        with CaptureQueriesContext(connection) as second_ctx:
            build_related_content_rail(self.article)
        self.assertEqual(
            len(second_ctx.captured_queries),
            0,
            msg="second rail build for the same object must hit the cache",
        )


class PublicIndexCoverageTest(TierSetupMixin, TestCase):
    """Public-filter indexes exist on the hot list models."""

    def test_hot_public_indexes_are_declared(self):
        from django.apps import apps

        expectations = {
            "Article": ["published", "page_type"],
            "Workshop": ["status"],
            "Tutorial": ["published"],
            "Project": ["published", "status"],
            "CuratedLink": ["published"],
            "Download": ["published"],
            "MarketingPage": ["status"],
        }
        for model_name, fields in expectations.items():
            model = apps.get_model("content", model_name)
            indexed = set()
            for index in model._meta.indexes:
                indexed.update(index.fields)
            for field in fields:
                self.assertIn(
                    field,
                    indexed,
                    msg=f"{model_name} is missing a public-filter index on {field}",
                )


class OAuthCallbackBudgetTest(TierSetupMixin, TestCase):
    """The OAuth login entry point stays cheap (no provider round-trip)."""

    def test_login_page_renders_within_query_budget(self):
        _, count = _measured_get(self.client, "/accounts/login/")
        self.assertLessEqual(
            count,
            15,
            msg=f"/accounts/login/ took {count} queries (expected <= 15)",
        )


class PastEventsNPlusOneTest(TierSetupMixin, TestCase):
    """Past events render the series link without per-row queries."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from events.models import Event, EventSeries

        now = timezone.now()
        cls.series = EventSeries.objects.create(
            name="Budget Series",
            slug="budget-series",
        )
        for i in range(4):
            Event.objects.create(
                title=f"Budget Past {i}",
                slug=f"budget-past-{i}",
                event_series=cls.series,
                start_datetime=now - timedelta(days=30 + i),
                end_datetime=now - timedelta(days=30 + i, hours=-1),
                status="completed",
                published=True,
            )

    def test_past_list_query_count_is_constant(self):
        self.client.get("/events?filter=past")
        _, small_count = _measured_get(self.client, "/events?filter=past")
        from events.models import Event

        now = timezone.now()
        for i in range(4, 8):
            Event.objects.create(
                title=f"Budget Past {i}",
                slug=f"budget-past-{i}",
                event_series=self.series,
                start_datetime=now - timedelta(days=60 + i),
                end_datetime=now - timedelta(days=60 + i, hours=-1),
                status="completed",
                published=True,
            )
        response, large_count = _measured_get(self.client, "/events?filter=past")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            small_count,
            large_count,
            msg=(f"past events query count grew with row volume ({small_count} -> {large_count})"),
        )
