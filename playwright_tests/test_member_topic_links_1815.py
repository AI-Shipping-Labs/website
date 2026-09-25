"""Topics body-link journeys (#1815).

Body-level relative-.md links resolve at render time against the
published slug set: clicking a resolved link lands on the target page,
and a stem with no published page renders as inert text, not an anchor.
The href-level contract is asserted authoritatively by the Django tests
(``topics.tests.test_rendering``, ``topics.tests.test_views``); these
journeys prove the rendered anchors behave in a real browser. The slugs
are unique to this file so the journeys never fight
``test_member_topics_1688.py``'s reset-style seeding.
"""

import os

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import goto_with_retry
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.core,
    pytest.mark.local_only,
]

LINKER_SLUG = "link-journey-1815"
TARGET_SLUG = "link-target-1815"
TARGET_TAIL = "this clause exists only in the stored link-target body"


def _seed_linker_topics():
    """Create the journey pages without touching other files' fixtures."""
    from topics.models import TopicPage

    TopicPage.objects.update_or_create(
        slug=TARGET_SLUG,
        defaults={
            "title": "Link Target",
            "summary": "The resolved side of the #1815 journey.",
            "body": f"A topic guide body that ends with {TARGET_TAIL}.",
        },
    )
    TopicPage.objects.update_or_create(
        slug=LINKER_SLUG,
        defaults={
            "title": "Link Journey",
            "summary": "Links a published stem and a missing stem.",
            "body": (
                "Read [Link Target](link-target-1815.md), then note the "
                "[Missing Page](missing-stem-1815.md) reference."
            ),
        },
    )


@browser_journey
def test_anonymous_follows_resolved_body_link_to_its_topic(
    browser, django_server,
):
    """Anonymous: a resolved body link navigates to the target topic."""
    _seed_linker_topics()
    page = browser.new_context().new_page()

    response = goto_with_retry(page, f"{django_server}/topics/{LINKER_SLUG}/")
    assert response.status == 200
    body = page.get_by_test_id("topic-body")
    expect(body).to_be_visible()

    link = body.get_by_role("link", name="Link Target")
    expect(link).to_have_attribute("href", f"/topics/{TARGET_SLUG}/")
    link.click()
    page.wait_for_load_state("domcontentloaded")
    assert f"/topics/{TARGET_SLUG}/" in page.url
    expect(page.get_by_test_id("topic-body")).to_be_visible()
    assert TARGET_TAIL in page.content()


@browser_journey
def test_unresolvable_body_link_renders_as_plain_text(browser, django_server):
    """Anonymous: a stem with no published page is inert text, no anchor."""
    _seed_linker_topics()
    page = browser.new_context().new_page()

    response = goto_with_retry(page, f"{django_server}/topics/{LINKER_SLUG}/")
    assert response.status == 200
    body = page.get_by_test_id("topic-body")
    expect(body).to_be_visible()
    assert "Missing Page" in (body.text_content() or "")
    assert body.get_by_role("link", name="Missing Page").count() == 0
