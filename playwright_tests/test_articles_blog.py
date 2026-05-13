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
from playwright_tests.conftest import ensure_tiers as _ensure_tiers

# Playwright creates an async event loop internally. Django's async safety
# check detects this and raises SynchronousOnlyOperation when we make ORM calls.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection


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

        pricing_link = page.locator('a:has-text("View Pricing")')
        assert pricing_link.count() >= 1
        pricing_link.first.click()
        page.wait_for_load_state("domcontentloaded")

        assert "/pricing" in page.url

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
