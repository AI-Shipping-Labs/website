"""Guest funnel coverage for issue #1159."""

import os
import re
import uuid
from datetime import date
from pathlib import Path

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import (
    DEFAULT_PASSWORD,
    auth_context,
    create_user,
    ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only

MOBILE = {"width": 390, "height": 844}
SCREENSHOT_DIR = Path(".tmp/screenshots/issue-1159")


def _email(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}@test.com"


def _screenshot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREENSHOT_DIR / f"{name}.png"), full_page=True)


def _seed_basic_article(slug="guest-funnel-basic-article"):
    from django.db import connection

    from content.models import Article

    ensure_tiers()
    article, _ = Article.objects.update_or_create(
        slug=slug,
        defaults={
            "title": "Guest Funnel Basic Article",
            "description": "A practical paid article teaser.",
            "content_markdown": "# Paid\n\nSECRET PAID ARTICLE BODY",
            "content_html": "<h1>Paid</h1><p>SECRET PAID ARTICLE BODY</p>",
            "date": date(2026, 7, 1),
            "published": True,
            "page_type": "blog",
            "required_level": 10,
        },
    )
    connection.close()
    return article.get_absolute_url()


def _seed_free_user(email):
    create_user(
        email=email,
        tier_slug="free",
        password=DEFAULT_PASSWORD,
        email_verified=True,
    )


def _csrf_token(page, django_server):
    page.goto(f"{django_server}/accounts/register/", wait_until="domcontentloaded")
    csrf_cookie = page.context.cookies(django_server)
    return next(
        cookie["value"] for cookie in csrf_cookie if cookie["name"] == "csrftoken"
    )


@pytest.mark.django_db(transaction=True)
class TestGuestFunnel1159:
    @pytest.mark.core
    def test_desktop_header_join_free_links_to_register_without_next(
        self, django_server, page
    ):
        page.goto(f"{django_server}/blog", wait_until="domcontentloaded")

        join = page.get_by_test_id("header-join-free-link")
        expect(join).to_be_visible()
        expect(page.get_by_test_id("header-sign-in-link").first).to_be_visible()
        assert join.get_attribute("href") == "/accounts/register/"
        assert "next=" not in join.get_attribute("href")
        _screenshot(page, "desktop-header-join-free")

        join.click()
        page.wait_for_url(f"{django_server}/accounts/register/", timeout=5000)
        expect(page.locator("#register-email")).to_be_visible()
        _screenshot(page, "standalone-register")

    @pytest.mark.core
    def test_mobile_header_join_free_is_primary_and_closes_menu(
        self, django_server, browser
    ):
        context = browser.new_context(viewport=MOBILE)
        page = context.new_page()
        try:
            page.goto(f"{django_server}/events", wait_until="domcontentloaded")
            page.locator("#mobile-menu-btn").click()

            join = page.get_by_test_id("mobile-header-join-free-link")
            expect(join).to_be_visible()
            expect(page.locator('#mobile-menu [data-testid="header-sign-in-link"]')).to_be_visible()
            assert join.get_attribute("href") == "/accounts/register/"
            box = join.bounding_box()
            assert box is not None
            assert box["height"] >= 44
            _screenshot(page, "mobile-menu-join-free")

            join.click()
            page.wait_for_url(f"{django_server}/accounts/register/", timeout=5000)
            expect(page.locator("#mobile-menu")).to_have_class(
                re.compile("hidden")
            )
        finally:
            context.close()

    @pytest.mark.core
    def test_guest_and_free_member_paid_article_paywalls(
        self, django_server, browser, django_db_blocker
    ):
        with django_db_blocker.unblock():
            article_path = _seed_basic_article("guest-vs-member-1159")
            free_email = _email("free-member-1159")
            _seed_free_user(free_email)

        guest_context = browser.new_context()
        guest_page = guest_context.new_page()
        try:
            guest_page.goto(
                f"{django_server}{article_path}",
                wait_until="domcontentloaded",
            )
            body = guest_page.content()
            assert "SECRET PAID ARTICLE BODY" not in body
            # Issue #1335: unified upgrade card — upgrade heading + Pricing
            # plus a no-cost account path and a sign-in link.
            assert "Upgrade to Basic to read this article" in body
            assert f'href="/accounts/signup/?next={article_path}"' in body
            assert f'href="/accounts/login/?next={article_path}"' in body
            expect(guest_page.get_by_test_id("gated-pricing-link")).to_be_visible()
            _screenshot(guest_page, "guest-paid-article-paywall")
        finally:
            guest_context.close()

        member_context = auth_context(browser, free_email)
        member_page = member_context.new_page()
        try:
            member_page.goto(
                f"{django_server}{article_path}",
                wait_until="domcontentloaded",
            )
            body = member_page.content()
            assert "Upgrade to Basic to read this article" in body
            assert "Create a free account" not in body
        finally:
            member_context.close()

    @pytest.mark.core
    def test_gated_article_signup_returns_as_authenticated_free_member(
        self, django_server, page, django_db_blocker
    ):
        with django_db_blocker.unblock():
            article_path = _seed_basic_article("signup-return-1159")
        email = _email("signup-return-1159")

        page.goto(f"{django_server}{article_path}", wait_until="domcontentloaded")
        page.get_by_test_id("gated-create-free-account-link").click()
        # Issue #1335: signup routes through /accounts/signup/ which redirects
        # to the registration page carrying the same next target.
        page.wait_for_url("**/accounts/register/**", timeout=5000)

        page.fill("#register-email", email)
        page.fill("#register-password", DEFAULT_PASSWORD)
        page.fill("#register-password-confirm", DEFAULT_PASSWORD)
        page.click("#register-submit")

        page.wait_for_url(f"{django_server}{article_path}", timeout=10000)
        expect(page.get_by_test_id("account-menu-trigger")).to_be_visible()
        body = page.content()
        assert "Upgrade to Basic to read this article" in body

        with django_db_blocker.unblock():
            from accounts.models import User

            user = User.objects.get(email=email)
            assert user.tier.slug == "free"
            assert user.email_verified is False

    @pytest.mark.core
    def test_register_api_unsafe_next_falls_back_to_dashboard(
        self, django_server, page
    ):
        csrf_token = _csrf_token(page, django_server)
        email = _email("unsafe-next-1159")

        response = page.request.post(
            f"{django_server}/api/register",
            data={
                "email": email,
                "password": DEFAULT_PASSWORD,
                "next": "https://evil.example/phish",
            },
            headers={"X-CSRFToken": csrf_token},
        )

        assert response.status == 201
        data = response.json()
        assert data["redirect_url"] == "/"
        assert data["return_url"] == ""

        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        expect(page.get_by_test_id("account-menu-trigger")).to_be_visible()

    @pytest.mark.core
    def test_duplicate_email_stays_on_form_and_remains_anonymous(
        self, django_server, page, django_db_blocker
    ):
        email = _email("duplicate-1159")
        with django_db_blocker.unblock():
            _seed_free_user(email)

        page.goto(f"{django_server}/accounts/register/", wait_until="domcontentloaded")
        page.fill("#register-email", email)
        page.fill("#register-password", DEFAULT_PASSWORD)
        page.fill("#register-password-confirm", DEFAULT_PASSWORD)
        page.click("#register-submit")

        error = page.locator("#register-error")
        expect(error).to_be_visible()
        expect(error).to_have_text("A user with this email already exists")
        assert page.url == f"{django_server}/accounts/register/"
        expect(page.get_by_test_id("header-join-free-link")).to_be_visible()
        expect(page.get_by_test_id("account-menu-trigger")).to_have_count(0)

    @pytest.mark.core
    def test_password_mismatch_blocks_register_api_until_corrected(
        self, django_server, page
    ):
        calls = {"count": 0}

        def count_register(route):
            calls["count"] += 1
            route.continue_()

        page.route("**/api/register", count_register)
        page.goto(f"{django_server}/accounts/register/", wait_until="domcontentloaded")
        page.fill("#register-email", _email("mismatch-1159"))
        page.fill("#register-password", DEFAULT_PASSWORD)
        page.fill("#register-password-confirm", "Different123!")
        page.click("#register-submit")

        expect(page.locator("#register-error")).to_have_text("Passwords do not match")
        assert calls["count"] == 0

        page.fill("#register-password-confirm", DEFAULT_PASSWORD)
        page.click("#register-submit")
        page.wait_for_url(f"{django_server}/", timeout=10000)
        assert calls["count"] == 1
