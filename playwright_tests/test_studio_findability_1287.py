"""End-to-end acceptance coverage for Studio findability and safety (#1287)."""

import datetime
import os
import re
from pathlib import Path

import pytest

from playwright_tests.conftest import auth_context, create_staff_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection
from django.utils import timezone

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]

SCREENSHOT_DIR = Path(".tmp/issue-1287-screenshots")


def _staff_page(browser, email, *, superuser=True):
    user = create_staff_user(email)
    if not superuser:
        user.is_superuser = False
        user.save(update_fields=["is_superuser"])
        connection.close()
    context = auth_context(browser, email)
    return context, context.new_page()


def _seed_findability_content():
    from content.models import Article
    from events.models import Event, EventSeries

    Event.objects.filter(slug__startswith="findability-1287").delete()
    EventSeries.objects.filter(slug__startswith="findability-1287").delete()
    Article.objects.filter(slug__startswith="findability-1287").delete()
    event = Event.objects.create(
        title="Findability 1287 Event",
        slug="findability-1287-event",
        status="upcoming",
        start_datetime=timezone.now() + datetime.timedelta(days=2),
    )
    series = EventSeries.objects.create(
        name="Findability 1287 Series",
        slug="findability-1287-series",
        start_time=datetime.time(17, 0),
    )
    article = Article.objects.create(
        title="Findability 1287 Article",
        slug="findability-1287-article",
        date=datetime.date.today(),
        published=True,
    )
    connection.close()
    return event, series, article


def _quick_jump(page, modifier="Control"):
    page.keyboard.press(f"{modifier}+k")
    dialog = page.get_by_test_id("studio-quick-jump")
    dialog.wait_for(state="visible")
    search = page.get_by_test_id("studio-quick-jump-input")
    assert search.evaluate("element => element === document.activeElement")
    return dialog, search


def _toggle(page, section):
    return page.locator(f'[data-studio-section-key="{section}"]')


def _seed_stripe_settings():
    from integrations.models import IntegrationSetting

    IntegrationSetting.objects.filter(group="stripe").delete()
    IntegrationSetting.objects.create(
        key="STRIPE_CUSTOMER_PORTAL_URL",
        value="https://example.com/old-portal",
        group="stripe",
        is_secret=False,
    )
    IntegrationSetting.objects.create(
        key="STRIPE_SECRET_KEY",
        value="sk_old_1287",
        group="stripe",
        is_secret=True,
    )
    connection.close()


def _integration_values(*keys):
    from integrations.models import IntegrationSetting

    result = dict(
        IntegrationSetting.objects.filter(key__in=keys).values_list("key", "value")
    )
    connection.close()
    return result


def _assert_no_horizontal_overflow(page):
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
    )


def _capture_theme_pair(page, stem):
    for theme in ("light", "dark"):
        page.evaluate("theme => window.themeToggle.setTheme(theme)", theme)
        page.screenshot(
            path=SCREENSHOT_DIR / f"{stem}-{theme}.png",
            full_page=True,
        )
        _assert_no_horizontal_overflow(page)


def test_quick_jump_meta_and_ctrl_enter_open_top_event(django_server, browser):
    event, _, _ = _seed_findability_content()
    context, page = _staff_page(browser, "findability-shortcuts-1287@test.com")
    for modifier in ("Meta", "Control"):
        page.goto(f"{django_server}/studio/users/", wait_until="domcontentloaded")
        _, search = _quick_jump(page, modifier)
        search.fill(event.title)
        results = page.get_by_test_id("studio-quick-jump-results")
        results.get_by_text("Events", exact=True).wait_for(state="visible")
        assert event.title in results.get_by_role("option").first.inner_text()
        search.press("Enter")
        page.wait_for_url(re.compile(rf"/studio/events/{event.id}/edit"))
    context.close()


def test_quick_jump_page_arrows_and_settings_navigation(django_server, browser):
    context, page = _staff_page(browser, "findability-pages-1287@test.com")
    page.goto(f"{django_server}/studio/events/new", wait_until="domcontentloaded")
    _, search = _quick_jump(page)
    search.fill("settings")
    option = page.get_by_test_id("studio-quick-jump-results").get_by_role(
        "option", name=re.compile("Settings")
    )
    option.wait_for(state="visible")
    search.press("ArrowDown")
    assert option.get_attribute("aria-selected") == "true"
    search.press("ArrowUp")
    search.press("ArrowDown")
    search.press("Enter")
    page.wait_for_url(re.compile(r"/studio/settings/$"))
    context.close()


def test_quick_jump_escape_and_outside_click_restore_form_focus(django_server, browser):
    context, page = _staff_page(browser, "findability-focus-1287@test.com")
    page.goto(f"{django_server}/studio/events/new", wait_until="domcontentloaded")
    title = page.locator('input[name="title"]')
    title.fill("Draft remains intact")
    title.focus()
    dialog, _ = _quick_jump(page, "Meta")
    page.keyboard.press("Escape")
    assert dialog.is_hidden()
    assert title.input_value() == "Draft remains intact"
    assert title.evaluate("element => element === document.activeElement")
    dialog, _ = _quick_jump(page)
    dialog.click(position={"x": 3, "y": 3})
    assert dialog.is_hidden()
    assert title.evaluate("element => element === document.activeElement")
    context.close()


def test_sidebar_search_desktop_series_and_mobile_article(django_server, browser):
    _, series, article = _seed_findability_content()
    context, page = _staff_page(browser, "findability-sidebar-1287@test.com")
    page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")
    page.get_by_test_id("studio-global-search-input").fill(series.name)
    page.get_by_test_id("studio-global-search-results").get_by_role(
        "option", name=re.compile(series.name)
    ).click()
    page.wait_for_url(re.compile(rf"/studio/event-series/{series.id}/$"))

    page.set_viewport_size({"width": 393, "height": 852})
    page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")
    page.locator("#studio-sidebar-toggle").click()
    page.get_by_test_id("studio-global-search-input").fill(article.title)
    page.get_by_test_id("studio-global-search-results").get_by_role(
        "option", name=re.compile(article.title)
    ).click()
    page.wait_for_url(re.compile(rf"/studio/articles/{article.id}/edit"))
    _assert_no_horizontal_overflow(page)
    context.close()


def test_ordinary_staff_pages_exclude_superuser_and_show_no_results(django_server, browser):
    context, page = _staff_page(
        browser, "findability-ordinary-1287@test.com", superuser=False
    )
    page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")
    search = page.get_by_test_id("studio-global-search-input")
    results = page.get_by_test_id("studio-global-search-results")
    search.fill("user")
    results.get_by_text("Pages", exact=True).wait_for(state="visible")
    assert results.get_by_text("Users", exact=True).is_visible()
    assert results.get_by_text("New user", exact=True).count() == 0
    search.fill("API tokens")
    results.get_by_text("No results", exact=True).wait_for(state="visible")
    search.fill("zz-no-result-1287")
    results.get_by_text("No results", exact=True).wait_for(state="visible")
    assert page.url.rstrip("/").endswith("/studio")
    context.close()


def test_settings_filter_cross_section_clear_and_no_results(django_server, browser):
    context, page = _staff_page(browser, "findability-filter-1287@test.com")
    page.goto(f"{django_server}/studio/settings/", wait_until="domcontentloaded")
    settings_filter = page.locator("[data-settings-filter]")
    settings_filter.fill("SLACK_BOT_TOKEN")
    assert page.locator('[data-settings-section="messaging"]').is_visible()
    assert page.locator("#integration-slack").is_visible()
    assert page.locator('[data-field-key="SLACK_BOT_TOKEN"]').is_visible()
    assert page.locator('[data-field-key="SLACK_ENVIRONMENT"]').is_hidden()
    page.locator("[data-settings-filter-clear]").click()
    assert page.locator('[data-settings-section="auth"]').is_visible()
    assert page.locator('[data-settings-section="messaging"]').is_hidden()
    settings_filter.fill("NO_SETTING_1287")
    assert page.locator("[data-settings-filter-empty]").is_visible()
    assert page.locator(
        '[data-settings-section] form[action*="/studio/settings/"]:visible'
    ).count() == 0
    context.close()


def test_invalid_stripe_save_is_atomic_then_valid_save_succeeds(django_server, browser):
    _seed_stripe_settings()
    context, page = _staff_page(browser, "findability-atomic-1287@test.com")
    page.goto(f"{django_server}/studio/settings/#payments", wait_until="domcontentloaded")
    stripe = page.locator("#integration-stripe")
    stripe.locator('input[name="STRIPE_CUSTOMER_PORTAL_URL"]').fill("not-a-url")
    stripe.locator('input[name="STRIPE_SECRET_KEY"]').fill("sk_changed_1287")
    stripe.locator('button[type="submit"]').click()
    page.wait_for_load_state("domcontentloaded")
    assert "STRIPE_CUSTOMER_PORTAL_URL must be a valid URL. No settings were saved." in page.locator("body").inner_text()
    assert page.url.endswith("/studio/settings/#payments")
    assert _integration_values(
        "STRIPE_CUSTOMER_PORTAL_URL", "STRIPE_SECRET_KEY"
    ) == {
        "STRIPE_CUSTOMER_PORTAL_URL": "https://example.com/old-portal",
        "STRIPE_SECRET_KEY": "sk_old_1287",
    }
    stripe = page.locator("#integration-stripe")
    stripe.locator('input[name="STRIPE_CUSTOMER_PORTAL_URL"]').fill(
        "https://example.com/new-portal"
    )
    stripe.locator('input[name="STRIPE_SECRET_KEY"]').fill("sk_changed_1287")
    stripe.locator('button[type="submit"]').click()
    page.wait_for_load_state("domcontentloaded")
    assert re.search(r"Saved \d+ settings in Stripe\.", page.locator("body").inner_text())
    context.close()


def test_clear_override_preserves_sibling_and_updates_source(django_server, browser):
    _seed_stripe_settings()
    context, page = _staff_page(browser, "findability-clear-1287@test.com")
    page.goto(f"{django_server}/studio/settings/#payments", wait_until="domcontentloaded")
    page.locator('[data-clear-override="STRIPE_CUSTOMER_PORTAL_URL"]').click()
    page.wait_for_load_state("domcontentloaded")
    assert "Cleared override for STRIPE_CUSTOMER_PORTAL_URL — now using env/default." in page.locator("body").inner_text()
    values = _integration_values("STRIPE_CUSTOMER_PORTAL_URL", "STRIPE_SECRET_KEY")
    assert "STRIPE_CUSTOMER_PORTAL_URL" not in values
    assert values["STRIPE_SECRET_KEY"] == "sk_old_1287"
    assert page.locator(
        '[data-field-key="STRIPE_CUSTOMER_PORTAL_URL"] [data-source-badge="db"]'
    ).count() == 0
    context.close()


def test_sidebar_preferences_merge_across_navigation_and_reload(django_server, browser):
    context, page = _staff_page(browser, "findability-persist-1287@test.com")
    page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")
    for section in ("content", "people"):
        button = _toggle(page, section)
        if button.get_attribute("aria-expanded") != "true":
            button.click()
    events = _toggle(page, "events")
    if events.get_attribute("aria-expanded") == "true":
        events.click()
    page.goto(f"{django_server}/studio/plans/", wait_until="domcontentloaded")
    for section in ("content", "people", "planning"):
        assert _toggle(page, section).get_attribute("aria-expanded") == "true"
    assert _toggle(page, "events").get_attribute("aria-expanded") == "false"
    page.reload(wait_until="domcontentloaded")
    for section in ("content", "people", "planning"):
        assert _toggle(page, section).get_attribute("aria-expanded") == "true"
    assert _toggle(page, "events").get_attribute("aria-expanded") == "false"
    context.close()


def test_active_section_survives_closed_and_malformed_storage(django_server, browser):
    context, page = _staff_page(browser, "findability-stale-1287@test.com")
    page.add_init_script(
        "localStorage.setItem('studio-nav-open', JSON.stringify({events:false, content:true}))"
    )
    page.goto(f"{django_server}/studio/events/", wait_until="domcontentloaded")
    assert _toggle(page, "events").get_attribute("aria-expanded") == "true"
    assert page.get_by_role("link", name="Events", exact=True).is_visible()
    assert page.evaluate("JSON.parse(localStorage.getItem('studio-nav-open')).events") is False
    page.evaluate("localStorage.setItem('studio-nav-open', '{bad json')")
    page.reload(wait_until="domcontentloaded")
    assert _toggle(page, "events").get_attribute("aria-expanded") == "true"
    assert page.evaluate("window.__findabilityErrors || []") == []
    context.close()


def test_host_labels_routes_field_contract_and_visual_states(django_server, browser):
    _seed_findability_content()
    context, page = _staff_page(browser, "findability-labels-1287@test.com")
    page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")
    assert page.get_by_role("link", name="Event hosts", exact=True).get_attribute("href") == "/studio/hosts/"
    people = _toggle(page, "people")
    if people.get_attribute("aria-expanded") != "true":
        people.click()
    assert page.get_by_role("link", name="Call hosts (scheduling)", exact=True).get_attribute("href") == "/studio/call-hosts/"
    page.goto(f"{django_server}/studio/events/new", wait_until="domcontentloaded")
    host = page.locator('input[name="host_email"]')
    assert host.count() == 1
    assert page.get_by_text("Auto-register host (platform email)", exact=True).is_visible()

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    consent = page.get_by_test_id("analytics-consent-deny")
    if consent.is_visible():
        with page.expect_navigation(wait_until="domcontentloaded"):
            consent.click()
        assert page.get_by_test_id("analytics-consent-panel").is_hidden()
    page.set_viewport_size({"width": 1440, "height": 1000})
    _, search = _quick_jump(page)
    search.fill("Findability 1287 Event")
    page.get_by_test_id("studio-quick-jump-results").get_by_role(
        "option"
    ).first.wait_for(state="visible")
    _capture_theme_pair(page, "quick-jump-desktop")
    page.keyboard.press("Escape")

    page.evaluate(
        "localStorage.setItem('studio-nav-open', "
        "JSON.stringify({events:false, content:true, people:true}))"
    )
    page.goto(f"{django_server}/studio/plans/", wait_until="domcontentloaded")
    for section in ("content", "people", "planning"):
        assert _toggle(page, section).get_attribute("aria-expanded") == "true"
    assert _toggle(page, "events").get_attribute("aria-expanded") == "false"
    sidebar_search = page.get_by_test_id("studio-global-search-input")
    sidebar_search.fill("Event hosts")
    page.get_by_test_id("studio-global-search-results").get_by_role(
        "option", name=re.compile("Event hosts")
    ).wait_for(state="visible")
    _capture_theme_pair(page, "sidebar-search-persisted-desktop")

    page.goto(f"{django_server}/studio/settings/", wait_until="domcontentloaded")
    settings_filter = page.locator("[data-settings-filter]")
    settings_filter.fill("SLACK_BOT_TOKEN")
    assert page.locator('[data-field-key="SLACK_BOT_TOKEN"]').is_visible()
    _capture_theme_pair(page, "settings-filtered-desktop")

    page.set_viewport_size({"width": 393, "height": 852})
    settings_filter.fill("NO_SETTING_1287")
    assert page.locator("[data-settings-filter-empty]").is_visible()
    _capture_theme_pair(page, "settings-no-results-mobile")

    _seed_stripe_settings()
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.goto(
        f"{django_server}/studio/settings/#payments",
        wait_until="domcontentloaded",
    )
    stripe = page.locator("#integration-stripe")
    stripe.locator('input[name="STRIPE_CUSTOMER_PORTAL_URL"]').fill(
        "not-a-url"
    )
    stripe.locator('input[name="STRIPE_SECRET_KEY"]').fill("sk_changed_1287")
    with page.expect_navigation(wait_until="domcontentloaded"):
        stripe.locator('button[type="submit"]').click()
    assert (
        "STRIPE_CUSTOMER_PORTAL_URL must be a valid URL. No settings were saved."
        in page.locator("body").inner_text()
    )
    _capture_theme_pair(page, "settings-validation-error-desktop")
    context.close()
