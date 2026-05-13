"""Issue #403 public page CTA hierarchy scenarios."""

import re

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import ensure_site_config_tiers, ensure_tiers


def _goto(page, django_server, path):
    page.goto(f"{django_server}{path}", wait_until="domcontentloaded")
    expect(page.locator("body")).not_to_contain_text("Not Found")


@pytest.mark.django_db(transaction=True)
def test_homepage_browse_first_hierarchy(django_server, page):
    ensure_tiers()
    ensure_site_config_tiers()

    _goto(page, django_server, "/")

    hero = page.locator("section").first
    expect(hero.get_by_role("heading", name=re.compile("Turn AI ideas"))).to_be_visible()
    primary_cta = hero.get_by_role("link", name="View Membership Tiers")
    expect(primary_cta).to_be_visible()
    expect(primary_cta).to_have_attribute("href", "/#tiers")
    expect(hero.get_by_role("link", name="Browse Resources")).to_have_attribute(
        "href", "/resources"
    )
    assert page.locator("form.subscribe-form").count() == 1


@pytest.mark.django_db(transaction=True)
def test_pricing_focuses_on_tier_comparison(django_server, page):
    ensure_tiers()

    _goto(page, django_server, "/pricing")

    tier_grid = page.locator("div.grid.sm\\:grid-cols-2.lg\\:grid-cols-4")
    expect(tier_grid).to_be_visible()
    before_grid_newsletter = page.locator(
        "section#pricing-section form.subscribe-form"
    )
    assert before_grid_newsletter.count() == 0


@pytest.mark.django_db(transaction=True)
def test_resources_courses_and_workshops_start_with_browse_content(
    django_server, page
):
    ensure_tiers()

    for path, heading in (
        ("/resources", "Workshops, Courses & More"),
        ("/courses", "Structured Learning Paths"),
        ("/workshops", "Hands-on Workshops"),
    ):
        _goto(page, django_server, path)
        main = page.locator("main")
        expect(main.get_by_role("heading", name=heading)).to_be_visible()
        assert main.locator("form.subscribe-form").count() == 0
