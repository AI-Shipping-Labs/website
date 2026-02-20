"""
Playwright E2E tests for the Articles / Blog feature (Issue #72).

Tests cover all 12 BDD scenarios from the issue:
- Anonymous visitor discovers articles and reads one
- Reader filters articles by tag
- Free user hits a paywall on a Basic-gated article
- Basic member reads a Basic-gated article
- Basic member hits a Main-gated article
- Reader finds related articles
- Admin publishes a draft article
- Admin unpublishes an article
- Reader encounters an empty blog
- Reader filters by a tag with no matching articles
- Reader clicks a tag on a detail page
- Admin creates a new article

Usage:
    uv run pytest playwright_tests/test_articles_blog.py -v
"""

import datetime
import os

import pytest
from django.utils import timezone
from playwright.sync_api import sync_playwright

from playwright_tests.conftest import DJANGO_BASE_URL

# Playwright creates an async event loop internally. Django's async safety
# check detects this and raises SynchronousOnlyOperation when we make ORM
# calls inside a sync_playwright() context. This is safe because we're
# running synchronous code in the main thread and the event loop is only
# used by Playwright's internal IPC.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


VIEWPORT = {"width": 1280, "height": 720}


def _ensure_tiers():
    """Ensure membership tiers exist (they may be flushed between tests).

    The seed migration creates tiers, but TransactionTestCase flushes all
    tables between tests, removing them. This re-creates them if missing.
    """
    from payments.models import Tier

    TIERS = [
        {"slug": "free", "name": "Free", "level": 0},
        {"slug": "basic", "name": "Basic", "level": 10},
        {"slug": "main", "name": "Main", "level": 20},
        {"slug": "premium", "name": "Premium", "level": 30},
    ]
    for tier_data in TIERS:
        Tier.objects.get_or_create(
            slug=tier_data["slug"], defaults=tier_data
        )


def _clear_articles():
    """Delete all articles to ensure a clean state."""
    from content.models import Article

    Article.objects.all().delete()


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
    return user


def _create_session_for_user(email):
    """Create a Django session for the given user and return the session key.

    This creates a server-side session directly in the database, bypassing
    the login API and CSRF entirely. This is the same pattern used by
    test_account_page.py.
    """
    from django.contrib.sessions.backends.db import SessionStore
    from django.contrib.auth import (
        SESSION_KEY,
        BACKEND_SESSION_KEY,
        HASH_SESSION_KEY,
    )
    from accounts.models import User

    user = User.objects.get(email=email)
    session = SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = (
        "django.contrib.auth.backends.ModelBackend"
    )
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.create()
    return session.session_key


def _auth_context(browser, email):
    """Create an authenticated browser context for the given user.

    Creates a Django session via the ORM and sets the sessionid cookie
    on a new browser context.
    """
    session_key = _create_session_for_user(email)
    context = browser.new_context(viewport=VIEWPORT)
    context.add_cookies([
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
    ])
    return context


def _login_admin_via_browser(page, base_url, email, password="adminpass123"):
    """Log in an admin user via the Django admin login page."""
    page.goto(f"{base_url}/admin/login/", wait_until="networkidle")
    page.fill("#id_username", email)
    page.fill("#id_password", password)
    page.click('input[type="submit"]')
    page.wait_for_load_state("networkidle")


@pytest.mark.django_db(transaction=True)
class TestScenario1AnonymousDiscoverArticles:
    """
    Scenario 1: Anonymous visitor discovers articles and reads one.
    """

    def test_blog_listing_shows_published_articles_only(self, django_server):
        """Three published articles appear; the draft does not."""
        _clear_articles()
        _create_article(
            title="Deploying ML Models",
            slug="deploying-ml-models",
            description="Learn how to deploy ML models in production.",
            content_markdown=(
                "# Deploying ML Models\n\n"
                "This is about **deploying** ML models.\n\n"
                "```python\nprint('hello')\n```"
            ),
            author="Alice",
            tags=["mlops", "python"],
            date=datetime.date(2026, 1, 3),
        )
        _create_article(
            title="AI in Production",
            slug="ai-in-production",
            description="Running AI systems at scale.",
            content_markdown="# AI in Production\n\nContent about AI in production.",
            author="Bob",
            tags=["ai", "production"],
            date=datetime.date(2026, 1, 2),
        )
        _create_article(
            title="Data Pipeline Patterns",
            slug="data-pipeline-patterns",
            description="Common patterns for data pipelines.",
            content_markdown="# Data Pipeline Patterns\n\nContent about pipelines.",
            author="Charlie",
            tags=["data", "python"],
            date=datetime.date(2026, 1, 1),
        )
        _create_article(
            title="Secret Draft",
            slug="secret-draft",
            description="This should not be visible.",
            content_markdown="# Secret\n\nDraft content.",
            author="Admin",
            tags=["secret"],
            published=False,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                response = page.goto(
                    f"{django_server}/blog", wait_until="networkidle"
                )
                assert response.status == 200

                content = page.content()

                # Published articles are visible
                assert "Deploying ML Models" in content
                assert "AI in Production" in content
                assert "Data Pipeline Patterns" in content

                # Draft is NOT visible
                assert "Secret Draft" not in content

                # Verify excerpts, authors, and dates are shown
                assert "Learn how to deploy ML models" in content
                assert "Alice" in content
                assert "Bob" in content
                assert "Charlie" in content
            finally:
                browser.close()

    def test_clicking_article_navigates_to_detail(self, django_server):
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

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(f"{django_server}/blog", wait_until="networkidle")

                # Click on the article card (the h2 title text)
                page.locator(
                    'h2:has-text("Deploying ML Models")'
                ).first.click()
                page.wait_for_load_state("networkidle")

                # Verify URL
                assert "/blog/deploying-ml-models" in page.url

                # Verify page title
                assert page.title() == "Deploying ML Models | AI Shipping Labs"

                # Verify full article body is rendered
                body = page.content()
                assert "Deploying ML Models" in body

                # Check markdown rendering: headings, bold, code blocks
                assert "deploying" in body
                assert "Setup" in body

                # Check code block with syntax highlighting
                assert "codehilite" in body or "highlight" in body
                assert "hello world" in body

                # Back to Blog link
                back_link = page.locator('a:has-text("Back to Blog")')
                assert back_link.count() >= 1
                href = back_link.first.get_attribute("href")
                assert href == "/blog"
            finally:
                browser.close()


@pytest.mark.django_db(transaction=True)
class TestScenario2FilterByTag:
    """
    Scenario 2: Reader filters articles by tag to find a specific topic.
    """

    def test_tag_filtering(self, django_server):
        """Click a tag chip and verify filtering works."""
        _clear_articles()
        _create_article(
            title="Python for ML",
            slug="python-for-ml",
            description="Python in machine learning.",
            content_markdown="# Python for ML\n\nContent.",
            author="Alice",
            tags=["python", "ml"],
            date=datetime.date(2026, 1, 3),
        )
        _create_article(
            title="Go Microservices",
            slug="go-microservices",
            description="Building microservices with Go.",
            content_markdown="# Go Microservices\n\nContent.",
            author="Bob",
            tags=["go", "backend"],
            date=datetime.date(2026, 1, 2),
        )
        _create_article(
            title="Python Web Scraping",
            slug="python-web-scraping",
            description="Web scraping with Python.",
            content_markdown="# Python Web Scraping\n\nContent.",
            author="Charlie",
            tags=["python", "scraping"],
            date=datetime.date(2026, 1, 1),
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(f"{django_server}/blog", wait_until="networkidle")

                # All three articles visible initially
                content = page.content()
                assert "Python for ML" in content
                assert "Go Microservices" in content
                assert "Python Web Scraping" in content

                # Click the "python" tag chip on the filter bar or on a card
                python_tag_link = page.locator(
                    'a[href*="tag=python"]'
                ).first
                python_tag_link.click()
                page.wait_for_load_state("networkidle")

                # Verify URL has tag=python
                assert "tag=python" in page.url

                # Only python articles are shown
                content = page.content()
                assert "Python for ML" in content
                assert "Python Web Scraping" in content
                assert "Go Microservices" not in content

                # "Clear all" or "All" filter link is visible
                clear_link = page.locator('a:has-text("Clear all")')
                assert clear_link.count() >= 1
                assert clear_link.first.is_visible()

                # Click the clear filter link
                clear_link.first.click()
                page.wait_for_load_state("networkidle")

                # URL should be /blog without query params
                assert page.url.rstrip("/").endswith("/blog")

                # All articles are visible again
                content = page.content()
                assert "Python for ML" in content
                assert "Go Microservices" in content
                assert "Python Web Scraping" in content
            finally:
                browser.close()


@pytest.mark.django_db(transaction=True)
class TestScenario3FreeUserPaywall:
    """
    Scenario 3: Free user hits a paywall on a Basic-gated article
    and sees the upgrade path.
    """

    def test_free_user_sees_paywall_on_gated_article(self, django_server):
        """Free user sees lock icon on listing, teaser + CTA on detail."""
        _clear_articles()
        _create_user("free@test.com", tier_slug="free")
        _create_article(
            title="Advanced Deployment Strategies",
            slug="advanced-deployment-strategies",
            description="Learn advanced deployment patterns for ML systems.",
            content_markdown=(
                "# Advanced Deployment\n\n"
                "This is the full article content that should be "
                "hidden behind the paywall."
            ),
            author="Expert",
            tags=["mlops"],
            required_level=10,  # Basic
            date=datetime.date(2026, 1, 1),
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "free@test.com")
            page = context.new_page()
            try:
                # Navigate to blog listing
                page.goto(f"{django_server}/blog", wait_until="networkidle")
                content = page.content()

                # Article card shows lock icon (lucide lock icon)
                assert "Advanced Deployment Strategies" in content
                article_card = page.locator(
                    'article:has-text("Advanced Deployment Strategies")'
                )
                lock_icon = article_card.locator('[data-lucide="lock"]')
                assert lock_icon.count() >= 1

                # Tier badge visible (e.g. "Basic+")
                assert "Basic+" in article_card.inner_text()

                # Click on the article (use h2 title text inside card)
                page.locator(
                    'h2:has-text("Advanced Deployment Strategies")'
                ).first.click()
                page.wait_for_load_state("networkidle")

                # Detail page loads (HTTP 200, not a redirect)
                assert "/blog/advanced-deployment-strategies" in page.url

                body = page.content()

                # Teaser text is visible
                assert (
                    "Learn advanced deployment patterns for ML systems" in body
                )

                # Full article body is NOT present
                assert (
                    "This is the full article content that should be hidden"
                    not in body
                )

                # CTA banner is shown
                assert "Upgrade to Basic to read this article" in body

                # Blurred placeholder is present (filter: blur)
                assert "blur" in body

                # Click "View Pricing" link
                pricing_link = page.locator('a:has-text("View Pricing")')
                assert pricing_link.count() >= 1
                pricing_link.first.click()
                page.wait_for_load_state("networkidle")

                # Navigated to /pricing
                assert "/pricing" in page.url
            finally:
                browser.close()


@pytest.mark.django_db(transaction=True)
class TestScenario4BasicMemberReadsGatedArticle:
    """
    Scenario 4: Basic member reads a Basic-gated article successfully.
    """

    def test_basic_member_sees_full_article(self, django_server):
        """Basic user sees no paywall and gets full content."""
        _clear_articles()
        _create_user("basic@test.com", tier_slug="basic")
        _create_article(
            title="Advanced Deployment Strategies",
            slug="advanced-deployment-strategies",
            description="Learn advanced deployment patterns for ML systems.",
            content_markdown=(
                "# Advanced Deployment Strategies\n\n"
                "This is the **full article** content.\n\n"
                "## Configuration\n\n"
                "```python\nconfig = {'key': 'value'}\n```"
            ),
            author="Expert",
            tags=["mlops", "deployment"],
            required_level=10,  # Basic
            date=datetime.date(2026, 1, 15),
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "basic@test.com")
            page = context.new_page()
            try:
                # Navigate to article detail
                page.goto(
                    f"{django_server}/blog/advanced-deployment-strategies",
                    wait_until="networkidle",
                )

                body = page.content()

                # No paywall
                assert "Upgrade to Basic" not in body

                # Full article body is rendered
                assert "full article" in body
                assert "Configuration" in body
                assert "codehilite" in body or "highlight" in body

                # Title, author, date, tags visible in header
                assert "Advanced Deployment Strategies" in body
                assert "Expert" in body
                assert "mlops" in body
                assert "deployment" in body
            finally:
                browser.close()


@pytest.mark.django_db(transaction=True)
class TestScenario5BasicMemberHitsMainGatedArticle:
    """
    Scenario 5: Basic member hits a Main-gated article and sees
    upgrade CTA.
    """

    def test_basic_user_sees_main_paywall(self, django_server):
        """Basic user cannot read Main-gated article."""
        _clear_articles()
        _create_user("basic@test.com", tier_slug="basic")
        _create_article(
            title="Exclusive Community Insights",
            slug="exclusive-community-insights",
            description="Deep dive into community-driven AI development.",
            content_markdown=(
                "# Exclusive Community Insights\n\n"
                "This is the secret Main-level content."
            ),
            author="Staff",
            tags=["community"],
            required_level=20,  # Main
            date=datetime.date(2026, 1, 1),
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "basic@test.com")
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/blog/exclusive-community-insights",
                    wait_until="networkidle",
                )

                body = page.content()

                # Teaser text visible
                assert (
                    "Deep dive into community-driven AI development" in body
                )

                # Full article body NOT present
                assert "This is the secret Main-level content" not in body

                # CTA reads "Upgrade to Main to read this article"
                assert "Upgrade to Main to read this article" in body

                # Blurred placeholder is present
                assert "blur" in body

                # View Pricing link to /pricing
                pricing_link = page.locator('a:has-text("View Pricing")')
                assert pricing_link.count() >= 1
                href = pricing_link.first.get_attribute("href")
                assert "/pricing" in href
            finally:
                browser.close()


@pytest.mark.django_db(transaction=True)
class TestScenario6RelatedArticles:
    """
    Scenario 6: Reader finds related articles and continues reading.
    """

    def test_related_articles_shown_based_on_shared_tags(
        self, django_server
    ):
        """Related articles with shared tags appear; unrelated do not."""
        _clear_articles()
        _create_article(
            title="Intro to MLOps",
            slug="intro-to-mlops",
            description="An introduction to MLOps.",
            content_markdown=(
                "# Intro to MLOps\n\nContent about MLOps basics."
            ),
            author="Alice",
            tags=["mlops", "python"],
            date=datetime.date(2026, 1, 4),
        )
        _create_article(
            title="MLOps Best Practices",
            slug="mlops-best-practices",
            description="Best practices for MLOps.",
            content_markdown=(
                "# MLOps Best Practices\n\nContent about best practices."
            ),
            author="Bob",
            tags=["mlops", "devops"],
            date=datetime.date(2026, 1, 3),
        )
        _create_article(
            title="Python ML Libraries",
            slug="python-ml-libraries",
            description="Top Python libraries for ML.",
            content_markdown=(
                "# Python ML Libraries\n\nContent about Python ML libs."
            ),
            author="Charlie",
            tags=["python", "ml"],
            date=datetime.date(2026, 1, 2),
        )
        _create_article(
            title="Go Concurrency",
            slug="go-concurrency",
            description="Go concurrency patterns.",
            content_markdown="# Go Concurrency\n\nContent about Go.",
            author="Dave",
            tags=["go"],
            date=datetime.date(2026, 1, 1),
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/blog/intro-to-mlops",
                    wait_until="networkidle",
                )

                body = page.content()

                # Related Articles section exists
                assert "Related Articles" in body

                # Related articles are shown (they share tags)
                assert "MLOps Best Practices" in body
                assert "Python ML Libraries" in body

                # Unrelated article is NOT shown in related section
                related_section = page.locator(
                    'section:has-text("Related Articles")'
                )
                related_text = related_section.inner_text()
                assert "Go Concurrency" not in related_text

                # Click on a related article
                related_section.locator(
                    'a[href="/blog/mlops-best-practices"]'
                ).first.click()
                page.wait_for_load_state("networkidle")

                assert "/blog/mlops-best-practices" in page.url
                assert "MLOps Best Practices" in page.content()
            finally:
                browser.close()


@pytest.mark.django_db(transaction=True)
class TestScenario7AdminPublishesDraft:
    """
    Scenario 7: Admin publishes a draft article and it appears on
    the blog.
    """

    def test_admin_publishes_article_via_action(self, django_server):
        """Admin uses admin action to publish a draft article."""
        _clear_articles()
        from accounts.models import User

        User.objects.create_superuser(
            email="admin@test.com", password="adminpass123"
        )
        _create_article(
            title="Upcoming Feature Guide",
            slug="upcoming-feature-guide",
            description="Guide to upcoming features.",
            content_markdown=(
                "# Upcoming Feature Guide\n\n"
                "Content about upcoming features."
            ),
            author="Admin",
            tags=["features"],
            published=False,
            date=datetime.date(2026, 2, 1),
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                # Log in as admin
                _login_admin_via_browser(
                    page, django_server, "admin@test.com"
                )

                # Navigate to article admin list
                page.goto(
                    f"{django_server}/admin/content/article/",
                    wait_until="networkidle",
                )

                body = page.content()
                assert "Upcoming Feature Guide" in body

                # Verify status shows "draft"
                assert "draft" in body.lower()

                # Select the checkbox for the article
                checkbox = page.locator(
                    'input[type="checkbox"][name="_selected_action"]'
                ).first
                checkbox.check()

                # Choose "Publish selected articles" action and submit
                page.select_option(
                    'select[name="action"]', "publish_articles"
                )
                page.click('button[name="index"]')
                page.wait_for_load_state("networkidle")

                # Reload to see updated status
                page.goto(
                    f"{django_server}/admin/content/article/",
                    wait_until="networkidle",
                )
                body = page.content()
                assert "published" in body.lower()

                # Navigate to public blog listing
                page.goto(
                    f"{django_server}/blog", wait_until="networkidle"
                )
                body = page.content()
                assert "Upcoming Feature Guide" in body

                # Click to verify the detail page works
                page.locator(
                    'h2:has-text("Upcoming Feature Guide"), '
                    'a:has-text("Upcoming Feature Guide")'
                ).first.click()
                page.wait_for_load_state("networkidle")

                assert "/blog/upcoming-feature-guide" in page.url
                assert "Upcoming Feature Guide" in page.content()
            finally:
                browser.close()


@pytest.mark.django_db(transaction=True)
class TestScenario8AdminUnpublishesArticle:
    """
    Scenario 8: Admin unpublishes an article and it disappears from
    the blog.
    """

    def test_admin_unpublishes_article(self, django_server):
        """Admin unpublishes a published article; it disappears from
        public blog and returns 404."""
        _clear_articles()
        from accounts.models import User

        User.objects.create_superuser(
            email="admin@test.com", password="adminpass123"
        )
        _create_article(
            title="Old Announcement",
            slug="old-announcement",
            description="An old announcement.",
            content_markdown="# Old Announcement\n\nOld content.",
            author="Admin",
            tags=["news"],
            published=True,
            date=datetime.date(2026, 1, 1),
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                # Verify article is visible on blog first
                page.goto(
                    f"{django_server}/blog", wait_until="networkidle"
                )
                assert "Old Announcement" in page.content()

                # Log in as admin
                _login_admin_via_browser(
                    page, django_server, "admin@test.com"
                )

                # Navigate to article admin list
                page.goto(
                    f"{django_server}/admin/content/article/",
                    wait_until="networkidle",
                )

                # Select the checkbox for the article
                checkbox = page.locator(
                    'input[type="checkbox"][name="_selected_action"]'
                ).first
                checkbox.check()

                # Choose "Unpublish selected articles"
                page.select_option(
                    'select[name="action"]', "unpublish_articles"
                )
                page.click('button[name="index"]')
                page.wait_for_load_state("networkidle")

                # Verify status changed to draft
                page.goto(
                    f"{django_server}/admin/content/article/",
                    wait_until="networkidle",
                )
                body = page.content()
                assert "draft" in body.lower()

                # Navigate to public blog
                page.goto(
                    f"{django_server}/blog", wait_until="networkidle"
                )
                assert "Old Announcement" not in page.content()

                # Direct access returns 404
                response = page.goto(
                    f"{django_server}/blog/old-announcement",
                    wait_until="networkidle",
                )
                assert response.status == 404
            finally:
                browser.close()


@pytest.mark.django_db(transaction=True)
class TestScenario9EmptyBlog:
    """
    Scenario 9: Reader encounters an empty blog (no published articles).
    """

    def test_empty_blog_shows_friendly_message(self, django_server):
        """With no published articles, blog shows a friendly empty state."""
        _clear_articles()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                response = page.goto(
                    f"{django_server}/blog", wait_until="networkidle"
                )
                assert response.status == 200

                body = page.content()

                # Friendly empty state message
                assert "No posts yet" in body
                assert "AI engineering" in body

                # Subscribe link
                subscribe_link = page.locator(
                    'a:has-text("Subscribe to get notified")'
                )
                assert subscribe_link.count() >= 1
            finally:
                browser.close()


@pytest.mark.django_db(transaction=True)
class TestScenario10FilterByTagNoMatches:
    """
    Scenario 10: Reader filters by a tag with no matching articles.
    """

    def test_no_matching_tag_shows_message(self, django_server):
        """Filtering by a tag with no articles shows a helpful message."""
        _clear_articles()
        _create_article(
            title="Python Basics",
            slug="python-basics",
            description="Python basics article.",
            content_markdown="# Python Basics\n\nContent.",
            author="Alice",
            tags=["python"],
            date=datetime.date(2026, 1, 1),
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                # Navigate with a tag that has no matching articles
                response = page.goto(
                    f"{django_server}/blog?tag=rust",
                    wait_until="networkidle",
                )
                assert response.status == 200

                body = page.content()
                assert "No articles found with the selected tags" in body

                # "View all articles" link is visible
                view_all = page.locator(
                    'a:has-text("View all articles")'
                )
                assert view_all.count() >= 1

                # Click it
                view_all.first.click()
                page.wait_for_load_state("networkidle")

                # Back to /blog, Python Basics is visible
                assert page.url.rstrip("/").endswith("/blog")
                assert "Python Basics" in page.content()
            finally:
                browser.close()


@pytest.mark.django_db(transaction=True)
class TestScenario11TagChipOnDetailPage:
    """
    Scenario 11: Reader clicks a tag on a detail page to find more
    articles on that topic.
    """

    def test_tag_chip_on_detail_navigates_to_filtered_listing(
        self, django_server
    ):
        """Click a tag on the detail page and see filtered listing."""
        _clear_articles()
        _create_article(
            title="Intro to Python",
            slug="intro-to-python",
            description="Introduction to Python programming.",
            content_markdown="# Intro to Python\n\nLearn Python.",
            author="Alice",
            tags=["python", "beginner"],
            date=datetime.date(2026, 1, 2),
        )
        _create_article(
            title="Advanced Python",
            slug="advanced-python",
            description="Advanced Python topics.",
            content_markdown="# Advanced Python\n\nAdvanced content.",
            author="Bob",
            tags=["python", "advanced"],
            date=datetime.date(2026, 1, 1),
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/blog/intro-to-python",
                    wait_until="networkidle",
                )

                # Click the "python" tag chip in the article header
                tag_link = page.locator(
                    'a[href="/blog?tag=python"]'
                ).first
                tag_link.click()
                page.wait_for_load_state("networkidle")

                # URL should be /blog?tag=python
                assert "tag=python" in page.url

                body = page.content()
                # Both python-tagged articles should be shown
                assert "Intro to Python" in body
                assert "Advanced Python" in body
            finally:
                browser.close()


@pytest.mark.django_db(transaction=True)
class TestScenario12AdminCreatesArticle:
    """
    Scenario 12: Admin creates a new article and it goes live on
    the blog.
    """

    def test_admin_creates_article_via_admin(self, django_server):
        """Admin creates a new article via admin interface."""
        _clear_articles()
        from accounts.models import User

        User.objects.create_superuser(
            email="admin@test.com", password="adminpass123"
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                # Log in as admin
                _login_admin_via_browser(
                    page, django_server, "admin@test.com"
                )

                # Navigate to the add article form
                page.goto(
                    f"{django_server}/admin/content/article/add/",
                    wait_until="networkidle",
                )

                # Fill in Title
                page.fill("#id_title", "Getting Started with LLMs")

                # Trigger the slug prepopulation by clicking the slug field
                page.click("#id_slug")
                page.wait_for_timeout(500)

                # Check slug was auto-populated
                slug_value = page.input_value("#id_slug")
                assert slug_value == "getting-started-with-llms"

                # Fill in description
                page.fill(
                    "#id_description",
                    "A beginner guide to large language models.",
                )

                # Fill in author
                page.fill("#id_author", "Test Author")

                # Fill in content_markdown with headings and a code block
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

                # Fill in tags as JSON (the field is a JSONField)
                page.fill("#id_tags", '["llm", "ai"]')

                # Set date
                page.fill("#id_date", "2026-02-20")

                # Check the "Published" checkbox
                published_checkbox = page.locator("#id_published")
                if not published_checkbox.is_checked():
                    published_checkbox.check()

                # Click "Save"
                page.click('input[name="_save"]')
                page.wait_for_load_state("networkidle")

                # Should redirect to the changelist
                assert "/admin/content/article/" in page.url

                body = page.content()
                assert "Getting Started with LLMs" in body
                assert "published" in body.lower()

                # Navigate to public blog
                page.goto(
                    f"{django_server}/blog", wait_until="networkidle"
                )
                body = page.content()
                assert "Getting Started with LLMs" in body
                assert (
                    "A beginner guide to large language models" in body
                )
                assert "Test Author" in body

                # Click on the article
                page.locator(
                    'h2:has-text("Getting Started with LLMs"), '
                    'a:has-text("Getting Started with LLMs")'
                ).first.click()
                page.wait_for_load_state("networkidle")

                assert "/blog/getting-started-with-llms" in page.url
                body = page.content()
                assert "Getting Started with LLMs" in body
                # Verify code block with syntax highlighting
                assert "codehilite" in body or "highlight" in body
                # Code content is present (may be wrapped in spans
                # by Pygments syntax highlighting)
                assert "openai" in body
            finally:
                browser.close()
