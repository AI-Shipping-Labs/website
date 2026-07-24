"""Interview hub empty-state and populated coverage for issue #1316.

The Interview Prep hub is a top-nav Resources destination. It must always
return 200 and render a member empty state when no categories exist, instead
of hard-404ing and turning a live nav item into a dead link.
"""

import os

import pytest
from playwright.sync_api import expect

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [
    pytest.mark.local_only,
    pytest.mark.core,
    pytest.mark.django_db(transaction=True),
]


def _clear_categories():
    from django.db import connection

    from content.models import InterviewCategory

    InterviewCategory.objects.all().delete()
    connection.close()


def _create_category(slug, title, description, *, status=""):
    from content.models import InterviewCategory

    return InterviewCategory.objects.create(
        slug=slug,
        title=title,
        description=description,
        status=status,
        sections_json=[
            {
                "id": "section-1",
                "title": "1. Section",
                "intro": "Intro copy.",
                "qa": [{"question": f"Sample question for {title}?"}],
            },
        ],
        body_markdown="## Intro\n\nBody copy.\n",
    )


def _close_connection():
    from django.db import connection

    connection.close()


def test_empty_interview_hub_returns_200_with_empty_state(
    django_server, django_db_blocker, page
):
    with django_db_blocker.unblock():
        _clear_categories()

    response = page.goto(
        f"{django_server}/interview", wait_until="domcontentloaded"
    )
    assert response is not None
    assert response.status == 200

    expect(
        page.get_by_role("heading", name="AI Engineer Interview Questions")
    ).to_be_visible()
    expect(
        page.locator("main").get_by_text("Interview Prep", exact=True)
    ).to_be_visible()
    expect(page.get_by_test_id("member-empty-state")).to_be_visible()
    expect(page.get_by_text("No interview questions yet")).to_be_visible()

    page.get_by_role("link", name="Back to home").click()
    page.wait_for_url(f"{django_server}/", timeout=5000)


def test_resources_nav_interview_link_is_not_a_dead_link_when_empty(
    django_server, django_db_blocker, page
):
    with django_db_blocker.unblock():
        _clear_categories()

    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    page.locator('[data-testid="nav-resources-trigger"]').hover()
    interview_link = page.locator('[data-testid="nav-resources-link-interview"]')
    interview_link.wait_for(state="visible", timeout=3000)
    response = page.request.get(f"{django_server}/interview")
    assert response.status == 200

    interview_link.click()
    page.wait_for_url(f"{django_server}/interview", timeout=5000)
    expect(page.get_by_test_id("member-empty-state")).to_be_visible()
    expect(page.get_by_text("No interview questions yet")).to_be_visible()


def test_populated_interview_hub_shows_grid_and_links_to_detail(
    django_server, django_db_blocker, page
):
    with django_db_blocker.unblock():
        _clear_categories()
        _create_category(
            "theory",
            "Theory Interview Questions",
            "Prepare for theory questions.",
        )
        _create_category(
            "coding",
            "Coding Interview Questions",
            "Prepare for coding questions.",
        )
        _close_connection()

    page.goto(f"{django_server}/interview", wait_until="domcontentloaded")

    expect(page.get_by_text("Theory Interview Questions")).to_be_visible()
    expect(page.get_by_text("Coding Interview Questions")).to_be_visible()
    assert page.get_by_test_id("member-empty-state").count() == 0

    page.get_by_role(
        "link", name="Theory Interview Questions"
    ).click()
    page.wait_for_url(f"{django_server}/interview/theory", timeout=5000)
    expect(
        page.get_by_text("Sample question for Theory Interview Questions?")
    ).to_be_visible()


def test_single_category_still_renders_grid_not_empty_state(
    django_server, django_db_blocker, page
):
    with django_db_blocker.unblock():
        _clear_categories()
        _create_category(
            "theory",
            "Theory Interview Questions",
            "Prepare for theory questions.",
        )
        _close_connection()

    page.goto(f"{django_server}/interview", wait_until="domcontentloaded")

    expect(page.get_by_text("Theory Interview Questions")).to_be_visible()
    assert page.get_by_test_id("member-empty-state").count() == 0
