"""Shared comment timestamp browser contracts for issue #1590."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import create_session_for_user, create_user
from playwright_tests.test_workshop_comments import (
    _clear_workshops_and_courses,
    _create_course_with_unit,
)
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

pytestmark = [pytest.mark.core, pytest.mark.local_only]

SCREENSHOT_DIR = Path(".tmp/screenshots/issue-1590")
FIXED_NOW = datetime(2027, 3, 1, 12, 0, tzinfo=UTC)


def _auth_context(
    browser,
    email,
    *,
    locale="en-US",
    timezone_id="Europe/Berlin",
    viewport=None,
):
    session_key = create_session_for_user(email)
    context = browser.new_context(
        locale=locale,
        timezone_id=timezone_id,
        viewport=viewport or {"width": 1280, "height": 720},
    )
    context.add_cookies(
        [
            {
                "name": "sessionid",
                "value": session_key,
                "domain": "127.0.0.1",
                "path": "/",
            },
            {
                "name": "csrftoken",
                "value": "e2e-test-csrf-token-value",
                "domain": "127.0.0.1",
                "path": "/",
            },
        ]
    )
    return context


def _install_fixed_now(page):
    now_ms = int(FIXED_NOW.timestamp() * 1000)
    page.add_init_script(
        script=f"""
          (() => {{
            const fixedNow = {now_ms};
            const NativeDate = Date;
            window.Date = class extends NativeDate {{
              constructor(...args) {{
                super(...(args.length ? args : [fixedNow]));
              }}
              static now() {{
                return fixedNow;
              }}
            }};
          }})();
        """
    )


def _seed_course_thread(*, author_name="Date Reader", rows):
    _, _, unit = _create_course_with_unit(
        course_slug="comment-dates",
        module_slug="dates-module",
        unit_slug="local-dates",
    )
    author = create_user(
        "comment-dates-author@test.com",
        tier_slug="basic",
        first_name=author_name,
    )
    viewer = create_user(
        "comment-dates-viewer@test.com",
        tier_slug="basic",
        first_name="Viewer",
    )

    from comments.models import Comment

    comments = {}
    for key, body, created_at, parent_key in rows:
        comment = Comment.objects.create(
            content_id=unit.content_id,
            user=author,
            body=body,
            parent=comments.get(parent_key),
        )
        Comment.objects.filter(pk=comment.pk).update(created_at=created_at)
        comments[key] = comment
    connection.close()
    return unit, viewer, comments


def _open_course_thread(page, django_server):
    page.goto(
        f"{django_server}/courses/comment-dates/dates-module/local-dates",
        wait_until="domcontentloaded",
    )
    page.get_by_test_id("comment-timestamp").first.wait_for(state="visible")


def _capture_qa(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    consent = page.get_by_role("button", name="Keep analytics off")
    if consent.count() and consent.is_visible():
        consent.click()
        expect(consent).to_be_hidden()
    page.locator("#qa-section").screenshot(path=SCREENSHOT_DIR / f"{name}.png")


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_us_locale_course_question_and_reply_use_day_first_semantic_dates(
    browser,
    django_server,
):
    _clear_workshops_and_courses()
    created_at = datetime(2026, 7, 16, 10, 30, tzinfo=UTC)
    _, viewer, comments = _seed_course_thread(
        rows=[
            ("question", "Question on local dates", created_at, None),
            ("reply", "Reply on local dates", created_at, "question"),
        ],
    )
    context = _auth_context(
        browser,
        viewer.email,
        locale="en-US",
        timezone_id="Europe/Berlin",
    )
    page = context.new_page()
    _install_fixed_now(page)

    try:
        _open_course_thread(page, django_server)
        card = page.locator(
            f'#qa-list > [data-comment-id="{comments["question"].pk}"]'
        )
        question_time = card.get_by_test_id("comment-timestamp")
        reply_time = card.get_by_test_id("reply-timestamp")

        expect(question_time).to_have_text("16/07/2026")
        expect(reply_time).to_have_text("16/07/2026")
        expect(question_time).not_to_have_text("7/16/2026")
        expect(question_time).to_have_attribute("datetime", created_at.isoformat())
        expect(reply_time).to_have_attribute("datetime", created_at.isoformat())
        accessible_label = question_time.get_attribute("aria-label") or ""
        assert "2026" in accessible_label
        assert ":" in accessible_label
        _capture_qa(page, "course-comments-desktop-light")
    finally:
        context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_timezone_boundary_changes_local_day_without_changing_format(
    browser,
    django_server,
):
    _clear_workshops_and_courses()
    boundary = datetime(2027, 1, 1, 0, 30, tzinfo=UTC)
    _, viewer, comments = _seed_course_thread(
        rows=[("boundary", "Timezone boundary", boundary, None)],
    )

    cases = [
        ("en-US", "America/Los_Angeles", "31/12/2026"),
        ("de-DE", "Europe/Berlin", "01/01/2027"),
    ]
    for locale, timezone_id, expected in cases:
        context = _auth_context(
            browser,
            viewer.email,
            locale=locale,
            timezone_id=timezone_id,
        )
        page = context.new_page()
        _install_fixed_now(page)
        try:
            _open_course_thread(page, django_server)
            timestamp = page.locator(
                f'#qa-list > [data-comment-id="{comments["boundary"].pk}"]'
            ).get_by_test_id("comment-timestamp")
            expect(timestamp).to_have_text(expected)
            expect(timestamp).to_have_attribute("datetime", boundary.isoformat())
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_relative_thresholds_and_long_mobile_metadata_remain_readable(
    browser,
    django_server,
):
    _clear_workshops_and_courses()
    rows = [
        ("seconds", "Seconds boundary", FIXED_NOW - timedelta(seconds=30), None),
        ("minutes", "Minutes boundary", FIXED_NOW - timedelta(minutes=5), None),
        ("hours", "Hours boundary", FIXED_NOW - timedelta(hours=3), None),
        ("days", "Days boundary", FIXED_NOW - timedelta(days=5), None),
        (
            "absolute",
            '<img src=x onerror="window.commentDateXss=true">',
            FIXED_NOW - timedelta(days=30),
            None,
        ),
    ]
    long_name = "AlexandriaSupercalifragilisticCommentAuthor"
    _, viewer, comments = _seed_course_thread(author_name=long_name, rows=rows)
    context = _auth_context(
        browser,
        viewer.email,
        locale="en-US",
        timezone_id="Europe/Berlin",
        viewport={"width": 320, "height": 720},
    )
    page = context.new_page()
    _install_fixed_now(page)

    try:
        _open_course_thread(page, django_server)
        expected = {
            "seconds": "just now",
            "minutes": "5 minutes ago",
            "hours": "3 hours ago",
            "days": "5 days ago",
            "absolute": "30/01/2027",
        }
        for key, label in expected.items():
            timestamp = page.locator(
                f'#qa-list > [data-comment-id="{comments[key].pk}"]'
            ).get_by_test_id("comment-timestamp")
            expect(timestamp).to_have_text(label)

        absolute_card = page.locator(
            f'#qa-list > [data-comment-id="{comments["absolute"].pk}"]'
        )
        expect(absolute_card.get_by_text(long_name, exact=True)).to_be_visible()
        expect(absolute_card.locator("p").first).to_have_text(rows[-1][1])
        assert absolute_card.locator("img").count() == 0
        assert page.evaluate("window.commentDateXss") is None
        assert absolute_card.evaluate(
            "element => element.scrollWidth <= element.clientWidth"
        )
        timestamp_box = absolute_card.get_by_test_id(
            "comment-timestamp"
        ).bounding_box()
        assert timestamp_box is not None
        assert timestamp_box["x"] >= 0
        assert timestamp_box["x"] + timestamp_box["width"] <= 320
        _capture_qa(page, "course-comments-mobile-320-light")
    finally:
        context.close()
