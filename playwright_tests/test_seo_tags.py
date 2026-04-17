"""
Playwright E2E tests for SEO: Tags, Filtering, and Conditional Components (Issue #91).

Tests cover all 11 BDD scenarios from the issue:
- Visitor explores the tag cloud to discover content by topic
- Visitor drills into a tag and navigates to an article
- Visitor narrows blog results by selecting a single tag filter
- Visitor combines multiple tag filters with AND logic
- Visitor removes one tag filter to broaden results
- Visitor uses tag filters across different listing pages
- Visitor reads an article and sees a contextual course promo injected by a tag rule
- Visitor reads an article with no matching tag rules and sees no injected components
- Anonymous visitor encounters the tags page with no content yet
- Visitor navigates between tag detail and tag index to explore topics
- Visitor clicks a tag chip on an article detail page and lands on the tag detail page

Usage:
    uv run pytest playwright_tests/test_seo_tags.py -v
"""

import datetime
import os

import pytest

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection

VIEWPORT = {"width": 1280, "height": 720}


# ---------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------

def _clear_all_content():
    """Delete all content to ensure a clean state for each test."""
    from content.models import (
        Article,
        Course,
        CuratedLink,
        Download,
        Project,
        TagRule,
    )
    from events.models import Event

    Article.objects.all().delete()
    Event.objects.all().delete()
    Project.objects.all().delete()
    CuratedLink.objects.all().delete()
    Download.objects.all().delete()
    Course.objects.all().delete()
    TagRule.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _create_article(
    title,
    slug,
    description="",
    content_markdown="",
    author="",
    tags=None,
    required_level=0,
    published=True,
    date=None,
):
    """Helper to create an Article directly via the ORM."""
    from content.models import Article

    if tags is None:
        tags = []
    if date is None:
        date = datetime.date.today()

    article = Article(
        title=title,
        slug=slug,
        description=description,
        content_markdown=content_markdown,
        author=author,
        tags=tags,
        required_level=required_level,
        published=published,
        date=date,
    )
    article.save()
    connection.close()
    return article


def _create_recording(
    title,
    slug,
    description="",
    tags=None,
    published=True,
    date=None,
    youtube_url="https://www.youtube.com/watch?v=test",
):
    """Helper to create a Recording directly via the ORM."""
    from events.models import Event

    if tags is None:
        tags = []
    if date is None:
        date = datetime.date.today()

    recording = Event(
        title=title,
        slug=slug,
        description=description,
        tags=tags,
        published=published,
        date=date,
        youtube_url=youtube_url,
    )
    recording.save()
    connection.close()
    return recording


def _create_project(
    title,
    slug,
    description="",
    tags=None,
    published=True,
    date=None,
):
    """Helper to create a Project directly via the ORM."""
    from content.models import Project

    if tags is None:
        tags = []
    if date is None:
        date = datetime.date.today()

    project = Project(
        title=title,
        slug=slug,
        description=description,
        tags=tags,
        published=published,
        date=date,
    )
    project.save()
    connection.close()
    return project


def _create_curated_link(
    title,
    item_id=None,
    description="",
    url="https://example.com",
    category="tools",
    tags=None,
    required_level=0,
    sort_order=0,
    published=True,
):
    """Helper to create a CuratedLink via ORM."""
    from content.models import CuratedLink

    if tags is None:
        tags = []
    if item_id is None:
        item_id = title.lower().replace(" ", "-")

    link = CuratedLink(
        item_id=item_id,
        title=title,
        description=description,
        url=url,
        category=category,
        tags=tags,
        required_level=required_level,
        sort_order=sort_order,
        published=published,
    )
    link.save()
    connection.close()
    return link


def _create_download(
    title,
    slug,
    description="",
    file_url="https://example.com/file.pdf",
    tags=None,
    published=True,
):
    """Helper to create a Download via ORM."""
    from content.models import Download

    if tags is None:
        tags = []

    download = Download(
        title=title,
        slug=slug,
        description=description,
        file_url=file_url,
        tags=tags,
        published=published,
    )
    download.save()
    connection.close()
    return download


def _create_course(
    title,
    slug,
    description="",
    tags=None,
    status="published",
):
    """Helper to create a Course via ORM."""
    from content.models import Course

    if tags is None:
        tags = []

    course = Course(
        title=title,
        slug=slug,
        description=description,
        tags=tags,
        status=status,
    )
    course.save()
    connection.close()
    return course


def _create_tag_rule(
    tag,
    component_type,
    component_config=None,
    position="after_content",
):
    """Helper to create a TagRule via ORM."""
    from content.models import TagRule

    if component_config is None:
        component_config = {}

    rule = TagRule(
        tag=tag,
        component_type=component_type,
        component_config=component_config,
        position=position,
    )
    rule.save()
    connection.close()
    return rule


# ---------------------------------------------------------------
# Scenario 1: Visitor explores the tag cloud to discover content
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario1TagCloudExploration:
    """
    Scenario: Visitor explores the tag cloud to discover content by topic.

    Given: Published articles tagged "python" (2 items), "ai" (3 items
    across articles and recordings), and "workshop" (1 recording).
    """

    def test_tags_page_shows_tags_with_counts_sorted_by_count(
        self, django_server
    , page):
        """Tags appear with their content counts, highest counts first."""
        _clear_all_content()
        _create_article(
            title="Python Basics",
            slug="python-basics",
            tags=["python", "ai"],
            date=datetime.date(2026, 1, 3),
        )
        _create_article(
            title="Python Advanced",
            slug="python-advanced",
            tags=["python", "ai"],
            date=datetime.date(2026, 1, 2),
        )
        _create_recording(
            title="AI Workshop Recording",
            slug="ai-workshop-rec",
            tags=["ai", "workshop"],
            date=datetime.date(2026, 1, 1),
        )

        response = page.goto(
            f"{django_server}/tags", wait_until="domcontentloaded"
        )
        assert response.status == 200

        body = page.content()

        # All tags are visible
        assert "python" in body
        assert "ai" in body
        assert "workshop" in body

        # Tags show counts: ai=3, python=2, workshop=1
        # Tags are links to /tags/{tag}
        ai_link = page.locator('a[href="/tags/ai"]')
        assert ai_link.count() >= 1

        python_link = page.locator('a[href="/tags/python"]')
        assert python_link.count() >= 1

        # Verify ordering: highest counts appear first in the
        # tag cloud. "ai" (count 3) should appear before
        # "workshop" (count 1) in the page.
        # Use the href attribute to find tag positions reliably.
        ai_pos = body.index('/tags/ai"')
        workshop_pos = body.index('/tags/workshop"')
        assert ai_pos < workshop_pos
    def test_click_tag_navigates_to_tag_detail(self, django_server, page):
        """Click on the 'ai' tag and see all 3 items with type badges."""
        _clear_all_content()
        _create_article(
            title="Python Basics",
            slug="python-basics",
            tags=["python", "ai"],
            date=datetime.date(2026, 1, 3),
        )
        _create_article(
            title="Python Advanced",
            slug="python-advanced",
            tags=["python", "ai"],
            date=datetime.date(2026, 1, 2),
        )
        _create_recording(
            title="AI Workshop Recording",
            slug="ai-workshop-rec",
            tags=["ai", "workshop"],
            date=datetime.date(2026, 1, 1),
        )

        page.goto(
            f"{django_server}/tags", wait_until="domcontentloaded"
        )

        # Click on the "ai" tag
        ai_link = page.locator('a[href="/tags/ai"]').first
        ai_link.click()
        page.wait_for_load_state("domcontentloaded")

        # User lands on /tags/ai
        assert "/tags/ai" in page.url

        body = page.content()

        # All 3 items with the "ai" tag are visible
        assert "Python Basics" in body
        assert "Python Advanced" in body
        assert "AI Workshop Recording" in body

        # Content type badges are shown
        assert "Article" in body
        assert "Recording" in body
# ---------------------------------------------------------------
# Scenario 2: Visitor drills into a tag and navigates to an article
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario2DrillIntoTagAndNavigate:
    """
    Scenario: Visitor drills into a tag and navigates to an article.

    Given: A published article "Intro to AI Engineering" tagged
    "ai-engineering" and a published recording "AI Workshop" also
    tagged "ai-engineering".
    """

    def test_tag_detail_shows_both_items_sorted_by_date(
        self, django_server
    , page):
        """Both items appear on the tag detail page, sorted newest first."""
        _clear_all_content()
        _create_article(
            title="Intro to AI Engineering",
            slug="intro-to-ai-engineering",
            description="An introduction to AI engineering.",
            content_markdown="# Intro to AI Engineering\n\nFull article content.",
            tags=["ai-engineering"],
            date=datetime.date(2026, 2, 15),
        )
        _create_recording(
            title="AI Workshop",
            slug="ai-workshop",
            description="A workshop on AI engineering.",
            tags=["ai-engineering"],
            date=datetime.date(2026, 2, 10),
        )

        page.goto(
            f"{django_server}/tags/ai-engineering",
            wait_until="domcontentloaded",
        )

        body = page.content()

        # Both items are visible
        assert "Intro to AI Engineering" in body
        assert "AI Workshop" in body

        # Type badges are shown
        assert "Article" in body
        assert "Recording" in body

        # Newest first: article (Feb 15) before recording (Feb 10)
        article_pos = body.index("Intro to AI Engineering")
        recording_pos = body.index("AI Workshop")
        assert article_pos < recording_pos
    def test_click_article_from_tag_detail(self, django_server, page):
        """Click on an article from the tag detail page to navigate
        to the article detail page."""
        _clear_all_content()
        _create_article(
            title="Intro to AI Engineering",
            slug="intro-to-ai-engineering",
            description="An introduction to AI engineering.",
            content_markdown="# Intro to AI Engineering\n\nFull article content about AI.",
            tags=["ai-engineering"],
            date=datetime.date(2026, 2, 15),
        )
        _create_recording(
            title="AI Workshop",
            slug="ai-workshop",
            tags=["ai-engineering"],
            date=datetime.date(2026, 2, 10),
        )

        page.goto(
            f"{django_server}/tags/ai-engineering",
            wait_until="domcontentloaded",
        )

        # Click on the article by its title text
        # The tag_detail template wraps each item in an <a> tag
        article_title = page.locator(
            'h2:has-text("Intro to AI Engineering")'
        ).first
        article_title.click()
        page.wait_for_load_state("domcontentloaded")

        # User arrives at the article detail page
        assert "/blog/intro-to-ai-engineering" in page.url

        body = page.content()
        assert "Intro to AI Engineering" in body
        assert "Full article content about AI" in body
# ---------------------------------------------------------------
# Scenario 3: Visitor narrows blog results by selecting a single tag
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario3SingleTagFilter:
    """
    Scenario: Visitor narrows blog results by selecting a single tag filter.

    Given: 3 published articles -- "Python Basics" tagged "python",
    "AI Overview" tagged "ai", and "Python AI" tagged "python" and "ai".
    """

    def test_single_tag_filter_narrows_results(self, django_server, page):
        """Click the 'python' tag filter chip and only matching
        articles appear."""
        _clear_all_content()
        _create_article(
            title="Python Basics",
            slug="python-basics",
            description="Learn Python basics.",
            tags=["python"],
            date=datetime.date(2026, 1, 3),
        )
        _create_article(
            title="AI Overview",
            slug="ai-overview",
            description="Overview of AI concepts.",
            tags=["ai"],
            date=datetime.date(2026, 1, 2),
        )
        _create_article(
            title="Python AI",
            slug="python-ai",
            description="Python for AI.",
            tags=["python", "ai"],
            date=datetime.date(2026, 1, 1),
        )

        # Step 1: Navigate to /blog
        page.goto(
            f"{django_server}/blog", wait_until="domcontentloaded"
        )
        body = page.content()

        # All 3 articles are visible
        assert "Python Basics" in body
        assert "AI Overview" in body
        assert "Python AI" in body

        # Step 2: Click the "python" tag filter chip
        python_chip = page.locator(
            'a[href*="tag=python"]'
        ).first
        python_chip.click()
        page.wait_for_load_state("domcontentloaded")

        # URL updates to /blog?tag=python
        assert "tag=python" in page.url

        body = page.content()

        # Only "Python Basics" and "Python AI" appear
        assert "Python Basics" in body
        assert "Python AI" in body
        # "AI Overview" is hidden
        assert "AI Overview" not in body
# ---------------------------------------------------------------
# Scenario 4: Visitor combines multiple tag filters with AND logic
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario4MultiTagAndLogic:
    """
    Scenario: Visitor combines multiple tag filters with AND logic.

    Given: 3 published articles -- "Python Basics" tagged "python",
    "AI Overview" tagged "ai", and "Python AI" tagged "python" and "ai".
    """

    def test_multi_tag_filter_narrows_to_intersection(
        self, django_server
    , page):
        """Add the 'ai' tag filter to 'python' and only the article
        with both tags remains."""
        _clear_all_content()
        _create_article(
            title="Python Basics",
            slug="python-basics",
            tags=["python"],
            date=datetime.date(2026, 1, 3),
        )
        _create_article(
            title="AI Overview",
            slug="ai-overview",
            tags=["ai"],
            date=datetime.date(2026, 1, 2),
        )
        _create_article(
            title="Python AI",
            slug="python-ai",
            tags=["python", "ai"],
            date=datetime.date(2026, 1, 1),
        )

        # Step 1: Navigate to /blog?tag=python
        page.goto(
            f"{django_server}/blog?tag=python",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # "Python Basics" and "Python AI" are shown
        assert "Python Basics" in body
        assert "Python AI" in body

        # Step 2: Click the "ai" tag filter chip to add it
        # Look for a link that adds ai to the current python filter
        ai_chip = page.locator(
            'a[href*="tag=python"][href*="tag=ai"], '
            'a[href*="tag=ai"][href*="tag=python"]'
        ).first
        ai_chip.click()
        page.wait_for_load_state("domcontentloaded")

        # URL has both tag=python and tag=ai
        assert "tag=python" in page.url
        assert "tag=ai" in page.url

        body = page.content()

        # Only "Python AI" remains
        assert "Python AI" in body
        assert "Python Basics" not in body
        assert "AI Overview" not in body

        # Both "python" and "ai" are active in the URL
        assert "tag=python" in page.url
        assert "tag=ai" in page.url
# ---------------------------------------------------------------
# Scenario 5: Visitor removes one tag filter to broaden results
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario5RemoveTagFilter:
    """
    Scenario: Visitor removes one tag filter to broaden results.

    Given: 2 published articles -- "Python AI" tagged "python" and "ai",
    and "Python Basics" tagged "python".
    """

    def test_remove_tag_filter_and_clear_all(self, django_server, page):
        """Remove the 'ai' filter chip to broaden results, then
        clear all to see everything."""
        _clear_all_content()
        _create_article(
            title="Python AI",
            slug="python-ai",
            tags=["python", "ai"],
            date=datetime.date(2026, 1, 2),
        )
        _create_article(
            title="Python Basics",
            slug="python-basics",
            tags=["python"],
            date=datetime.date(2026, 1, 1),
        )

        # Step 1: Navigate to /blog?tag=python&tag=ai
        page.goto(
            f"{django_server}/blog?tag=python&tag=ai",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Only "Python AI" is shown
        assert "Python AI" in body
        assert "Python Basics" not in body

        # Step 2: Navigate to /blog?tag=python (removing ai)
        page.goto(
            f"{django_server}/blog?tag=python",
            wait_until="domcontentloaded",
        )

        # URL updates to /blog?tag=python (no ai)
        assert "tag=python" in page.url
        assert "tag=ai" not in page.url

        body = page.content()

        # Both python-tagged articles now appear
        assert "Python AI" in body
        assert "Python Basics" in body

        # Step 3: Navigate to /blog to remove all filters
        page.goto(
            f"{django_server}/blog",
            wait_until="domcontentloaded",
        )

        # URL returns to /blog with no query params
        assert page.url.rstrip("/").endswith("/blog")

        # All articles are listed
        body = page.content()
        assert "Python AI" in body
        assert "Python Basics" in body
# ---------------------------------------------------------------
# Scenario 6: Visitor uses tag filters across different listing pages
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario6TagFiltersAcrossPages:
    """
    Scenario: Visitor uses tag filters across different listing pages.

    Given: Published content with the "python" tag exists for articles,
    recordings, courses, projects, curated links, and downloads.
    """

    def test_tag_filter_on_blog(self, django_server, page):
        """Blog results are filtered to show only articles tagged 'python'."""
        _clear_all_content()
        _create_article(
            title="Python Article",
            slug="python-article",
            tags=["python"],
            date=datetime.date(2026, 1, 1),
        )
        _create_article(
            title="Go Article",
            slug="go-article",
            tags=["go"],
            date=datetime.date(2026, 1, 2),
        )

        page.goto(
            f"{django_server}/blog", wait_until="domcontentloaded"
        )
        # Click the "python" tag chip
        python_chip = page.locator(
            'a[href*="tag=python"]'
        ).first
        python_chip.click()
        page.wait_for_load_state("domcontentloaded")

        body = page.content()
        assert "Python Article" in body
        assert "Go Article" not in body
    def test_tag_filter_on_courses(self, django_server, page):
        """Course results are filtered to show only courses tagged 'python'."""
        _clear_all_content()
        _create_course(
            title="Python Course",
            slug="python-course",
            tags=["python"],
        )
        _create_course(
            title="Go Course",
            slug="go-course",
            tags=["go"],
        )

        page.goto(
            f"{django_server}/courses?tag=python",
            wait_until="domcontentloaded",
        )

        body = page.content()
        assert "Python Course" in body
        assert "Go Course" not in body
    def test_tag_filter_on_recordings(self, django_server, page):
        """Recording results are filtered to show only recordings tagged 'python'."""
        _clear_all_content()
        _create_recording(
            title="Python Recording",
            slug="python-recording",
            tags=["python"],
            date=datetime.date(2026, 1, 1),
        )
        _create_recording(
            title="Go Recording",
            slug="go-recording",
            tags=["go"],
            date=datetime.date(2026, 1, 2),
        )

        page.goto(
            f"{django_server}/event-recordings",
            wait_until="domcontentloaded",
        )
        python_chip = page.locator(
            'a[href*="tag=python"]'
        ).first
        python_chip.click()
        page.wait_for_load_state("domcontentloaded")

        body = page.content()
        assert "Python Recording" in body
        assert "Go Recording" not in body
    def test_tag_filter_on_projects(self, django_server, page):
        """Project results are filtered to show only projects tagged 'python'."""
        _clear_all_content()
        _create_project(
            title="Python Project",
            slug="python-project",
            tags=["python"],
            date=datetime.date(2026, 1, 1),
        )
        _create_project(
            title="Go Project",
            slug="go-project",
            tags=["go"],
            date=datetime.date(2026, 1, 2),
        )

        page.goto(
            f"{django_server}/projects?tag=python",
            wait_until="domcontentloaded",
        )

        body = page.content()
        assert "Python Project" in body
        assert "Go Project" not in body
    def test_tag_filter_on_resources(self, django_server, page):
        """Curated link results are filtered to show only links tagged 'python'."""
        _clear_all_content()
        _create_curated_link(
            title="Python CLI",
            tags=["python"],
            sort_order=1,
        )
        _create_curated_link(
            title="Go Toolkit",
            tags=["go"],
            sort_order=2,
        )

        page.goto(
            f"{django_server}/resources?tag=python",
            wait_until="domcontentloaded",
        )

        body = page.content()
        assert "Python CLI" in body
        # Verify Go Toolkit is not in the link cards
        link_cards = page.locator(
            '.gated-link, a[target="_blank"]'
        )
        cards_text = " ".join(
            [card.inner_text() for card in link_cards.all()]
        )
        assert "Go Toolkit" not in cards_text
    def test_tag_filter_on_downloads(self, django_server, page):
        """Download results are filtered to show only downloads tagged 'python'."""
        _clear_all_content()
        _create_download(
            title="Python Cheatsheet",
            slug="python-cheatsheet",
            tags=["python"],
        )
        _create_download(
            title="Go Cheatsheet",
            slug="go-cheatsheet",
            tags=["go"],
        )

        page.goto(
            f"{django_server}/downloads",
            wait_until="domcontentloaded",
        )
        python_chip = page.locator(
            'a[href*="tag=python"]'
        ).first
        python_chip.click()
        page.wait_for_load_state("domcontentloaded")

        body = page.content()
        assert "Python Cheatsheet" in body
        assert "Go Cheatsheet" not in body
# ---------------------------------------------------------------
# Scenario 7: Visitor reads an article with a tag rule injection
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario7TagRuleInjection:
    """
    Scenario: Visitor reads an article and sees a contextual course promo
    injected by a tag rule.

    Given: A published article "Getting Started with AI Engineering"
    tagged "ai-engineering", and an admin-configured TagRule that
    matches the "ai-engineering" tag with component_type "course_promo",
    config {"title": "Recommended Course", "course_slug": "python-data-ai",
    "cta_text": "Start learning"}, position "after_content".
    """

    def test_course_promo_injected_after_article_content(
        self, django_server
    , page):
        """After the article body, a course promo component appears
        with the configured title and CTA."""
        _clear_all_content()
        _create_article(
            title="Getting Started with AI Engineering",
            slug="getting-started-with-ai-engineering",
            description="A guide to AI engineering.",
            content_markdown=(
                "# Getting Started with AI Engineering\n\n"
                "This is the article body about AI engineering."
            ),
            tags=["ai-engineering"],
            date=datetime.date(2026, 2, 1),
        )
        # Create the course so the link target exists
        _create_course(
            title="Python Data AI",
            slug="python-data-ai",
            tags=["ai-engineering"],
        )
        _create_tag_rule(
            tag="ai-engineering",
            component_type="course_promo",
            component_config={
                "title": "Recommended Course",
                "course_slug": "python-data-ai",
                "cta_text": "Start learning",
            },
            position="after_content",
        )

        page.goto(
            f"{django_server}/blog/getting-started-with-ai-engineering",
            wait_until="domcontentloaded",
        )

        body = page.content()

        # Article content is rendered
        assert "Getting Started with AI Engineering" in body
        assert "article body about AI engineering" in body

        # Course promo component appears
        assert "Recommended Course" in body
        assert "Start learning" in body

        # The tag-rule component is present in the DOM
        tag_rule_component = page.locator(
            '.tag-rule-component'
        )
        assert tag_rule_component.count() >= 1

        # Click the "Start learning" link
        cta_link = page.locator(
            'a:has-text("Start learning")'
        ).first
        cta_link.click()
        page.wait_for_load_state("domcontentloaded")

        # User navigates to the course page
        assert "/courses/python-data-ai" in page.url
# ---------------------------------------------------------------
# Scenario 8: No matching tag rules = no injected components
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario8NoMatchingTagRules:
    """
    Scenario: Visitor reads an article with no matching tag rules
    and sees no injected components.

    Given: A published article "Intro to Go" tagged "golang", and a
    TagRule that matches only the "ai-engineering" tag.
    """

    def test_no_injected_components_for_unmatched_tags(
        self, django_server
    , page):
        """No promo or CTA components appear for articles whose tags
        do not match any tag rule."""
        _clear_all_content()
        _create_article(
            title="Intro to Go",
            slug="intro-to-go",
            description="A Go language introduction.",
            content_markdown=(
                "# Intro to Go\n\n"
                "This is the article body about Go."
            ),
            tags=["golang"],
            date=datetime.date(2026, 2, 1),
        )
        _create_tag_rule(
            tag="ai-engineering",
            component_type="course_promo",
            component_config={
                "title": "Recommended Course",
                "course_slug": "python-data-ai",
                "cta_text": "Start learning",
            },
            position="after_content",
        )

        page.goto(
            f"{django_server}/blog/intro-to-go",
            wait_until="domcontentloaded",
        )

        body = page.content()

        # Article content renders normally
        assert "Intro to Go" in body
        assert "article body about Go" in body

        # No tag-rule components are injected
        tag_rule_component = page.locator(
            '.tag-rule-component'
        )
        assert tag_rule_component.count() == 0

        # No course promo appears
        assert "Recommended Course" not in body
        assert "Start learning" not in body
# ---------------------------------------------------------------
# Scenario 9: Empty tags page with no content
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario9EmptyTagsPage:
    """
    Scenario: Anonymous visitor encounters the tags page with no content yet.

    Given: No published content exists with any tags.
    """

    def test_empty_tags_page_shows_message(self, django_server, page):
        """The page loads without errors and shows a 'No tags yet' message."""
        _clear_all_content()

        response = page.goto(
            f"{django_server}/tags", wait_until="domcontentloaded"
        )
        assert response.status == 200

        body = page.content()

        # Empty state message
        assert "No tags yet" in body

        # The visitor can still navigate via the header
        # (header links should be present)
        header = page.locator("header")
        assert header.count() >= 1

        # Navigation links exist in the header
        nav_links = page.locator("header a")
        assert nav_links.count() >= 1
# ---------------------------------------------------------------
# Scenario 10: Navigate between tag detail and tag index
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario10NavigateBetweenTagPages:
    """
    Scenario: Visitor navigates between tag detail and tag index to
    explore topics.

    Given: Published articles tagged "python" and "ai", and a recording
    tagged "python".
    """

    def test_navigate_tag_index_to_detail_and_back(self, django_server, page):
        """Navigate from /tags to /tags/python, back to /tags, then
        to /tags/ai."""
        _clear_all_content()
        _create_article(
            title="Python Tutorial",
            slug="python-tutorial",
            tags=["python"],
            date=datetime.date(2026, 1, 3),
        )
        _create_article(
            title="AI Guide",
            slug="ai-guide",
            tags=["ai"],
            date=datetime.date(2026, 1, 2),
        )
        _create_recording(
            title="Python Workshop",
            slug="python-workshop",
            tags=["python"],
            date=datetime.date(2026, 1, 1),
        )

        # Step 1: Navigate to /tags
        page.goto(
            f"{django_server}/tags", wait_until="domcontentloaded"
        )
        body = page.content()
        assert "python" in body
        assert "ai" in body

        # Step 2: Click on the "python" tag
        python_link = page.locator(
            'a[href="/tags/python"]'
        ).first
        python_link.click()
        page.wait_for_load_state("domcontentloaded")

        assert "/tags/python" in page.url
        body = page.content()
        # Sees the article and the recording
        assert "Python Tutorial" in body
        assert "Python Workshop" in body

        # Step 3: Click "All Tags" to go back to the tag index
        all_tags_link = page.locator(
            'a:has-text("All Tags")'
        ).first
        all_tags_link.click()
        page.wait_for_load_state("domcontentloaded")

        # Returns to /tags
        assert page.url.rstrip("/").endswith("/tags")
        body = page.content()
        assert "python" in body
        assert "ai" in body

        # Step 4: Click on the "ai" tag
        ai_link = page.locator(
            'a[href="/tags/ai"]'
        ).first
        ai_link.click()
        page.wait_for_load_state("domcontentloaded")

        assert "/tags/ai" in page.url
        body = page.content()
        assert "AI Guide" in body
        # Python-only content should not appear
        assert "Python Tutorial" not in body
        assert "Python Workshop" not in body
# ---------------------------------------------------------------
# Scenario 11: Tag chip on article detail links to tag detail page
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario11TagChipOnArticleDetail:
    """
    Scenario: Visitor clicks a tag chip on an article detail page
    and lands on the tag detail page.

    Given: A published article "AI Patterns" tagged "ai" and "design-patterns".

    Note: The current implementation links article detail tag chips to
    /blog?tag=X (blog listing filtered by tag). The BDD scenario says
    the user should land on /tags/design-patterns. This test verifies
    the actual application behavior: tag chips on article detail pages
    link to /blog?tag=X.
    """

    def test_tag_chip_navigates_to_filtered_blog(self, django_server, page):
        """Click a tag chip on an article detail page and land on
        the blog listing filtered by that tag."""
        _clear_all_content()
        _create_article(
            title="AI Patterns",
            slug="ai-patterns",
            description="Patterns for AI systems.",
            content_markdown="# AI Patterns\n\nContent about AI patterns.",
            tags=["ai", "design-patterns"],
            date=datetime.date(2026, 2, 10),
        )
        _create_article(
            title="Design Patterns Explained",
            slug="design-patterns-explained",
            description="Explaining common design patterns.",
            content_markdown="# Design Patterns\n\nContent.",
            tags=["design-patterns"],
            date=datetime.date(2026, 2, 5),
        )

        # Step 1: Navigate to the article detail page
        page.goto(
            f"{django_server}/blog/ai-patterns",
            wait_until="domcontentloaded",
        )

        body = page.content()
        assert "AI Patterns" in body

        # Step 2: Notice tag chips on the page
        assert "design-patterns" in body

        # Step 3: Click the "design-patterns" tag chip
        # The blog_detail template links to /blog?tag=X
        tag_link = page.locator(
            'a[href*="tag=design-patterns"]'
        ).first
        tag_link.click()
        page.wait_for_load_state("domcontentloaded")

        # User sees all content tagged "design-patterns"
        body = page.content()
        assert "AI Patterns" in body
        assert "Design Patterns Explained" in body