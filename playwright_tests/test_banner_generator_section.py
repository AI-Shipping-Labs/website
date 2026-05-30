"""Playwright E2E for the banner-generator failure/in-progress hints (issue #790).

Three scenarios on the Studio article edit page (one representative
content type — the other four are covered by Django view tests):

1. Operator sees the failure hint with a clickable "View task" link that
   resolves to the Studio worker-task detail page.
2. Operator sees the in-progress hint and the Regenerate control renders
   as the disabled-inflight variant rather than the enabled button.
3. Anonymous visitor cannot reach the Studio edit page at all (preserves
   the existing access gate shipped in #788).

Per-state DOM matrix and helper-function coverage live in
``studio/tests/test_banner_generator_section.py`` — Playwright is for the
click-through and the negative access check.
"""

import datetime as dt
import os
import uuid
from unittest.mock import patch

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402

# Studio fixtures seed the local DB directly, so this module is local-only.
pytestmark = pytest.mark.local_only

RENDER_TASK_PATH = (
    "integrations.services.banner_generator.tasks.render_banner_for_content"
)


def _reset_state():
    from django_q.models import OrmQ, Task

    from content.models import Article, Course
    from integrations.models import IntegrationSetting

    OrmQ.objects.all().delete()
    Task.objects.all().delete()
    Article.objects.all().delete()
    Course.objects.all().delete()
    IntegrationSetting.objects.filter(key__startswith="BANNER_GENERATOR_").delete()
    connection.close()


def _enable_banner_generator():
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    for key, value in (
        ("BANNER_GENERATOR_FUNCTION_URL", "https://lambda.example.com/"),
        ("BANNER_GENERATOR_AUTH_TOKEN", "token-abc"),
        ("AWS_S3_CONTENT_BUCKET", "content-bucket"),
        ("CONTENT_CDN_BASE", "https://cdn.example.com"),
    ):
        IntegrationSetting.objects.update_or_create(
            key=key,
            defaults={
                "value": value,
                "is_secret": False,
                "group": "banner_generator",
                "description": "",
            },
        )
    clear_config_cache()
    connection.close()


def _create_article(slug="banner-hint-article", title="Banner Hint Article", **overrides):
    from content.models import Article

    defaults = dict(
        slug=slug, title=title, date=dt.date(2026, 1, 1),
    )
    defaults.update(overrides)
    article = Article.objects.create(**defaults)
    connection.close()
    return article


def _create_course(slug="banner-hint-course", title="Banner Hint Course", **overrides):
    from content.models import Course

    defaults = dict(slug=slug, title=title, status="published")
    defaults.update(overrides)
    course = Course.objects.create(**defaults)
    connection.close()
    return course


def _simulate_successful_worker(content_type, content_pk):
    from django_q.models import OrmQ

    from integrations.services.banner_generator.tasks import (
        render_banner_for_content,
    )

    with patch(
        "integrations.services.banner_generator.tasks.render_to_s3",
        return_value={"ok": True},
    ), patch(
        "integrations.services.banner_generator.tasks.delete_generated_banner_object",
        return_value=True,
    ):
        result = render_banner_for_content(content_type, content_pk)
    OrmQ.objects.all().delete()
    connection.close()
    return result


def _create_failed_task(content_type, content_pk, result_text):
    from django_q.models import Task

    task = Task.objects.create(
        id=uuid.uuid4().hex,
        name=f"Render banner: {content_type} #{content_pk} from studio regenerate button",
        func=RENDER_TASK_PATH,
        hook="",
        args=(content_type, content_pk),
        kwargs={},
        result=result_text,
        started=timezone.now() - dt.timedelta(seconds=30),
        stopped=timezone.now() - dt.timedelta(seconds=28),
        success=False,
        attempt_count=1,
    )
    connection.close()
    return task


def _create_inflight_ormq(content_type, content_pk):
    from django_q.models import OrmQ
    from django_q.signing import SignedPackage

    payload = {
        "id": uuid.uuid4().hex,
        "name": f"Render banner: {content_type} #{content_pk}",
        "func": RENDER_TASK_PATH,
        "args": (content_type, content_pk),
        "kwargs": {},
    }
    ormq = OrmQ.objects.create(
        key="default",
        payload=SignedPackage.dumps(payload),
        lock=None,
    )
    connection.close()
    return ormq


@pytest.mark.django_db(transaction=True)
class TestBannerGeneratorHintOnArticleEdit:
    """Issue #790: the four-state hint chrome on the Studio article edit page."""

    @pytest.mark.core
    def test_operator_sees_failure_hint_with_clickable_view_task_link(
        self, django_server, browser,
    ):
        _reset_state()
        _ensure_tiers()
        _enable_banner_generator()
        _create_staff_user(email="staff-banner-fail@test.com")
        article = _create_article(slug="hint-fail", title="Hint Fail")
        task = _create_failed_task(
            "article", article.pk,
            "botocore.exceptions.ClientError: AccessDenied on s3:PutObject",
        )

        context = _auth_context(browser, "staff-banner-fail@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/articles/{article.pk}/edit",
            wait_until="domcontentloaded",
        )

        hint = page.locator('[data-testid="banner-generator-last-failure"]')
        assert hint.count() == 1
        assert "Last attempt failed" in hint.inner_text()

        view_task_link = hint.locator("a", has_text="View task")
        assert view_task_link.count() == 1
        href = view_task_link.get_attribute("href")
        assert href == f"/studio/worker/task/{task.id}/"

        # Clicking lands on the worker-task detail page (200).
        view_task_link.click()
        page.wait_for_url(f"**/studio/worker/task/{task.id}/", timeout=10000)
        assert page.url.endswith(f"/studio/worker/task/{task.id}/")

    @pytest.mark.core
    def test_operator_sees_inflight_hint_and_disabled_button(
        self, django_server, browser,
    ):
        _reset_state()
        _ensure_tiers()
        _enable_banner_generator()
        _create_staff_user(email="staff-banner-inflight@test.com")
        article = _create_article(slug="hint-inflight", title="Hint Inflight")
        _create_inflight_ormq("article", article.pk)

        context = _auth_context(browser, "staff-banner-inflight@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/articles/{article.pk}/edit",
            wait_until="domcontentloaded",
        )

        in_progress = page.locator('[data-testid="banner-generator-in-progress"]')
        assert in_progress.count() == 1
        assert "Regeneration in progress" in in_progress.inner_text()

        # The enabled Regenerate button is replaced by the disabled-inflight variant.
        assert page.locator(
            '[data-testid="banner-generator-regenerate-button-disabled-inflight"]'
        ).count() == 1
        assert page.locator(
            '[data-testid="banner-generator-regenerate-button"]'
        ).count() == 0

    @pytest.mark.core
    def test_anonymous_cannot_reach_studio_edit_page(
        self, django_server, page,
    ):
        _reset_state()
        _ensure_tiers()
        _enable_banner_generator()
        article = _create_article(slug="hint-anon", title="Hint Anon")
        _create_failed_task("article", article.pk, "boom")

        response = page.goto(
            f"{django_server}/studio/articles/{article.pk}/edit",
            wait_until="domcontentloaded",
        )
        # Anonymous staff_required path either redirects to login or 404s.
        assert response.status in (302, 404) or "/accounts/login" in page.url
        # The failure hint is never exposed to anonymous visitors.
        html = page.content()
        assert 'data-testid="banner-generator-last-failure"' not in html

    @pytest.mark.core
    def test_operator_refreshes_to_cache_busting_jpg_after_worker_success(
        self, django_server, browser,
    ):
        _reset_state()
        _ensure_tiers()
        _enable_banner_generator()
        _create_staff_user(email="staff-banner-jpg@test.com")
        old_url = "https://cdn.example.com/banners/article/old.jpg"
        article = _create_article(
            slug="hint-jpg",
            title="Hint JPG",
            auto_banner_url=old_url,
        )

        context = _auth_context(browser, "staff-banner-jpg@test.com")
        page = context.new_page()
        edit_url = f"{django_server}/studio/articles/{article.pk}/edit"
        page.goto(edit_url, wait_until="domcontentloaded")

        page.locator('[data-testid="banner-generator-regenerate-button"]').click()
        page.wait_for_url(f"**/studio/articles/{article.pk}/edit", timeout=10000)
        new_url = _simulate_successful_worker("article", article.pk)

        page.goto(edit_url, wait_until="domcontentloaded")
        img = page.locator('[data-testid="banner-generator-image"]')
        assert img.count() == 1
        src = img.get_attribute("src")
        assert src == new_url
        assert src != old_url
        assert src.startswith("https://cdn.example.com/banners/article/")
        assert src.endswith(".jpg")

    @pytest.mark.core
    def test_operator_preserves_manual_cover_while_generated_banner_changes(
        self, django_server, browser,
    ):
        _reset_state()
        _ensure_tiers()
        _enable_banner_generator()
        _create_staff_user(email="staff-banner-cover@test.com")
        manual_cover = "https://cdn.example.com/manual/article-cover.png"
        old_url = "https://cdn.example.com/banners/article/old.jpg"
        article = _create_article(
            slug="hint-cover",
            title="Hint Cover",
            cover_image_url=manual_cover,
            auto_banner_url=old_url,
        )

        context = _auth_context(browser, "staff-banner-cover@test.com")
        page = context.new_page()
        edit_url = f"{django_server}/studio/articles/{article.pk}/edit"
        page.goto(edit_url, wait_until="domcontentloaded")
        page.locator('[data-testid="banner-generator-regenerate-button"]').click()
        page.wait_for_url(f"**/studio/articles/{article.pk}/edit", timeout=10000)
        new_url = _simulate_successful_worker("article", article.pk)

        page.goto(edit_url, wait_until="domcontentloaded")
        assert page.locator('input[name="cover_image_url"]').input_value() == manual_cover
        src = page.locator('[data-testid="banner-generator-image"]').get_attribute("src")
        assert src == new_url
        assert src != old_url
        assert src.endswith(".jpg")
