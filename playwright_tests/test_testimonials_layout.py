"""Playwright coverage for testimonial layout polish (Issue #430)."""

import os
from pathlib import Path

import pytest

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


SCREENSHOT_DIR = Path("playwright_tests/screenshots/issue-430")


def _ensure_testimonial_course():
    """Create a published course with testimonials for screenshot coverage."""
    from django.db import connection

    from content.models import Course, Module, Unit

    course, _ = Course.objects.update_or_create(
        slug="testimonial-layout",
        defaults={
            "title": "Testimonial Layout Course",
            "description": "A course used to verify testimonial layout.",
            "status": "published",
            "required_level": 0,
            "testimonials": [
                {
                    "quote": (
                        "The lessons gave me a practical path from rough "
                        "prototype to evaluated AI workflow."
                    ),
                    "name": "Ada Lovelace",
                    "role": "Principal Applied AI Systems Reliability Engineer",
                    "company": "Very Long Company Name for Enterprise Research Platforms",
                    "source_url": "https://example.com/ada",
                },
                {
                    "quote": (
                        "Clear examples, useful structure, and enough depth "
                        "to make the work stick."
                    ),
                    "name": "Grace Hopper",
                    "role": "Engineering Lead",
                    "company": "Example Labs",
                },
            ],
        },
    )
    module, _ = Module.objects.get_or_create(
        course=course,
        slug="intro",
        defaults={"title": "Intro", "sort_order": 1},
    )
    Unit.objects.get_or_create(
        module=module,
        slug="welcome",
        defaults={"title": "Welcome", "sort_order": 1},
    )
    connection.close()


def _assert_no_horizontal_overflow(page, selector):
    overflows = page.locator(selector).evaluate_all(
        """els => els.filter(el => el.scrollWidth > el.clientWidth + 1).length"""
    )
    assert overflows == 0


def _screenshot_section(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    section = page.locator('[data-testid="testimonial-grid"]').first
    page.add_style_tag(
        content="header, #section-nav { visibility: hidden !important; }"
    )
    section.evaluate(
        "el => window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - 140)"
    )
    section.screenshot(path=SCREENSHOT_DIR / f"{name}.png")


@pytest.mark.django_db
def test_homepage_testimonials_desktop_and_mobile_screenshots(django_server, page):
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(f"{django_server}/", wait_until="networkidle")
    grid = page.locator('[data-testid="testimonial-grid"]').first
    cards = page.locator('[data-testid="testimonial-card"]')

    assert grid.is_visible()
    assert cards.count() >= 4
    assert grid.evaluate("el => getComputedStyle(el).display") == "grid"
    first_four_x_positions = cards.evaluate_all(
        "els => els.slice(0, 4).map(el => el.getBoundingClientRect().x)"
    )
    assert len({round(x) for x in first_four_x_positions}) == 2
    _assert_no_horizontal_overflow(page, '[data-testid="testimonial-card"]')
    _screenshot_section(page, "homepage-desktop")

    page.set_viewport_size({"width": 390, "height": 900})
    page.goto(f"{django_server}/", wait_until="networkidle")
    _assert_no_horizontal_overflow(page, '[data-testid="testimonial-card"]')
    _screenshot_section(page, "homepage-mobile")


@pytest.mark.django_db(transaction=True)
def test_course_testimonials_shared_layout_and_source_link(django_server, page):
    _ensure_testimonial_course()

    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(f"{django_server}/courses/testimonial-layout", wait_until="networkidle")
    grid = page.locator('[data-testid="testimonial-grid"]').first
    cards = page.locator('[data-testid="testimonial-card"]')

    assert grid.is_visible()
    assert cards.count() == 2
    assert grid.evaluate("el => getComputedStyle(el).display") == "grid"
    _assert_no_horizontal_overflow(page, '[data-testid="testimonial-card"]')

    source_link = page.locator('[data-testid="testimonial-author"] a').first
    assert source_link.get_attribute("href") == "https://example.com/ada"
    source_link.focus()
    assert source_link.evaluate("el => document.activeElement === el")
    _screenshot_section(page, "course-desktop")

    page.set_viewport_size({"width": 390, "height": 900})
    page.goto(f"{django_server}/courses/testimonial-layout", wait_until="networkidle")
    _assert_no_horizontal_overflow(page, '[data-testid="testimonial-card"]')
    _screenshot_section(page, "course-mobile")
