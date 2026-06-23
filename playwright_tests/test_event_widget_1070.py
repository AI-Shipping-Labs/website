"""Playwright E2E for the event claim widget + Studio screens (issue #1070).

These cover the user-facing hydration states (claim/dedup, signed-out CTA,
under-level block, paused flag, markdown embed render + deactivation) and
the Studio subscription/widget screens. The dispatcher, signing, dedup,
gating, and API contracts are covered by Django tests under
``triggers/tests/`` and ``api/tests/test_triggers_api.py``.

Usage:
    uv run pytest playwright_tests/test_event_widget_1070.py -v
"""

import os

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from django.db import connection  # noqa: E402

# Local-only: seeds the DB, sets integration config, injects session cookies.
# ``django_db(transaction=True)`` makes pytest-django build an isolated test
# database before the in-process ``django_server`` fixture starts, and lets
# the server thread see rows the test wrote (same pattern as the API-token
# Playwright suite).
pytestmark = [
    pytest.mark.local_only,
    pytest.mark.core,
    pytest.mark.django_db(transaction=True),
]


def _set_triggers_enabled(value):
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    IntegrationSetting.objects.update_or_create(
        key="TRIGGERS_ENABLED",
        defaults={"value": "true" if value else "false", "group": "triggers"},
    )
    clear_config_cache()
    connection.close()


def _reset():
    from content.models import Article
    from integrations.models import IntegrationSetting
    from triggers.models import (
        EventEmission,
        EventWidget,
        TriggerSubscription,
        WebhookDelivery,
    )

    WebhookDelivery.objects.all().delete()
    EventEmission.objects.all().delete()
    TriggerSubscription.objects.all().delete()
    EventWidget.objects.all().delete()
    Article.objects.filter(slug__startswith="evt-widget-").delete()
    IntegrationSetting.objects.filter(key="TRIGGERS_ENABLED").delete()
    connection.close()


def _make_widget(slug, *, min_level=5, active=True, event_name=None):
    from triggers.models import EventWidget

    widget = EventWidget.objects.create(
        slug=slug,
        event_name=event_name or slug.replace("-", "_"),
        min_level=min_level,
        claim_label="Claim your credit",
        claim_body="Get your v0 credit now.",
        signin_cta="Sign in to claim",
        claimed_label="Claimed — check your email",
        is_active=active,
    )
    connection.close()
    return widget


def _make_article(slug, widget_slug):
    from django.utils import timezone

    from content.models import Article

    article, _ = Article.objects.get_or_create(
        slug=slug,
        defaults={
            "title": "Widget Host Article",
            "date": timezone.now().date(),
            "content_markdown": (
                "Intro paragraph.\n\n"
                f"```eventwidget\nslug: {widget_slug}\n```\n\n"
                "Outro paragraph."
            ),
            "required_level": 0,
            "published": True,
        },
    )
    # Re-save to ensure content_html is rendered with current markdown.
    article.content_markdown = (
        "Intro paragraph.\n\n"
        f"```eventwidget\nslug: {widget_slug}\n```\n\n"
        "Outro paragraph."
    )
    article.save()
    connection.close()
    return article


def test_registered_member_claims_and_dedup_persists(browser, django_server):
    _reset()
    _set_triggers_enabled(True)
    _make_widget("evt-v0-claim", min_level=5)
    _make_article("evt-widget-claim", "evt-v0-claim")
    _create_user("claimer@test.com", tier_slug="free", email_verified=True)

    context = _auth_context(browser, "claimer@test.com")
    page = context.new_page()
    try:
        page.goto(f"{django_server}/blog/evt-widget-claim")
        claim_btn = page.locator('[data-testid="event-widget-claim"]')
        expect(claim_btn).to_be_visible()
        expect(page.locator('[data-testid="event-widget-body"]')).to_contain_text(
            "Get your v0 credit"
        )

        claim_btn.click()
        claimed = page.locator('[data-testid="event-widget-claimed"]')
        expect(claimed).to_be_visible()
        expect(claimed).to_contain_text("Claimed")

        # Reload: dedup persisted, still shows claimed, no claim button.
        page.reload()
        expect(page.locator('[data-testid="event-widget-claimed"]')).to_be_visible()
        expect(page.locator('[data-testid="event-widget-claim"]')).to_have_count(0)

        from triggers.models import EventEmission

        self_count = EventEmission.objects.filter(event_name="evt_v0_claim").count()
        connection.close()
        assert self_count == 1
    finally:
        context.close()
        _reset()


def test_anonymous_sees_signin_cta(browser, django_server):
    _reset()
    _set_triggers_enabled(True)
    _make_widget("evt-v0-claim", min_level=5)
    _make_article("evt-widget-anon", "evt-v0-claim")

    context = browser.new_context()
    page = context.new_page()
    try:
        page.goto(f"{django_server}/blog/evt-widget-anon")
        cta = page.locator('[data-testid="event-widget-signin"]')
        expect(cta).to_be_visible()
        expect(cta).to_contain_text("Sign in to claim")
        # No claim button for an anonymous visitor.
        expect(page.locator('[data-testid="event-widget-claim"]')).to_have_count(0)
        # The shortcode is expanded to the hydration placeholder in the
        # rendered article body (the raw fence does not survive into the
        # rendered prose). The Django markdown tests assert the exact
        # expansion; here we confirm the placeholder node exists.
        expect(page.locator("[data-event-widget]")).to_have_count(1)
    finally:
        context.close()
        _reset()


def test_under_level_member_is_blocked(browser, django_server):
    _reset()
    _set_triggers_enabled(True)
    # min_level=20 (Main); a free member is below it.
    _make_widget("evt-premium-claim", min_level=20)
    _make_article("evt-widget-underlevel", "evt-premium-claim")
    _create_user("underlevel@test.com", tier_slug="free", email_verified=True)

    context = _auth_context(browser, "underlevel@test.com")
    page = context.new_page()
    try:
        page.goto(f"{django_server}/blog/evt-widget-underlevel")
        expect(
            page.locator('[data-testid="event-widget-under-level"]')
        ).to_be_visible()
        expect(page.locator('[data-testid="event-widget-claim"]')).to_have_count(0)

        from triggers.models import EventEmission

        count = EventEmission.objects.filter(event_name="evt_premium_claim").count()
        connection.close()
        assert count == 0
    finally:
        context.close()
        _reset()


def test_paused_state_when_flag_off(browser, django_server):
    _reset()
    _set_triggers_enabled(False)
    _make_widget("evt-v0-claim", min_level=5)
    _make_article("evt-widget-paused", "evt-v0-claim")
    _create_user("paused@test.com", tier_slug="free", email_verified=True)

    context = _auth_context(browser, "paused@test.com")
    page = context.new_page()
    try:
        page.goto(f"{django_server}/blog/evt-widget-paused")
        expect(page.locator('[data-testid="event-widget-paused"]')).to_be_visible()
        expect(page.locator('[data-testid="event-widget-claim"]')).to_have_count(0)
    finally:
        context.close()
        _reset()


def test_deactivated_widget_renders_nothing(browser, django_server):
    _reset()
    _set_triggers_enabled(True)
    _make_widget("evt-v0-claim", min_level=5, active=False)
    _make_article("evt-widget-inactive", "evt-v0-claim")
    _create_user("inactive@test.com", tier_slug="free", email_verified=True)

    context = _auth_context(browser, "inactive@test.com")
    page = context.new_page()
    try:
        page.goto(f"{django_server}/blog/evt-widget-inactive")
        # The placeholder is present in HTML (rendered at save) but JS clears
        # it because the widget is inactive (state=unavailable): no claim
        # button, no sign-in CTA, and the pre-hydration Loading text is gone.
        expect(page.locator('[data-testid="event-widget-claim"]')).to_have_count(0)
        expect(page.locator('[data-testid="event-widget-signin"]')).to_have_count(0)
        widget_node = page.locator("[data-event-widget]")
        expect(widget_node).to_have_count(1)
        expect(widget_node.locator(".event-widget-loading")).to_have_count(0)
        expect(widget_node).to_have_text("")
        # The page itself still rendered (outro paragraph present).
        expect(page.locator("body")).to_contain_text("Outro paragraph")
    finally:
        context.close()
        _reset()


def test_studio_subscription_create_masks_secret(browser, django_server):
    _reset()
    _create_staff_user("studio-triggers@test.com")

    context = _auth_context(browser, "studio-triggers@test.com")
    page = context.new_page()
    try:
        page.goto(f"{django_server}/studio/triggers/subscriptions/new/")
        page.fill('[name="property_filter"]', '{"name": "v0_workshop"}')
        page.fill('[name="target_url"]', "https://handler.example.com/hook")
        page.fill('[name="secret"]', "supersecret")
        page.locator('[data-testid="subscription-save"]').click()

        # Lands back on the list; the secret is masked, never shown.
        expect(page.locator('[data-testid="subscriptions-list"]')).to_be_visible()
        expect(
            page.locator('[data-testid="subscription-secret-masked"]').first
        ).to_be_visible()
        expect(page.locator("body")).not_to_contain_text("supersecret")
    finally:
        context.close()
        _reset()


def test_studio_widget_screen_shows_embed_shortcode(browser, django_server):
    _reset()
    _create_staff_user("studio-triggers@test.com")
    _make_widget("evt-v0-claim", min_level=5)

    context = _auth_context(browser, "studio-triggers@test.com")
    page = context.new_page()
    try:
        page.goto(f"{django_server}/studio/triggers/widgets/")
        embed = page.locator('[data-testid="widget-embed-shortcode"]').first
        expect(embed).to_be_visible()
        expect(embed).to_contain_text("slug: evt-v0-claim")
        # A copy button sits next to the shortcode and copies the literal
        # multi-line value (newlines preserved via data-copy-text).
        copy_btn = page.locator('[data-testid="widget-embed-copy"]').first
        expect(copy_btn).to_be_visible()
        copy_value = copy_btn.get_attribute("data-copy-text")
        assert copy_value == "```eventwidget\nslug: evt-v0-claim\n```", copy_value
    finally:
        context.close()
        _reset()
