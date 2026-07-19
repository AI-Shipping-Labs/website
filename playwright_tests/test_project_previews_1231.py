"""Project preview card journeys for issue #1231."""

import base64
import datetime
import os

import pytest
from playwright.sync_api import expect

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only

COVER_URL = "https://cdn.example.com/project-cover-1231.png"
CUSTOM_URL = "https://cdn.example.com/project-custom-1231.png"
AUTO_URL = "https://cdn.example.com/project-auto-1231.png"
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
    "AScY42YAAAAASUVORK5CYII="
)


def _reset_projects():
    from django.db import connection

    from content.models import Project

    Project.objects.all().delete()
    connection.close()


def _create_project(title, slug, *, day=1, **kwargs):
    from django.db import connection

    from content.models import Project

    defaults = {
        "description": f"{title} description.",
        "content_markdown": f"# {title}\n\nExisting project content remains intact.",
        "date": datetime.date(2026, 7, day),
        "author": "AI Shipping Labs",
        "difficulty": "beginner",
        "tags": ["agents", "python"],
        "published": True,
    }
    defaults.update(kwargs)
    project = Project.objects.create(title=title, slug=slug, **defaults)
    connection.close()
    return project


def _open(page, django_server, path):
    response = page.goto(
        f"{django_server}{path}",
        wait_until="domcontentloaded",
    )
    assert response is not None
    assert response.status == 200


def _serve_images(page, *urls):
    """Keep preview precedence checks independent of external networking."""
    for url in urls:
        page.route(
            url,
            lambda route: route.fulfill(
                status=200, content_type="image/png", body=TINY_PNG,
            ),
        )


def _project_card(page, title):
    return page.get_by_test_id("project-card").filter(has_text=title)


def _preview(card):
    return card.get_by_test_id("project-card-preview")


def _assert_same_row(first, second):
    first_preview = _preview(first).bounding_box()
    second_preview = _preview(second).bounding_box()
    first_title = first.get_by_role("heading").bounding_box()
    second_title = second.get_by_role("heading").bounding_box()
    assert first_preview is not None
    assert second_preview is not None
    assert first_title is not None
    assert second_title is not None
    assert abs(first_preview["height"] - second_preview["height"]) <= 1
    assert abs(first_title["y"] - second_title["y"]) <= 1


@pytest.mark.visual_regression
@pytest.mark.django_db(transaction=True)
def test_visitor_compares_covered_and_coverless_projects_without_grid_jump(
    django_server, page,
):
    _reset_projects()
    covered = _create_project(
        "Covered Grid Project 1231",
        "covered-grid-project-1231",
        day=2,
        cover_image_url=COVER_URL,
    )
    coverless = _create_project(
        "Coverless Grid Project 1231",
        "coverless-grid-project-1231",
        day=1,
    )
    page.set_viewport_size({"width": 1440, "height": 1000})
    _serve_images(page, COVER_URL)

    _open(page, django_server, "/projects")
    covered_card = _project_card(page, covered.title)
    coverless_card = _project_card(page, coverless.title)
    expect(_preview(covered_card).get_by_role("img")).to_have_attribute(
        "src", COVER_URL,
    )
    expect(
        _preview(coverless_card).get_by_test_id(
            "project-card-preview-fallback",
        )
    ).to_be_visible()
    _assert_same_row(covered_card, coverless_card)

    coverless_card.locator("a").first.click()
    page.wait_for_url(f"{django_server}{coverless.get_absolute_url()}")
    expect(
        page.locator("article header").get_by_role(
            "heading", name=coverless.title,
        )
    ).to_be_visible()


@pytest.mark.django_db(transaction=True)
def test_visitor_recognizes_operator_selected_custom_project_banner(
    django_server, page,
):
    _reset_projects()
    project = _create_project(
        "Custom Banner Project 1231",
        "custom-banner-project-1231",
        custom_banner_url=CUSTOM_URL,
        auto_banner_url=AUTO_URL,
    )
    _serve_images(page, CUSTOM_URL, AUTO_URL)

    _open(page, django_server, "/projects")
    card = _project_card(page, project.title)
    expect(_preview(card).get_by_role("img")).to_have_attribute(
        "src", CUSTOM_URL,
    )
    expect(card.locator(f'img[src="{AUTO_URL}"]')).to_have_count(0)
    expect(
        card.get_by_test_id("project-card-preview-fallback"),
    ).to_be_hidden()

    card.locator("a").first.click()
    page.wait_for_url(f"{django_server}{project.get_absolute_url()}")
    expect(page.get_by_test_id("project-body")).to_contain_text(
        "Existing project content remains intact.",
    )


@pytest.mark.django_db(transaction=True)
def test_visitor_keeps_generated_preview_after_difficulty_filter(
    django_server, page,
):
    _reset_projects()
    project = _create_project(
        "Generated Banner Project 1231",
        "generated-banner-project-1231",
        difficulty="intermediate",
        auto_banner_url=AUTO_URL,
    )
    _create_project(
        "Other Difficulty Project 1231",
        "other-difficulty-project-1231",
        day=2,
        difficulty="advanced",
    )
    _serve_images(page, AUTO_URL)

    _open(page, django_server, "/projects")
    card = _project_card(page, project.title)
    expect(_preview(card).get_by_role("img")).to_have_attribute(
        "src", AUTO_URL,
    )
    page.get_by_role("link", name="intermediate", exact=True).click()
    page.wait_for_url(f"{django_server}/projects?difficulty=intermediate")
    filtered_card = _project_card(page, project.title)
    expect(_preview(filtered_card).get_by_role("img")).to_have_attribute(
        "src", AUTO_URL,
    )
    expect(filtered_card.locator("a").first).to_have_attribute(
        "href", project.get_absolute_url(),
    )


@pytest.mark.django_db(transaction=True)
def test_frontmatter_cover_remains_highest_priority_project_preview(
    django_server, page,
):
    _reset_projects()
    project = _create_project(
        "Cover Priority Project 1231",
        "cover-priority-project-1231",
        cover_image_url=COVER_URL,
        custom_banner_url=CUSTOM_URL,
        auto_banner_url=AUTO_URL,
    )
    _serve_images(page, COVER_URL, CUSTOM_URL, AUTO_URL)

    _open(page, django_server, "/projects")
    card = _project_card(page, project.title)
    expect(_preview(card).get_by_role("img")).to_have_attribute(
        "src", COVER_URL,
    )
    expect(card.locator(f'img[src="{CUSTOM_URL}"]')).to_have_count(0)
    expect(card.locator(f'img[src="{AUTO_URL}"]')).to_have_count(0)

    card.locator("a").first.click()
    page.wait_for_url(f"{django_server}{project.get_absolute_url()}")
    expect(
        page.locator("article header").get_by_role(
            "heading", name=project.title,
        )
    ).to_be_visible()


@pytest.mark.django_db(transaction=True)
def test_reader_sees_shared_project_badge_before_existing_detail_metadata(
    django_server, page,
):
    _reset_projects()
    project = _create_project(
        "Shared Detail Badge Project 1231",
        "shared-detail-badge-project-1231",
        author="Project Builder",
        description="Readable detail description 1231.",
        difficulty="advanced",
    )

    _open(page, django_server, project.get_absolute_url())
    header = page.locator("article header")
    badge = header.locator('[data-component="member-badge"]').filter(
        has_text="Project",
    )
    expect(badge).to_be_visible()
    expect(badge.locator("svg.lucide-rocket")).to_have_count(1)
    expect(header.get_by_role("heading", name=project.title)).to_be_visible()
    expect(header).to_contain_text("by Project Builder")
    expect(header).to_contain_text("Readable detail description 1231.")
    expect(header).to_contain_text("advanced")
    badge_precedes_title = header.evaluate(
        """header => {
            const badge = header.querySelector('[data-component="member-badge"]');
            const title = header.querySelector('h1');
            return Boolean(
                badge.compareDocumentPosition(title)
                & Node.DOCUMENT_POSITION_FOLLOWING
            );
        }"""
    )
    assert badge_precedes_title


@pytest.mark.visual_regression
@pytest.mark.django_db(transaction=True)
def test_difficulty_filter_preserves_preview_structure_and_gap(
    django_server, page,
):
    _reset_projects()
    covered = _create_project(
        "Filtered Covered Project 1231",
        "filtered-covered-project-1231",
        day=4,
        difficulty="beginner",
        cover_image_url=COVER_URL,
    )
    fallback = _create_project(
        "Filtered Fallback Project 1231",
        "filtered-fallback-project-1231",
        day=3,
        difficulty="beginner",
    )
    _create_project(
        "Filtered Custom Project 1231",
        "filtered-custom-project-1231",
        day=2,
        difficulty="advanced",
        custom_banner_url=CUSTOM_URL,
    )
    _create_project(
        "Filtered Auto Project 1231",
        "filtered-auto-project-1231",
        day=1,
        difficulty="advanced",
        auto_banner_url=AUTO_URL,
    )
    page.set_viewport_size({"width": 1440, "height": 1000})

    _open(page, django_server, "/projects")
    page.get_by_role("link", name="beginner", exact=True).click()
    page.wait_for_url(f"{django_server}/projects?difficulty=beginner")
    covered_card = _project_card(page, covered.title)
    fallback_card = _project_card(page, fallback.title)
    expect(page.get_by_test_id("project-card")).to_have_count(2)
    expect(covered_card.locator("a").first).to_have_attribute(
        "href", covered.get_absolute_url(),
    )
    expect(fallback_card.locator("a").first).to_have_attribute(
        "href", fallback.get_absolute_url(),
    )
    _assert_same_row(covered_card, fallback_card)
    grid_gap = page.get_by_test_id("project-card").first.evaluate(
        "card => getComputedStyle(card.parentElement).gap"
    )
    assert grid_gap == "24px"

    page.get_by_role("link", name="Clear filter", exact=True).click()
    page.wait_for_url(f"{django_server}/projects")
    expect(page.get_by_test_id("project-card")).to_have_count(4)
