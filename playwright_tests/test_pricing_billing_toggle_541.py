import os

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.django_db(transaction=True)
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

PRICING_TIERS = [
    {
        "slug": "free",
        "name": "Free",
        "level": 0,
        "price_eur_month": None,
        "price_eur_year": None,
        "description": "Subscribe to the newsletter and access open content.",
        "features": ["Newsletter emails", "Access to open content"],
    },
    {
        "slug": "basic",
        "name": "Basic",
        "level": 10,
        "price_eur_month": 20,
        "price_eur_year": 200,
        "description": "Access curated educational content and tutorials.",
        "features": ["Exclusive articles", "Tutorials with code examples"],
    },
    {
        "slug": "main",
        "name": "Main",
        "level": 20,
        "price_eur_month": 50,
        "price_eur_year": 500,
        "description": "Everything in Basic, plus structure and peer support.",
        "features": ["Everything in Basic", "Slack community access"],
    },
    {
        "slug": "premium",
        "name": "Premium",
        "level": 30,
        "price_eur_month": 100,
        "price_eur_year": 1000,
        "description": "Everything in Main, plus courses and feedback.",
        "features": ["Everything in Main", "All mini-courses"],
    },
]


def _ensure_pricing_tiers():
    from django.db import connection

    from payments.models import Tier

    for tier in PRICING_TIERS:
        Tier.objects.update_or_create(slug=tier["slug"], defaults=tier)
    connection.close()


def _goto_pricing(page, django_server, width=1280, height=900):
    _ensure_pricing_tiers()
    page.set_viewport_size({"width": width, "height": height})
    page.goto(f"{django_server}/pricing", wait_until="domcontentloaded")
    page.locator("#pricing-section").scroll_into_view_if_needed()


def _paid_prices(page):
    return {
        "basic": page.locator('[data-tier-card="basic"] .tier-price').inner_text(),
        "main": page.locator('[data-tier-card="main"] .tier-price').inner_text(),
        "premium": page.locator('[data-tier-card="premium"] .tier-price').inner_text(),
    }


def _billing_control_metrics(page):
    toggle = page.locator("#billing-toggle")
    dot = page.locator("#billing-toggle-dot")
    monthly_label = page.locator("#monthly-label")
    annual_label = page.locator("#annual-label")
    return {
        "toggle_box": toggle.bounding_box(),
        "dot_box": dot.bounding_box(),
        "toggle_class": toggle.get_attribute("class"),
        "dot_class": dot.get_attribute("class"),
        "monthly_class": monthly_label.get_attribute("class"),
        "annual_class": annual_label.get_attribute("class"),
        "pressed": toggle.get_attribute("aria-pressed"),
    }


def test_pricing_billing_toggle_defaults_to_annual(django_server, page):
    _goto_pricing(page, django_server)

    expect(page.locator("#billing-toggle")).to_have_attribute(
        "aria-pressed", "true"
    )
    assert "text-muted-foreground" in page.locator(
        "#monthly-label"
    ).get_attribute("class")
    assert "text-foreground" in page.locator("#annual-label").get_attribute(
        "class"
    )
    assert _paid_prices(page) == {
        "basic": "€200",
        "main": "€500",
        "premium": "€1000",
    }
    for tier in ["basic", "main", "premium"]:
        cta = page.locator(f'[data-tier-card="{tier}"] .tier-cta-link')
        assert cta.get_attribute("href") == cta.get_attribute(
            "data-link-annual"
        )


def test_pricing_billing_toggle_keyboard_switches_monthly_and_back(
    django_server, page
):
    _goto_pricing(page, django_server)
    toggle = page.locator("#billing-toggle")

    toggle.focus()
    expect(toggle).to_be_focused()
    page.keyboard.press("Space")

    expect(toggle).to_have_attribute("aria-pressed", "false")
    assert _paid_prices(page) == {
        "basic": "€20",
        "main": "€50",
        "premium": "€100",
    }
    for tier in ["basic", "main", "premium"]:
        cta = page.locator(f'[data-tier-card="{tier}"] .tier-cta-link')
        assert cta.get_attribute("href") == cta.get_attribute(
            "data-link-monthly"
        )

    page.keyboard.press("Enter")
    expect(toggle).to_have_attribute("aria-pressed", "true")
    assert _paid_prices(page)["main"] == "€500"


@pytest.mark.parametrize("width", [320, 393, 768, 1024, 1280])
def test_pricing_billing_toggle_stays_centered_without_body_overflow(
    django_server, page, width
):
    _goto_pricing(page, django_server, width=width, height=851)

    metrics = page.evaluate("""
        () => {
            const section = document.querySelector('#pricing-section');
            const group = document.querySelector('#billing-toggle')
                .closest('.flex');
            const groupBox = group.getBoundingClientRect();
            const sectionBox = section.getBoundingClientRect();
            return {
                scrollWidth: document.documentElement.scrollWidth,
                innerWidth: window.innerWidth,
                groupLeft: groupBox.left,
                groupRight: groupBox.right,
                groupCenter: groupBox.left + groupBox.width / 2,
                sectionCenter: sectionBox.left + sectionBox.width / 2,
            };
        }
    """)

    assert metrics["scrollWidth"] <= metrics["innerWidth"]
    assert metrics["groupLeft"] >= 0
    assert metrics["groupRight"] <= metrics["innerWidth"]
    assert abs(metrics["groupCenter"] - metrics["sectionCenter"]) <= 2

    annual_label = page.locator("#annual-label").bounding_box()
    toggle = page.locator("#billing-toggle").bounding_box()
    monthly_label = page.locator("#monthly-label").bounding_box()
    assert monthly_label["x"] + monthly_label["width"] < toggle["x"]
    assert toggle["x"] + toggle["width"] < annual_label["x"]


@pytest.mark.parametrize("width,height", [(1280, 900), (393, 851)])
def test_home_and_pricing_use_matching_billing_toggle_pattern(
    django_server, page, width, height
):
    _ensure_pricing_tiers()
    page.set_viewport_size({"width": width, "height": height})
    page.goto(f"{django_server}/#tiers", wait_until="domcontentloaded")
    page.locator("#tiers").scroll_into_view_if_needed()
    home = _billing_control_metrics(page)

    page.goto(f"{django_server}/pricing", wait_until="domcontentloaded")
    page.locator("#pricing-section").scroll_into_view_if_needed()
    pricing = _billing_control_metrics(page)

    assert pricing["pressed"] == home["pressed"] == "true"
    assert pricing["toggle_class"] == home["toggle_class"]
    assert pricing["dot_class"] == home["dot_class"]
    assert pricing["monthly_class"] == home["monthly_class"]
    assert pricing["annual_class"] == home["annual_class"]
    assert pricing["toggle_box"]["width"] == home["toggle_box"]["width"]
    assert pricing["toggle_box"]["height"] == home["toggle_box"]["height"]
    assert pricing["dot_box"]["width"] == home["dot_box"]["width"]
    assert pricing["dot_box"]["height"] == home["dot_box"]["height"]
