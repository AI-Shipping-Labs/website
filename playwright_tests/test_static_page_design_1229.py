"""Focused public journeys and screenshot support for issue #1229."""

import os
from pathlib import Path
from uuid import uuid4

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.local_only,
]

SCREENSHOT_DIR = Path(".tmp/screenshots/issue-1229")
FAQ_QUESTION = "Who is this community for?"
ROUTE_READY_HEADINGS = {
    "/": "Turn AI ideas into real projects",
    "/about": "About AI Shipping Labs",
    "/faq": "Frequently Asked Questions",
    "/terms/": "Terms of Service",
    "/privacy/": "Privacy Policy",
    "/impressum/": "Impressum",
}


@pytest.fixture(autouse=True)
def _seed_required_home_data(django_db_blocker):
    with django_db_blocker.unblock():
        ensure_tiers()


def test_home_social_proof_leads_into_working_faq(django_server, page):
    response = page.goto(f"{django_server}/", wait_until="domcontentloaded")
    assert response.status == 200

    testimonials = page.locator("#testimonials")
    faq = page.locator("#faq")
    expect(testimonials.get_by_text("What learners say", exact=True)).to_be_visible()
    expect(testimonials.get_by_role("heading", level=2)).to_be_visible()
    expect(faq.get_by_role("heading", name="Common questions")).to_be_visible()

    disclosure = faq.locator("details").filter(has_text=FAQ_QUESTION)
    disclosure.locator("summary").click()
    expect(disclosure).to_have_attribute("open", "")
    expect(disclosure.locator("div").last).to_be_visible()
    expect(testimonials).to_be_visible()


@pytest.mark.creates_data
def test_mobile_home_footer_handles_valid_then_invalid_email(django_server, page):
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")
    newsletter = page.locator("#newsletter")
    newsletter.scroll_into_view_if_needed()
    form = newsletter.locator("form.subscribe-form")
    form.locator('input[name="email"]').fill(
        f"static-design-1229-{uuid4().hex[:10]}@example.com"
    )
    form.get_by_role("button", name="Subscribe").click()
    success = newsletter.locator(".footer-subscribe-message")
    expect(success).to_be_visible(timeout=10000)
    expect(success).to_contain_text("created a free account")
    assert page.url.rstrip("/") == django_server.rstrip("/")

    page.reload(wait_until="domcontentloaded")
    newsletter = page.locator("#newsletter")
    newsletter.scroll_into_view_if_needed()
    form = newsletter.locator("form.subscribe-form")
    form.locator('input[name="email"]').fill("not-an-email")
    form.get_by_role("button", name="Subscribe").click()
    expect(newsletter.locator(".footer-subscribe-error")).to_be_visible()
    expect(newsletter.locator(".footer-subscribe-message")).to_be_hidden()
    expect(form.locator('input[name="email"]')).to_be_editable()
    assert page.url.rstrip("/") == django_server.rstrip("/")


def test_about_keeps_both_founders_and_safe_linkedin_handoff(django_server, page):
    response = page.goto(f"{django_server}/about", wait_until="domcontentloaded")
    assert response.status == 200
    expect(page.get_by_role("heading", name="Founders", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Alexey Grigorev")).to_be_visible()
    expect(page.get_by_role("heading", name="Valeriia Kuka")).to_be_visible()

    linkedin = page.locator('a[aria-label="LinkedIn"]').first
    expect(linkedin).to_have_attribute("target", "_blank")
    expect(linkedin).to_have_attribute("rel", "noopener noreferrer")
    page.context.route(
        "https://linkedin.com/**",
        lambda route: route.fulfill(status=200, body="LinkedIn profile"),
    )
    with page.expect_popup() as popup_info:
        linkedin.click()
    popup = popup_info.value
    popup.wait_for_load_state("domcontentloaded")
    assert popup.url.startswith("https://linkedin.com/in/")
    popup.close()


def test_standalone_faq_opens_closes_and_returns_home(django_server, page):
    response = page.goto(f"{django_server}/faq", wait_until="domcontentloaded")
    assert response.status == 200
    expect(page.locator("#faq")).to_have_count(0)
    disclosure = page.locator("details").filter(has_text=FAQ_QUESTION)
    summary = disclosure.locator("summary")
    summary.click()
    expect(disclosure).to_have_attribute("open", "")
    summary.click()
    expect(disclosure).not_to_have_attribute("open", "")
    page.get_by_role("link", name="Back to home").click()
    page.wait_for_url(f"{django_server}/")


def test_registration_terms_journey_keeps_signup_available(django_server, page):
    page.goto(f"{django_server}/accounts/register/", wait_until="domcontentloaded")
    page.get_by_role("link", name="Terms of Service").first.click()
    page.wait_for_url(f"{django_server}/terms")
    expect(page.get_by_role("heading", name="Terms of Service")).to_be_visible()
    expect(page.get_by_role("heading", name="14. Contact")).to_be_visible()
    expect(page.locator("footer")).to_be_visible()
    page.get_by_role("link", name="Join free", exact=True).first.click()
    page.wait_for_url(f"{django_server}/accounts/register/")
    expect(page.locator("#register-form")).to_be_visible()


def test_privacy_keeps_rights_contact_and_related_legal_links(django_server, page):
    response = page.goto(f"{django_server}/privacy/", wait_until="domcontentloaded")
    assert response.status == 200
    expect(page.get_by_role("heading", name="Privacy Policy")).to_be_visible()
    expect(page.get_by_role("heading", name="6. Your rights")).to_be_visible()
    expect(page.get_by_role("heading", name="10. Contact")).to_be_visible()
    footer = page.locator("footer")
    expect(footer.get_by_role("link", name="Terms of Service")).to_be_visible()
    expect(footer.get_by_role("link", name="Impressum")).to_be_visible()


def test_footer_impressum_journey_keeps_operator_sections(django_server, page):
    page.goto(f"{django_server}/", wait_until="domcontentloaded")
    footer = page.locator("footer")
    footer.get_by_role("link", name="Impressum").click()
    page.wait_for_url(f"{django_server}/impressum")
    for heading in [
        "Angaben gemäß § 5 TMG",
        "Kontakt",
        "Umsatzsteuer-Identifikationsnummer",
        "Streitschlichtung",
        "Haftung für Inhalte",
        "Haftung für Links",
        "Urheberrecht",
    ]:
        expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()
    expect(page.locator("header")).to_be_visible()
    expect(page.locator("footer")).to_be_visible()


SCREENSHOT_CASES = [
    pytest.param(path, width, theme, id=f"{name}-{width}-{theme}")
    for path, name in [
        ("/", "home"),
        ("/about", "about"),
        ("/faq", "faq"),
        ("/terms/", "terms"),
        ("/privacy/", "privacy"),
        ("/impressum/", "impressum"),
    ]
    for width in (1440, 390)
    for theme in ("light", "dark")
]


@pytest.mark.manual_visual
@pytest.mark.parametrize(("path", "width", "theme"), SCREENSHOT_CASES)
def test_capture_static_page_review_matrix(django_server, page, path, width, theme):
    """Generate the tester-owned 24-image desktop/mobile/theme review set."""
    page.set_viewport_size({"width": width, "height": 900 if width == 1440 else 844})
    page.add_init_script(f"localStorage.setItem('theme', '{theme}')")
    response = page.goto(f"{django_server}{path}", wait_until="domcontentloaded")
    assert response.status == 200
    expect(
        page.get_by_role("heading", name=ROUTE_READY_HEADINGS[path], exact=True)
    ).to_be_visible()
    assert not page.evaluate(
        "() => document.documentElement.scrollWidth > window.innerWidth + 1"
    )
    expected_dark = theme == "dark"
    assert page.locator("html").evaluate("el => el.classList.contains('dark')") \
        is expected_dark

    route_name = "home" if path == "/" else path.strip("/")
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(
        path=SCREENSHOT_DIR / f"{route_name}-{width}-{theme}.png",
        full_page=True,
    )
