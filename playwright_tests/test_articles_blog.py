"""
Playwright smoke tests for browser-valued Articles / Blog behavior.

The server-rendered listing, tag filtering, related-article selection, markdown
rendering, and tier-state assertions are canonical in faster Django tests:
- content/tests/test_blog.py
- content/tests/test_access_control.py
"""

import datetime
import os

import pytest

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from playwright_tests.conftest import ensure_tiers as _ensure_tiers

# Playwright creates an async event loop internally. Django's async safety
# check detects this and raises SynchronousOnlyOperation when we make ORM calls.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, etc.) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only


def _clear_articles():
    """Delete all articles to ensure a clean state."""
    from content.models import Article

    Article.objects.all().delete()
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


def _create_user(email, password="testpass123", tier_slug=None):
    """Helper to create a User and optionally assign a tier."""
    from accounts.models import User
    from payments.models import Tier

    _ensure_tiers()
    user = User.objects.create_user(email=email, password=password)
    if tier_slug:
        tier = Tier.objects.get(slug=tier_slug)
        user.tier = tier
        user.save()
    connection.close()
    return user


def _login_admin_via_browser(page, base_url, email, password="adminpass123"):
    """Log in an admin user via the Django admin login page."""
    page.goto(f"{base_url}/admin/login/", wait_until="domcontentloaded")
    page.fill("#id_username", email)
    page.fill("#id_password", password)
    page.click('input[type="submit"]')
    page.wait_for_load_state("domcontentloaded")


@pytest.mark.django_db(transaction=True)
class TestBlogBrowserSmoke:
    @pytest.mark.core
    def test_clicking_article_navigates_to_detail(self, django_server, page):
        """Click an article card and navigate to its detail page."""
        _clear_articles()
        _create_article(
            title="Deploying ML Models",
            slug="deploying-ml-models",
            description="Learn how to deploy ML models in production.",
            content_markdown=(
                "# Deploying ML Models\n\n"
                "This is about **deploying** ML models.\n\n"
                "## Setup\n\n"
                "Install the dependencies.\n\n"
                "```python\nprint('hello world')\n```"
            ),
            author="Alice",
            tags=["mlops", "python"],
            date=datetime.date(2026, 1, 3),
        )

        page.goto(f"{django_server}/blog", wait_until="domcontentloaded")
        page.locator('h2:has-text("Deploying ML Models")').first.click()
        page.wait_for_load_state("domcontentloaded")

        assert "/blog/deploying-ml-models" in page.url
        assert page.title() == "Deploying ML Models | AI Shipping Labs"

        back_link = page.locator('a:has-text("Back to Blog")')
        assert back_link.count() >= 1
        assert back_link.first.get_attribute("href") == "/blog"

    @pytest.mark.core
    def test_free_user_paywall_journey_navigates_to_pricing(self, django_server, browser):
        """Free user auth cookie, gated article paywall, and pricing link work."""
        _clear_articles()
        _create_user("free@test.com", tier_slug="free")
        _create_article(
            title="Advanced Deployment Strategies",
            slug="advanced-deployment-strategies",
            description="Learn advanced deployment patterns for ML systems.",
            content_markdown=(
                "# Advanced Deployment\n\n"
                "This is the full article content that should be hidden "
                "behind the paywall."
            ),
            author="Expert",
            tags=["mlops"],
            required_level=10,
            date=datetime.date(2026, 1, 1),
        )

        context = _auth_context(browser, "free@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/blog", wait_until="domcontentloaded")

        article_card = page.locator('article:has-text("Advanced Deployment Strategies")')
        assert article_card.locator('[data-lucide="lock"]').count() >= 1
        assert "Basic or above" in article_card.inner_text()

        page.locator('h2:has-text("Advanced Deployment Strategies")').first.click()
        page.wait_for_load_state("domcontentloaded")

        assert "/blog/advanced-deployment-strategies" in page.url
        body = page.content()
        assert "Learn advanced deployment patterns for ML systems" in body
        assert "This is the full article content that should be hidden" not in body
        assert "Upgrade to Basic to read this article" in body
        assert "Create a free account" not in body

        pricing_link = page.get_by_test_id("gated-pricing-link")
        assert pricing_link.count() >= 1
        pricing_link.first.click()
        page.wait_for_load_state("domcontentloaded")

        assert "/pricing" in page.url

    @pytest.mark.core
    def test_staff_opens_draft_preview_link_from_studio(
        self, django_server, browser,
    ):
        """Studio exposes a private draft link that anonymous reviewers can open."""
        _clear_articles()
        _create_staff_user("draft-preview-staff@test.com")
        article = _create_article(
            title="Private Draft Preview",
            slug="private-draft-preview",
            description="Draft description",
            content_markdown="# Private Draft Preview\n\nReviewer-only body.",
            author="Editor",
            required_level=10,
            published=False,
            date=datetime.date(2026, 1, 4),
        )

        context = _auth_context(browser, "draft-preview-staff@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/studio/articles/", wait_until="domcontentloaded")

        row = page.locator("tr", has_text="Private Draft Preview").first
        assert row.get_by_text("Preview draft").is_visible()
        assert row.get_by_text("View on site").count() == 0

        page.goto(
            f"{django_server}/studio/articles/{article.pk}/edit",
            wait_until="domcontentloaded",
        )
        assert page.get_by_test_id("preview-draft").is_visible()
        assert page.get_by_test_id("draft-preview-link-panel").is_visible()
        preview_url = page.get_by_test_id("draft-preview-url").input_value()
        assert f"/blog/preview/{article.preview_token}" in preview_url

        anon_page = browser.new_page()
        response = anon_page.goto(preview_url, wait_until="domcontentloaded")
        assert response is not None
        assert response.status == 200
        assert anon_page.get_by_test_id("draft-preview-banner").is_visible()
        assert anon_page.locator("h1").first.inner_text() == "Private Draft Preview"
        assert "Reviewer-only body." in anon_page.content()
        assert "Upgrade to Basic" not in anon_page.content()
        assert anon_page.locator("[data-testid='studio-edit-button']").count() == 0

        public_response = anon_page.goto(
            f"{django_server}/blog/private-draft-preview",
            wait_until="domcontentloaded",
        )
        assert public_response is not None
        assert public_response.status == 404

        anon_page.close()
        context.close()

    def test_admin_article_form_slug_and_save(self, django_server, page):
        """Admin form slug prepopulation and save work in a real browser."""
        _clear_articles()
        from accounts.models import User

        User.objects.create_superuser(
            email="admin@test.com", password="adminpass123"
        )

        _login_admin_via_browser(page, django_server, "admin@test.com")
        page.goto(
            f"{django_server}/admin/content/article/add/",
            wait_until="domcontentloaded",
        )

        page.fill("#id_title", "Getting Started with LLMs")
        page.click("#id_slug")
        page.wait_for_load_state("domcontentloaded")
        assert page.input_value("#id_slug") == "getting-started-with-llms"

        page.fill(
            "#id_description",
            "A beginner guide to large language models.",
        )
        page.fill("#id_author", "Test Author")
        page.fill(
            "#id_content_markdown",
            (
                "# Getting Started with LLMs\n\n"
                "This article covers the basics of LLMs.\n\n"
                "## Installation\n\n"
                "```python\npip install openai\n```\n\n"
                "## Usage\n\n"
                "Call the API to generate text."
            ),
        )
        page.fill("#id_tags", '["llm", "ai"]')
        page.fill("#id_date", "2026-02-20")

        published_checkbox = page.locator("#id_published")
        if not published_checkbox.is_checked():
            published_checkbox.check()

        page.click('input[name="_save"]')
        page.wait_for_load_state("domcontentloaded")

        assert "/admin/content/article/" in page.url
        body = page.content()
        assert "Getting Started with LLMs" in body
        assert "published" in body.lower()


@pytest.mark.django_db(transaction=True)
class TestBlogTagFilterRow:
    """Page-level "Filter by topic" pill row on /blog (issue #1319)."""

    def _seed_two_topics(self):
        _clear_articles()
        _create_article(
            title="RAG in Production",
            slug="rag-in-production",
            description="Retrieval augmented generation patterns.",
            tags=["rag"],
            date=datetime.date(2026, 3, 3),
        )
        _create_article(
            title="MLOps Foundations",
            slug="mlops-foundations",
            description="Operationalising ML systems.",
            tags=["mlops"],
            date=datetime.date(2026, 3, 2),
        )

    @pytest.mark.core
    def test_filter_row_visible_with_all_and_topic_pills(self, django_server, page):
        """Anonymous reader sees a filter row with an active All pill + topic pills."""
        self._seed_two_topics()
        page.goto(f"{django_server}/blog", wait_until="domcontentloaded")

        row = page.get_by_test_id("blog-tag-filter")
        assert row.is_visible()
        assert page.get_by_text("Filter by topic").is_visible()

        all_pill = page.get_by_test_id("blog-tag-all")
        assert all_pill.get_attribute("aria-current") == "page"
        assert "bg-accent" in all_pill.get_attribute("class")

        assert page.get_by_test_id("blog-tag-rag").is_visible()
        assert page.get_by_test_id("blog-tag-mlops").is_visible()

    @pytest.mark.core
    def test_selecting_topic_narrows_list_and_marks_active(self, django_server, page):
        """Clicking a topic pill filters the list and flips the active state."""
        self._seed_two_topics()
        page.goto(f"{django_server}/blog", wait_until="domcontentloaded")

        page.get_by_test_id("blog-tag-rag").click()
        page.wait_for_load_state("domcontentloaded")

        assert "tag=rag" in page.url
        assert page.locator('h2:has-text("RAG in Production")').count() == 1
        assert page.locator('h2:has-text("MLOps Foundations")').count() == 0

        rag_pill = page.get_by_test_id("blog-tag-rag")
        assert rag_pill.get_attribute("aria-current") == "page"
        assert "bg-accent" in rag_pill.get_attribute("class")
        all_pill = page.get_by_test_id("blog-tag-all")
        assert all_pill.get_attribute("aria-current") is None

    @pytest.mark.core
    def test_all_pill_clears_active_filter(self, django_server, page):
        """The All pill returns a filtered view to the full listing."""
        self._seed_two_topics()
        page.goto(f"{django_server}/blog?tag=rag", wait_until="domcontentloaded")
        assert page.locator('h2:has-text("MLOps Foundations")').count() == 0

        page.get_by_test_id("blog-tag-all").click()
        page.wait_for_load_state("domcontentloaded")

        assert page.url.rstrip("/").endswith("/blog")
        assert page.locator('h2:has-text("RAG in Production")').count() == 1
        assert page.locator('h2:has-text("MLOps Foundations")').count() == 1
        all_pill = page.get_by_test_id("blog-tag-all")
        assert all_pill.get_attribute("aria-current") == "page"

    @pytest.mark.core
    def test_card_chip_filters_and_marks_matching_pill(self, django_server, page):
        """A per-card tag chip narrows the list and activates the row pill."""
        self._seed_two_topics()
        page.goto(f"{django_server}/blog", wait_until="domcontentloaded")

        card = page.locator('article:has-text("RAG in Production")')
        card.locator('[data-testid="blog-card-tags"] a:has-text("rag")').first.click()
        page.wait_for_load_state("domcontentloaded")

        assert "tag=rag" in page.url
        assert page.locator('h2:has-text("MLOps Foundations")').count() == 0
        assert page.get_by_test_id("blog-tag-rag").get_attribute("aria-current") == "page"

    @pytest.mark.core
    def test_no_match_shows_empty_state_with_view_all_cta(self, django_server, page):
        """Filtering to a tag with no articles shows the filter empty state."""
        self._seed_two_topics()
        # rag and mlops never co-occur, so the intersection is empty.
        page.goto(
            f"{django_server}/blog?tag=rag&tag=mlops",
            wait_until="domcontentloaded",
        )

        assert page.locator("article").count() == 0
        empty = page.get_by_test_id("member-empty-state")
        assert empty.is_visible()
        cta = empty.locator('a:has-text("View all articles")')
        assert cta.count() == 1
        assert cta.first.get_attribute("href") == "/blog"

    def test_long_tag_list_uses_disclosure(self, django_server, page):
        """More than 12 tags collapse the tail into a closed disclosure."""
        _clear_articles()
        tags = [f"topic-{i:02d}" for i in range(15)]
        for i, t in enumerate(tags):
            _create_article(
                title=f"Long Tail {i}",
                slug=f"long-tail-{i}",
                tags=[t],
                date=datetime.date(2026, 3, 1),
            )
        page.goto(f"{django_server}/blog", wait_until="domcontentloaded")

        more = page.get_by_test_id("blog-tag-more")
        assert more.count() == 1
        assert "More topics (+3)" in more.inner_text()
        # Closed by default: a hidden-tail pill is not visible until opened.
        hidden_pill = page.get_by_test_id("blog-tag-topic-14")
        assert not hidden_pill.is_visible()

        page.get_by_test_id("blog-tag-more-toggle").click()
        assert hidden_pill.is_visible()

    def test_active_hidden_tag_opens_disclosure(self, django_server, page):
        """An active tag in the hidden tail auto-opens the disclosure."""
        _clear_articles()
        tags = [f"topic-{i:02d}" for i in range(15)]
        for i, t in enumerate(tags):
            _create_article(
                title=f"Long Tail {i}",
                slug=f"long-tail-{i}",
                tags=[t],
                date=datetime.date(2026, 3, 1),
            )
        page.goto(
            f"{django_server}/blog?tag=topic-14",
            wait_until="domcontentloaded",
        )

        hidden_pill = page.get_by_test_id("blog-tag-topic-14")
        assert hidden_pill.is_visible()
        assert hidden_pill.get_attribute("aria-current") == "page"

    def test_untagged_blog_hides_filter_row(self, django_server, page):
        """No published article has tags -> no filter row is rendered."""
        _clear_articles()
        _create_article(
            title="Plain Article",
            slug="plain-article",
            description="No tags here.",
            tags=[],
            date=datetime.date(2026, 3, 1),
        )
        page.goto(f"{django_server}/blog", wait_until="domcontentloaded")

        assert page.locator('h2:has-text("Plain Article")').count() == 1
        assert page.get_by_test_id("blog-tag-filter").count() == 0
        assert page.get_by_text("Filter by topic").count() == 0
