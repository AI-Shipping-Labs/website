"""Issue #543 logged-in member surface screenshots.

Screenshots are written to ``/tmp/aisl-issue-543-screenshots`` for manual
review across the requested themes and viewports.
"""

import os
from pathlib import Path

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_site_config_tiers as _ensure_site_config_tiers,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

SCREENSHOT_DIR = Path("/tmp/aisl-issue-543-screenshots")
DESKTOP = {"width": 1280, "height": 900}
MOBILE = {"width": 393, "height": 851}


def _set_theme(context, theme):
    context.add_init_script(
        f"""
            localStorage.setItem('theme', '{theme}');
            document.documentElement.classList.toggle('dark', '{theme}' === 'dark');
        """
    )


def _doc_overflow(page):
    return page.evaluate(
        "() => document.documentElement.scrollWidth - "
        "document.documentElement.clientWidth"
    )


def _capture(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=True)


def _assert_theme(page, theme):
    has_dark_class = page.evaluate(
        "() => document.documentElement.classList.contains('dark')"
    )
    assert has_dark_class is (theme == "dark")


def _seed_member_notifications(user):
    from django.db import connection

    from notifications.models import Notification

    Notification.objects.all().delete()
    Notification.objects.create(
        user=user,
        title="New workshop page available",
        body="A new checkpoint was added to your current sprint plan.",
        url="/account/",
        notification_type="new_content",
        read=False,
    )
    Notification.objects.create(
        user=user,
        title="Event reminder",
        body="Your next live session starts soon.",
        url="/events",
        notification_type="event_reminder",
        read=True,
    )
    connection.close()


def _open_member_page(browser, email, base_url, path, viewport, theme):
    context = _auth_context(browser, email)
    _set_theme(context, theme)
    page = context.new_page()
    page.set_viewport_size(viewport)
    page.goto(f"{base_url}{path}", wait_until="networkidle")
    _assert_theme(page, theme)
    return context, page


def _open_anon_page(browser, base_url, path, viewport, theme):
    context = browser.new_context(viewport=viewport)
    _set_theme(context, theme)
    page = context.new_page()
    page.goto(f"{base_url}{path}", wait_until="networkidle")
    _assert_theme(page, theme)
    return context, page


@pytest.mark.django_db(transaction=True)
def test_member_surfaces_have_consistent_frames_screenshots(
    django_server, browser,
):
    _ensure_tiers()
    _ensure_site_config_tiers()
    user = _create_user(
        "issue-543-main@example.com",
        tier_slug="main",
        first_name="Alex",
    )
    _seed_member_notifications(user)

    member_routes = [
        ("dashboard", "/", "Welcome back, Alex"),
        ("account", "/account/", "Account"),
        ("notifications", "/notifications", "Notifications"),
    ]
    auth_routes = [
        ("login", "/accounts/login/", "Sign in"),
        ("register", "/accounts/register/", "Create Account"),
    ]

    for label, path, text in member_routes:
        for theme in ("light", "dark"):
            context, page = _open_member_page(
                browser, user.email, django_server, path, DESKTOP, theme
            )
            try:
                page.get_by_role("heading", name=text).first.wait_for()
                assert _doc_overflow(page) <= 1
                _capture(page, f"{label}-desktop-{theme}-1280x900")
            finally:
                context.close()

            context, page = _open_member_page(
                browser, user.email, django_server, path, MOBILE, theme
            )
            try:
                page.get_by_role("heading", name=text).first.wait_for()
                assert _doc_overflow(page) <= 1
                _capture(page, f"{label}-mobile-{theme}-393x851")
            finally:
                context.close()

    for label, path, text in auth_routes:
        for theme in ("light", "dark"):
            context, page = _open_anon_page(
                browser, django_server, path, DESKTOP, theme
            )
            try:
                page.get_by_role("heading", name=text).wait_for()
                assert _doc_overflow(page) <= 1
                _capture(page, f"{label}-desktop-{theme}-1280x900")
            finally:
                context.close()

            context, page = _open_anon_page(
                browser, django_server, path, MOBILE, theme
            )
            try:
                page.get_by_role("heading", name=text).wait_for()
                assert _doc_overflow(page) <= 1
                _capture(page, f"{label}-mobile-{theme}-393x851")
            finally:
                context.close()
