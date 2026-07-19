import os

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import ensure_site_config_tiers, ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.local_only,
    pytest.mark.core,
]


def _seed(django_db_blocker):
    with django_db_blocker.unblock():
        from allauth.socialaccount.models import SocialApp

        from events.models import Event

        ensure_tiers()
        ensure_site_config_tiers()
        SocialApp.objects.all().delete()
        Event.objects.all().delete()


def test_value_story_precedes_peer_tiers_and_separate_conversion(
    django_server, page, django_db_blocker
):
    _seed(django_db_blocker)
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    section_ids = page.locator("main > section[id]").evaluate_all(
        "sections => sections.map(section => section.id)"
    )
    assert section_ids[:10] == [
        "about",
        "activities",
        "sprint-story",
        "testimonials",
        "tiers",
        "join-free",
        "blog",
        "workshops",
        "faq",
    ]
    expect(page.locator("#activities [data-testid=home-activity-card]")).to_have_count(5)

    tiers = page.locator("#tiers")
    expect(tiers.locator('[data-testid="home-tier-card"]')).to_have_count(3)
    expect(tiers.locator("form, input, [data-auth-oauth-providers]")).to_have_count(0)
    expect(tiers.locator('[data-tier-card="free"]')).to_have_count(0)
    expect(tiers.get_by_role("link", name="Join free", exact=True)).to_have_count(0)

    join = page.locator("#join-free")
    expect(join).to_be_attached()
    expect(join.locator("#register-form")).to_have_count(1)
    assert join.evaluate("el => el.parentElement.tagName === 'MAIN'")
    assert join.evaluate("el => !el.closest('[data-tier-carousel]')")


def test_direct_fragment_reveals_usable_free_section(
    django_server, page, django_db_blocker
):
    _seed(django_db_blocker)
    page.goto(f"{django_server}/#join-free", wait_until="domcontentloaded")
    heading = page.get_by_role("heading", name="Create your free account")
    expect(heading).to_be_visible()
    positions = page.evaluate(
        """() => ({
          headerBottom: document.querySelector('#site-header').getBoundingClientRect().bottom,
          headingTop: document.querySelector('#home-join-free-heading').getBoundingClientRect().top
        })"""
    )
    assert positions["headingTop"] >= positions["headerBottom"] - 1

    submit = page.locator("#join-free #register-submit")
    submit.focus()
    assert submit.evaluate("el => getComputedStyle(el).outlineStyle !== 'none'")
    expect(page.locator("#join-free #register-form")).to_be_visible()


def test_password_mismatch_is_announced_without_request_or_card_mutation(
    django_server, page, django_db_blocker
):
    _seed(django_db_blocker)
    register_requests = []
    page.on(
        "request",
        lambda request: register_requests.append(request)
        if request.url.endswith("/api/register")
        else None,
    )
    page.goto(f"{django_server}/#join-free", wait_until="domcontentloaded")
    tier_heights = page.locator('[data-testid="home-tier-card"]').evaluate_all(
        "cards => cards.map(card => card.getBoundingClientRect().height)"
    )
    page.locator("#register-email").fill("mismatch-1241@example.com")
    page.locator("#register-password").fill("Password123!")
    page.locator("#register-password-confirm").fill("Different123!")
    page.locator("#register-submit").click()
    error = page.locator("#register-error")
    expect(error).to_be_visible()
    expect(error).to_have_text("Passwords do not match")
    expect(error).to_have_attribute("role", "alert")
    assert register_requests == []
    current_tier_heights = page.locator('[data-testid="home-tier-card"]').evaluate_all(
        "cards => cards.map(card => card.getBoundingClientRect().height)"
    )
    assert all(
        abs(before - after) < 0.1
        for before, after in zip(tier_heights, current_tier_heights, strict=True)
    )


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_mobile_conversion_stays_outside_carousel_without_page_overflow(
    django_server, browser, django_db_blocker, theme
):
    _seed(django_db_blocker)
    context = browser.new_context(viewport={"width": 393, "height": 852})
    page = context.new_page()
    page.add_init_script(f"localStorage.setItem('theme', '{theme}')")
    try:
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        html_classes = (page.locator("html").get_attribute("class") or "").split()
        assert ("dark" in html_classes) is (theme == "dark")
        carousel = page.get_by_test_id("home-tier-carousel")
        expect(carousel.locator('[data-testid="home-tier-card"]')).to_have_count(3)
        expect(carousel.locator('[data-tier-card="free"]')).to_have_count(0)
        page.goto(f"{django_server}/#join-free", wait_until="domcontentloaded")
        join = page.locator("#join-free")
        expect(join.locator("#register-form")).to_be_visible()
        assert join.evaluate("el => !el.closest('[data-tier-carousel]')")
        assert page.evaluate(
            "() => document.documentElement.scrollWidth - innerWidth"
        ) <= 1
        for selector in [
            "#register-email",
            "#register-password",
            "#register-password-confirm",
            "#register-submit",
        ]:
            box = page.locator(selector).bounding_box()
            assert box is not None
            assert box["x"] >= 0
            assert box["x"] + box["width"] <= 394
    finally:
        context.close()


def test_pricing_inline_owner_does_not_leak_into_home(
    django_server, page, django_db_blocker
):
    _seed(django_db_blocker)
    page.goto(f"{django_server}/pricing", wait_until="domcontentloaded")
    expect(page.locator(".pricing-inline-register-embed")).to_have_count(1)
    page.goto(f"{django_server}/", wait_until="domcontentloaded")
    expect(page.locator(".pricing-inline-register-embed")).to_have_count(0)
    expect(page.locator("#tiers form")).to_have_count(0)
    expect(page.locator("#join-free #register-form")).to_have_count(1)
