"""Browser acceptance for content-sync observability (issue #1286)."""

import os
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import pytest
from django.db import connection
from django.utils import timezone

from playwright_tests.conftest import create_session_for_user, create_staff_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [pytest.mark.core, pytest.mark.local_only, pytest.mark.django_db(transaction=True)]

SCREENSHOTS = Path(__file__).resolve().parents[1] / ".tmp" / "screenshots" / "issue-1286"


def _seed():
    from content.models import Article, Course
    from integrations.models import ContentSource, SyncLog

    SyncLog.objects.all().delete()
    ContentSource.objects.all().delete()
    Article.objects.filter(slug__in=("unique-error-target", "collision-target")).delete()
    Course.objects.filter(slug="collision-target").delete()
    now = timezone.now()
    sources = {}
    specs = (
        ("fresh", "synthetic/fresh-content", "success", now - timedelta(hours=1)),
        ("stale", "synthetic/stale-content", "success", now - timedelta(days=8)),
        ("failed", "synthetic/failed-content", "failed", now - timedelta(days=2)),
        ("partial", "synthetic/partial-content-with-an-extremely-long-repository-name", "partial", now),
        ("never", "synthetic/never-content", None, None),
    )
    for key, repo, status, fresh_at in specs:
        sources[key] = ContentSource.objects.create(
            repo_name=repo,
            webhook_secret="synthetic-secret",
            last_sync_status=status,
            last_synced_at=fresh_at,
        )
    article = Article.objects.create(
        title="Unique error target", slug="unique-error-target", date=date(2026, 1, 1),
    )
    Article.objects.create(
        title="Collision article", slug="collision-target", date=date(2026, 1, 2),
    )
    Course.objects.create(title="Collision course", slug="collision-target")
    long_message = "A synthetic parser failure with a deliberately long line " * 8
    SyncLog.objects.create(
        source=sources["partial"],
        status="partial",
        errors=[
            {"file": "articles/unique-error-target.md", "error": long_message},
            {"file": "articles/unique-error-target.md", "error": long_message},
            {"file": "courses/collision-target.yaml", "error": "collision-target is ambiguous"},
        ],
    )
    SyncLog.objects.create(
        source=sources["failed"],
        status="failed",
        errors=[{"file": "failed.md", "error": "Synthetic failed run"}],
    )
    for index in range(52):
        errors = [{"file": f"history-{index}.md", "error": "Synthetic history failure"}]
        if index == 51:
            errors = [{
                "file": "articles/unique-error-target.md",
                "error": "unique-error-target has a synthetic history failure",
            }]
        SyncLog.objects.create(
            source=sources["failed"],
            status="failed",
            errors=errors,
        )
    connection.close()
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    return sources, article


def _context(browser, email, *, theme="light", viewport=None):
    session = create_session_for_user(email)
    context = browser.new_context(viewport=viewport or {"width": 1440, "height": 900})
    context.add_cookies([{
        "name": "sessionid", "value": session, "domain": "127.0.0.1", "path": "/",
    }, {
        "name": "aslab_analytics_consent", "value": "denied",
        "domain": "127.0.0.1", "path": "/", "httpOnly": True,
    }])
    context.add_init_script(f"window.localStorage.setItem('theme', '{theme}');")
    return context


def _dismiss_consent(page):
    button = page.get_by_test_id("analytics-consent-deny")
    if button.is_visible():
        button.click()
        page.wait_for_load_state("networkidle")


def _no_horizontal_overflow(page):
    widths = page.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]")
    assert widths[0] <= widths[1] + 2, widths


def _assert_visible_keyboard_focus(locator):
    focus = locator.evaluate(
        "el => {"
        " const style = getComputedStyle(el);"
        " return document.activeElement === el && el.matches(':focus-visible') &&"
        "   (style.boxShadow !== 'none' || style.outlineStyle !== 'none');"
        "}",
    )
    assert focus, f"No visible keyboard focus for {locator}"


def _tab_to(page, locator, *, reverse=False, limit=160):
    """Reach a control with real keyboard navigation and prove its focus ring."""
    locator = locator.first
    key = "Shift+Tab" if reverse else "Tab"
    for _ in range(limit):
        page.keyboard.press(key)
        if locator.evaluate("el => document.activeElement === el"):
            _assert_visible_keyboard_focus(locator)
            return locator
    raise AssertionError(f"Could not reach {locator} with {key}")


def test_dashboard_health_errors_anchor_polling_and_screenshots(django_server, browser):
    email = "sync-observability-browser@test.com"
    create_staff_user(email)
    sources, article = _seed()
    for theme in ("light", "dark"):
        context = _context(browser, email, theme=theme)
        page = context.new_page()
        page.goto(f"{django_server}/studio/sync/", wait_until="networkidle")
        _dismiss_consent(page)
        rows = page.get_by_test_id("sync-health-row")
        assert rows.count() == 5
        rows.first.wait_for(state="visible")
        assert page.get_by_test_id("analytics-consent-panel").is_hidden()
        assert "Never refreshed" in rows.filter(has_text="never-content").inner_text()
        page.screenshot(path=SCREENSHOTS / f"dashboard-desktop-{theme}.png")

        partial = rows.filter(has_text="partial-content")
        partial.focus()
        assert partial.evaluate("el => el.matches(':focus')")
        partial.press("Enter")
        page.wait_for_timeout(100)
        card = page.locator(f"#sync-source-{sources['partial'].pk}")
        assert page.url.endswith(f"#sync-source-{sources['partial'].pk}")
        assert "scroll-mt-24" in (card.get_attribute("class") or "")

        details = card.get_by_test_id("sync-structured-errors")
        assert details.get_attribute("open") is None
        details.locator("summary").click()
        assert "3 errors (2 unique)" in details.inner_text()
        assert "×2" in details.inner_text()
        assert details.locator(f'a[href="/studio/articles/{article.pk}/edit"]').count() == 1
        assert details.locator('a[data-error-target]').filter(has_text="ambiguous").count() == 0
        if theme == "light":
            page.screenshot(path=SCREENSHOTS / "dashboard-errors-expanded.png")
        context.close()


def test_history_filters_keyboard_pager_and_desktop_screenshots(django_server, browser):
    email = "sync-history-browser@test.com"
    create_staff_user(email)
    sources, _article = _seed()
    for theme in ("light", "dark"):
        context = _context(browser, email, theme=theme)
        page = context.new_page()
        page.goto(f"{django_server}/studio/sync/history/", wait_until="networkidle")
        _dismiss_consent(page)
        source = page.locator("#sync-history-source")
        status = page.locator("#sync-history-status")
        assert source.get_attribute("aria-label") is None  # associated native label is used

        _tab_to(page, source)
        page.keyboard.press("ArrowDown")  # failed-content is the first sorted source
        assert source.input_value() == str(sources["failed"].pk)
        page.keyboard.press("Tab")
        _assert_visible_keyboard_focus(status)
        page.keyboard.type("Failed")
        assert status.input_value() == "failed"
        page.keyboard.press("Tab")
        apply_button = page.get_by_role("button", name="Apply")
        _assert_visible_keyboard_focus(apply_button)
        with page.expect_navigation(wait_until="networkidle"):
            page.keyboard.press("Enter")
        assert f"source={sources['failed'].pk}" in page.url
        assert "status=failed" in page.url
        assert page.get_by_role("link", name="Next").count() == 1

        _tab_to(page, page.get_by_role("link", name="Next"))
        with page.expect_navigation(wait_until="networkidle"):
            page.keyboard.press("Enter")
        assert f"source={sources['failed'].pk}" in page.url and "status=failed" in page.url
        assert "page=2" in page.url
        _tab_to(page, page.get_by_role("link", name="Previous"))
        with page.expect_navigation(wait_until="networkidle"):
            page.keyboard.press("Enter")
        assert "page=2" not in page.url

        batch_summary = _tab_to(
            page,
            page.locator("details[data-sync-history-batch] > summary").first,
        )
        page.keyboard.press("Enter")
        page.wait_for_function(
            "summary => summary.parentElement.open",
            arg=batch_summary.element_handle(),
        )
        details_action = batch_summary.locator('[data-action="sync-history-details"]')
        page.wait_for_function(
            "action => action.getAttribute('aria-expanded') === 'true'",
            arg=details_action.element_handle(),
        )
        batch_detail = page.locator(".batch-detail").first
        batch_detail.wait_for(state="visible")
        error_summary = _tab_to(
            page,
            batch_detail.get_by_test_id("sync-structured-errors").locator("summary"),
        )
        page.keyboard.press("Enter")
        assert batch_detail.get_by_test_id("sync-structured-errors").get_attribute("open") is not None
        target = _tab_to(page, batch_detail.locator("[data-error-target]"))
        assert "unique-error-target" in target.inner_text()
        page.keyboard.press("Shift+Tab")
        assert error_summary.evaluate("el => document.activeElement === el")
        page.keyboard.press("Enter")
        assert batch_detail.get_by_test_id("sync-structured-errors").get_attribute("open") is None
        assert page.get_by_role("navigation", name="Sync history pages").is_visible()
        error_summary.scroll_into_view_if_needed()
        page.get_by_role("heading", name="Sync history").dispatch_event("mousedown")
        assert error_summary.evaluate("el => el.matches(':focus-visible')")
        page.screenshot(path=SCREENSHOTS / f"history-desktop-{theme}.png")

        if theme == "light":
            _tab_to(page, page.get_by_role("link", name="Clear filters"))
            with page.expect_navigation(wait_until="networkidle"):
                page.keyboard.press("Enter")
            assert "source=" not in page.url
            assert page.url.rstrip("/").endswith("/studio/sync/history")
        context.close()


def test_mobile_dashboard_and_history_wrap_without_overflow(django_server, browser):
    email = "sync-mobile-browser@test.com"
    create_staff_user(email)
    sources, _article = _seed()
    context = _context(browser, email, viewport={"width": 393, "height": 852})
    page = context.new_page()
    page.goto(f"{django_server}/studio/sync/", wait_until="networkidle")
    _dismiss_consent(page)
    _no_horizontal_overflow(page)
    partial_card = page.locator(f"#sync-source-{sources['partial'].pk}")
    dashboard_error_summary = _tab_to(
        page,
        partial_card.get_by_test_id("sync-structured-errors").locator("summary"),
    )
    page.keyboard.press("Enter")
    partial_card.scroll_into_view_if_needed()
    _no_horizontal_overflow(page)
    status = partial_card.locator('td[data-label="Status"]').get_by_text(
        "Completed with 3 errors",
        exact=True,
    ).first
    assert status.is_visible()
    status_box = status.bounding_box()
    card_box = partial_card.bounding_box()
    assert status_box["x"] + status_box["width"] <= card_box["x"] + card_box["width"] + 1
    assert dashboard_error_summary.evaluate("el => el.matches(':focus-visible')")
    page.screenshot(path=SCREENSHOTS / "dashboard-mobile-light.png")

    page.goto(
        f"{django_server}/studio/sync/history/?source={sources['partial'].pk}&status=partial",
        wait_until="networkidle",
    )
    _no_horizontal_overflow(page)
    for selector in ("#sync-history-source", "#sync-history-status"):
        assert page.locator(selector).evaluate("el => el.getBoundingClientRect().height >= 44")
    batch_summary = _tab_to(
        page,
        page.locator("details[data-sync-history-batch] > summary").first,
    )
    page.keyboard.press("Enter")
    page.wait_for_function(
        "summary => summary.parentElement.open",
        arg=batch_summary.element_handle(),
    )
    history_error_summary = _tab_to(
        page,
        page.get_by_test_id("sync-structured-errors").locator("summary"),
    )
    page.keyboard.press("Enter")
    history_error_summary.scroll_into_view_if_needed()
    _no_horizontal_overflow(page)
    page.get_by_role("heading", name="Sync history").dispatch_event("mousedown")
    assert history_error_summary.evaluate("el => el.matches(':focus-visible')")
    page.screenshot(path=SCREENSHOTS / "history-mobile-light.png")
    context.close()


def test_dashboard_fragment_poll_updates_summary_and_card_atomically(django_server, browser):
    from playwright_tests.test_github_content_sync import _sync_blog_source_with_error

    email = "sync-poller-browser@test.com"
    create_staff_user(email)
    sources, _article = _seed()
    source = sources["fresh"]

    context = _context(browser, email)
    page = context.new_page()
    page.goto(f"{django_server}/studio/sync/", wait_until="networkidle")
    _dismiss_consent(page)
    row = page.get_by_test_id("sync-health-row").filter(has_text="fresh-content")
    card = page.locator(f"#sync-source-{source.pk}")

    with mock.patch(
        "integrations.services.content_sync_queue._enqueue_async_task",
        return_value="synthetic-sync-task",
    ) as enqueue:
        card.get_by_role("button", name="Sync now").click()
        page.wait_for_load_state("networkidle")
    enqueue.assert_called_once()
    assert enqueue.call_args.kwargs["force"] is False
    assert "queued" in row.inner_text().lower()
    assert card.get_attribute("data-status") == "queued"
    queued_card = card.element_handle()

    sync_log = _sync_blog_source_with_error(
        source,
        [{"slug": "poller-fixture-article", "title": "Poller fixture article"}],
        bad_filename="synthetic-poller-error.md",
    )
    assert sync_log.status == "partial"
    connection.close()
    page.wait_for_function(
        "sourceId => {"
        " const card = document.querySelector('#sync-source-' + sourceId);"
        " const row = [...document.querySelectorAll('[data-testid=sync-health-row]')]"
        ".find(el => el.textContent.includes('fresh-content'));"
        " return card?.dataset.status === 'partial' && row?.textContent.includes('Completed with');"
        "}",
        arg=str(source.pk),
        timeout=10_000,
    )
    assert queued_card.evaluate("el => !el.isConnected")
    refreshed_errors = card.get_by_test_id("sync-structured-errors")
    assert refreshed_errors.get_attribute("open") is None
    refreshed_errors.locator("summary").click()
    assert "synthetic-poller-error.md" in refreshed_errors.inner_text()
    context.close()
