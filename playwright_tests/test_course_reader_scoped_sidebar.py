"""Browser coverage for the focused course reader navigation."""

import os

import pytest

from playwright_tests.conftest import auth_context, create_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from django.db import connection

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]


@browser_journey
def test_scoped_sidebar_opens_current_topic_and_scrolls_independently(
    django_server, browser,
):
    from content.models import Course, Module, Unit

    Course.objects.filter(slug="sidebar-scroll-course").delete()
    create_user("sidebar-scroll@test.com")
    course = Course.objects.create(
        title="Sidebar scroll course",
        slug="sidebar-scroll-course",
        status="published",
        required_level=0,
        reader_navigation_scope="module",
    )
    week = Module.objects.create(
        course=course, title="Week one", slug="week-one", sort_order=1,
    )
    for index in range(1, 25):
        topic = Module.objects.create(
            course=course,
            parent=week,
            title=f"Topic {index}",
            slug=f"topic-{index}",
            sort_order=index,
        )
        Unit.objects.create(
            module=topic,
            title=f"Topic {index} lesson",
            slug="lesson",
            sort_order=1,
            body="\n\n".join(f"Lesson paragraph {number}." for number in range(80)),
        )
    connection.close()

    context = auth_context(browser, "sidebar-scroll@test.com")
    try:
        page = context.new_page()
        page.set_viewport_size({"width": 1440, "height": 768})
        page.goto(
            f"{django_server}/courses/sidebar-scroll-course/week-one/topic-2/lesson",
            wait_until="domcontentloaded",
        )
        topics = page.locator('#sidebar-nav details[data-reader-submodule]')
        assert topics.count() == 24
        assert page.locator('#sidebar-nav details[data-reader-submodule][open]').count() == 1
        assert "Topic 2" in topics.nth(1).locator("summary").inner_text()
        assert topics.nth(1).get_attribute("open") is not None

        topics.nth(0).locator("summary").click()
        page.wait_for_function(
            """() => {
                const topics = [...document.querySelectorAll('#sidebar-nav details[data-reader-submodule]')];
                return topics[0].open && !topics[1].open && topics.filter(topic => topic.open).length === 1;
            }"""
        )

        page.evaluate("document.documentElement.style.scrollBehavior = 'auto'; window.scrollTo(0, 300)")
        page.wait_for_function("window.scrollY >= 295")
        nav = page.locator("#sidebar-nav")
        metrics = nav.evaluate(
            "el => ({scrollHeight: el.scrollHeight, clientHeight: el.clientHeight})"
        )
        assert metrics["scrollHeight"] > metrics["clientHeight"]
        before_window = page.evaluate("window.scrollY")
        bounds = nav.bounding_box()
        assert bounds is not None
        page.mouse.move(bounds["x"] + bounds["width"] / 2, bounds["y"] + bounds["height"] / 2)
        page.mouse.wheel(0, 400)
        page.wait_for_function("document.querySelector('#sidebar-nav').scrollTop > 0")
        assert abs(page.evaluate("window.scrollY") - before_window) <= 1
    finally:
        context.close()
