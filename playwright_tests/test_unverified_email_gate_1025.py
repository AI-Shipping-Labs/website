"""Playwright coverage for issue #1025 unverified-email UX."""

import datetime
from pathlib import Path

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import (
    auth_context,
    create_user,
)

pytestmark = [
    pytest.mark.local_only,
    pytest.mark.core,
    pytest.mark.django_db(transaction=True),
]

DESKTOP = {"width": 1280, "height": 900}
MOBILE = {"width": 390, "height": 844}
SCREENSHOT_DIR = Path(".tmp/screenshots/issue-1025")


def _screenshot_path(name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    return str(SCREENSHOT_DIR / f"{name}.png")


def _seed_workshops():
    """Seed the Free repro workshop and a paid-tier regression workshop."""
    from django.db import connection

    from content.models import Instructor, Workshop, WorkshopInstructor, WorkshopPage

    Workshop.objects.filter(
        slug__in=["agent-with-guardrails", "basic-paywall-1025"],
    ).delete()

    instructor, _ = Instructor.objects.get_or_create(
        instructor_id="issue-1025-instructor",
        defaults={"name": "AI Shipping Labs", "status": "published"},
    )

    free_workshop = Workshop.objects.create(
        slug="agent-with-guardrails",
        title="Agent With Guardrails",
        status="published",
        date=datetime.date(2026, 5, 26),
        landing_required_level=0,
        pages_required_level=0,
        recording_required_level=0,
        description=(
            "# Agent With Guardrails\n\n"
            "A hands-on workshop for building a guarded agent workflow."
        ),
    )
    WorkshopInstructor.objects.create(
        workshop=free_workshop, instructor=instructor, position=0,
    )
    WorkshopPage.objects.create(
        workshop=free_workshop,
        slug="intro",
        title="Intro",
        sort_order=1,
        body="# Intro\n\nBuild the first guardrail.",
    )

    paid_workshop = Workshop.objects.create(
        slug="basic-paywall-1025",
        title="Basic Paywall Workshop",
        status="published",
        date=datetime.date(2026, 5, 27),
        landing_required_level=10,
        pages_required_level=10,
        recording_required_level=10,
        description="# Basic Paywall\n\nPaid content.",
    )
    WorkshopInstructor.objects.create(
        workshop=paid_workshop, instructor=instructor, position=0,
    )
    connection.close()
    return free_workshop, paid_workshop


def _seed_users():
    create_user(
        "free-unverified-1025@test.com",
        tier_slug="free",
        email_verified=False,
    )
    create_user(
        "free-verified-1025@test.com",
        tier_slug="free",
        email_verified=True,
    )


def _open_as(browser, email, viewport=DESKTOP, theme="light"):
    context = auth_context(browser, email)
    context.add_init_script(
        f"localStorage.setItem('theme', {theme!r});"
    )
    page = context.new_page()
    page.set_viewport_size(viewport)
    return context, page


def _assert_no_horizontal_overflow(page):
    has_overflow = page.evaluate(
        "() => document.documentElement.scrollWidth > "
        "document.documentElement.clientWidth"
    )
    assert not has_overflow


def _assert_verify_gate(page, email):
    gate = page.locator('[data-testid="verify-email-required-card"]')
    expect(gate).to_be_visible()
    expect(gate).to_contain_text("Verify your email")
    expect(gate).to_contain_text(email)
    expect(gate).to_contain_text("included with your Free account")
    expect(gate.get_by_role("button", name="Resend verification email")).to_be_visible()
    forbidden = [
        "Upgrade to Free",
        "Free required",
        "Free or above required",
        "public metadata",
        "Current access: Free member",
    ]
    body = page.locator("body").inner_text()
    for text in forbidden:
        assert text not in body
    assert page.locator('[data-testid="gated-required-tier"]').count() == 0


class TestIssue1025UnverifiedEmailUX:
    def test_free_unverified_member_sees_verification_gate_and_resends(
        self, django_server, browser, django_db_blocker
    ):
        with django_db_blocker.unblock():
            free_workshop, _ = _seed_workshops()
            _seed_users()

        email = "free-unverified-1025@test.com"
        context, page = _open_as(browser, email)
        url = f"{django_server}{free_workshop.get_absolute_url()}"
        page.goto(url, wait_until="domcontentloaded")

        _assert_verify_gate(page, email)
        expect(page.locator('[data-testid="workshop-landing-paywall"]')).to_have_count(0)
        page.screenshot(path=_screenshot_path("gate-desktop-light"), full_page=True)

        for viewport, theme, name in [
            (DESKTOP, "dark", "gate-desktop-dark"),
            (MOBILE, "light", "gate-mobile-light"),
        ]:
            shot_context, shot_page = _open_as(
                browser, email, viewport=viewport, theme=theme,
            )
            shot_page.goto(url, wait_until="domcontentloaded")
            _assert_verify_gate(shot_page, email)
            _assert_no_horizontal_overflow(shot_page)
            shot_page.screenshot(path=_screenshot_path(name), full_page=True)
            shot_context.close()

        gate = page.locator('[data-testid="verify-email-required-card"]')
        gate.get_by_role("button", name="Resend verification email").click()
        page.wait_for_load_state("domcontentloaded")
        assert page.url == url
        expect(page.locator("body")).to_contain_text("Verification email sent")
        _assert_verify_gate(page, email)

        gate = page.locator('[data-testid="verify-email-required-card"]')
        gate.get_by_role("button", name="Resend verification email").click()
        page.wait_for_load_state("domcontentloaded")
        assert page.url == url
        expect(page.locator("body")).to_contain_text("minute")
        _assert_verify_gate(page, email)
        context.close()

    def test_free_verified_member_can_read_same_free_workshop(
        self, django_server, browser, django_db_blocker
    ):
        with django_db_blocker.unblock():
            free_workshop, _ = _seed_workshops()
            _seed_users()

        context, page = _open_as(browser, "free-verified-1025@test.com")
        page.goto(
            f"{django_server}{free_workshop.get_absolute_url()}",
            wait_until="domcontentloaded",
        )

        expect(page.locator('[data-testid="verify-email-required-card"]')).to_have_count(0)
        expect(page.locator('[data-testid="workshop-description"]')).to_be_visible()
        expect(page.locator('[data-testid="workshop-pages-list"]')).to_be_visible()
        context.close()

    def test_tutorial_and_recording_free_gates_use_verification_copy(
        self, django_server, browser, django_db_blocker
    ):
        with django_db_blocker.unblock():
            free_workshop, _ = _seed_workshops()
            _seed_users()

        email = "free-unverified-1025@test.com"
        context, page = _open_as(browser, email, viewport=MOBILE, theme="dark")
        page.goto(
            f"{django_server}{free_workshop.get_absolute_url()}/tutorial/intro",
            wait_until="domcontentloaded",
        )
        _assert_verify_gate(page, email)
        _assert_no_horizontal_overflow(page)
        page.screenshot(path=_screenshot_path("gate-mobile-dark"), full_page=True)

        page.goto(
            f"{django_server}{free_workshop.get_absolute_url()}/video",
            wait_until="domcontentloaded",
        )
        _assert_verify_gate(page, email)
        _assert_no_horizontal_overflow(page)
        context.close()

    def test_global_banner_compact_for_unverified_and_hidden_otherwise(
        self, django_server, browser, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _seed_users()

        email = "free-unverified-1025@test.com"
        for viewport, theme, name in [
            (DESKTOP, "light", "banner-desktop-light"),
            (DESKTOP, "dark", "banner-desktop-dark"),
            (MOBILE, "light", "banner-mobile-light"),
            (MOBILE, "dark", "banner-mobile-dark"),
        ]:
            context, page = _open_as(browser, email, viewport=viewport, theme=theme)
            page.goto(f"{django_server}/account/", wait_until="domcontentloaded")

            banner = page.locator("#email-verification-banner")
            expect(banner).to_be_visible()
            expect(banner).to_have_count(1)
            expect(banner).to_contain_text("Verify your email")
            assert banner.bounding_box()["height"] < 96
            header_height = page.locator("#site-header").bounding_box()["height"]
            heading_y = page.locator("h1").bounding_box()["y"]
            assert heading_y >= header_height
            _assert_no_horizontal_overflow(page)
            page.screenshot(path=_screenshot_path(name), full_page=True)
            context.close()

        anon = browser.new_page(viewport=DESKTOP)
        anon.goto(f"{django_server}/", wait_until="domcontentloaded")
        expect(anon.locator("#email-verification-banner")).to_have_count(0)
        anon.close()

        context, page = _open_as(browser, "free-verified-1025@test.com")
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        expect(page.locator("#email-verification-banner")).to_have_count(0)
        page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
        expect(page.locator("#email-verification-banner")).to_have_count(0)
        context.close()

    def test_paid_content_still_uses_membership_paywall(
        self, django_server, browser, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _, paid_workshop = _seed_workshops()
            _seed_users()

        context, page = _open_as(browser, "free-verified-1025@test.com")
        page.goto(
            f"{django_server}{paid_workshop.get_absolute_url()}",
            wait_until="domcontentloaded",
        )

        paywall = page.locator('[data-testid="workshop-landing-paywall"]')
        expect(paywall).to_be_visible()
        expect(paywall).to_contain_text("Upgrade to Basic")
        expect(paywall).to_contain_text("Basic or above required")
        expect(paywall.get_by_role("link", name="View Pricing")).to_be_visible()
        expect(page.locator('[data-testid="verify-email-required-card"]')).to_have_count(0)
        context.close()
