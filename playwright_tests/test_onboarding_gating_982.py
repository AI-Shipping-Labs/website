"""Playwright E2E tests for onboarding paid-gating (issue #982).

Onboarding feeds the personalized plan and the 1:1 founder call -- both
paid-member benefits. These scenarios verify the user-visible contract:

- A paid (Basic) member sees the dashboard onboarding prompt and can enter
  the flow.
- A Free member (no override) sees no onboarding entry point on the
  dashboard, and a direct hit to ``/onboarding/`` (or ``/onboarding/
  questions``) is redirected to the dashboard -- never an error page.
- A Free-base member with an ACTIVE override is treated as paid (prompt
  shown, flow enterable); an EXPIRED override loses access.
- The request-a-call ``Finish onboarding`` CTA is not handed to a Free
  member.
- Anonymous visitors keep the existing login redirect.

The form-first path is pinned on (``ONBOARDING_AI_ENABLED=false``) so the
self-ID screen is deterministic. Screenshots land in
``.tmp/aisl-issue-982-screenshots`` for tester review.
"""

import datetime
import os
from pathlib import Path

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

pytestmark = pytest.mark.local_only

SCREENSHOT_DIR = Path(__file__).parent.parent / ".tmp" / "aisl-issue-982-screenshots"


def _shot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=True)


def _force_form_first_onboarding():
    """Pin the form-first path so the self-ID screen is deterministic."""
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    IntegrationSetting.objects.update_or_create(
        key="ONBOARDING_AI_ENABLED",
        defaults={
            "value": "false",
            "is_secret": False,
            "group": "llm",
            "description": "",
        },
    )
    clear_config_cache()
    connection.close()


def _reset():
    """Re-seed onboarding data, pin the form path, clear responses."""
    import importlib

    from django.apps import apps as django_apps

    seed_module = importlib.import_module(
        "questionnaires.migrations.0003_seed_personas_and_onboarding",
    )
    seed_module.seed(django_apps, None)

    from questionnaires.models import Response

    Response.objects.all().delete()
    connection.close()
    _force_form_first_onboarding()


def _add_override(email, override_slug="main", *, days=14, is_active=True):
    """Attach a TierOverride to the member, expiring ``days`` from now."""
    from django.utils import timezone

    from accounts.models import TierOverride, User
    from payments.models import Tier

    user = User.objects.get(email=email)
    TierOverride.objects.create(
        user=user,
        original_tier=user.tier,
        override_tier=Tier.objects.get(slug=override_slug),
        expires_at=timezone.now() + datetime.timedelta(days=days),
        is_active=is_active,
    )
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestPaidMemberGetsOnboarding:
    @pytest.mark.core
    def test_basic_member_prompt_and_flow(self, django_server, browser):
        _ensure_tiers()
        _reset()
        _create_user("paid982@test.com", tier_slug="basic", email_verified=True)

        context = _auth_context(browser, "paid982@test.com")
        page = context.new_page()

        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        prompt = page.locator('[data-testid="onboarding-prompt"]')
        prompt.wait_for(state="visible")
        _shot(page, "paid_dashboard_prompt")

        page.locator('[data-testid="onboarding-prompt-cta"]').click()
        page.locator('[data-testid="onboarding-title"]').wait_for(state="visible")
        # The paid member reaches the self-ID screen and can proceed.
        assert page.locator('[data-testid="onboarding-identify-form"]').count() == 1


@pytest.mark.django_db(transaction=True)
class TestFreeMemberNoOnboarding:
    @pytest.mark.core
    def test_free_member_no_dashboard_prompt(self, django_server, browser):
        _ensure_tiers()
        _reset()
        _create_user("free982@test.com", tier_slug="free", email_verified=True)

        context = _auth_context(browser, "free982@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        # No onboarding entry point anywhere on the dashboard.
        assert page.locator('[data-testid="onboarding-prompt"]').count() == 0
        _shot(page, "free_dashboard_no_prompt")

    @pytest.mark.core
    def test_free_member_direct_url_redirects_to_dashboard(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset()
        _create_user("freeurl982@test.com", tier_slug="free", email_verified=True)

        context = _auth_context(browser, "freeurl982@test.com")
        page = context.new_page()

        # /onboarding/ -> dashboard, no self-ID screen, no error page.
        page.goto(f"{django_server}/onboarding/", wait_until="domcontentloaded")
        assert page.url.rstrip("/") == django_server.rstrip("/")
        assert page.locator('[data-testid="onboarding-title"]').count() == 0
        assert page.locator('[data-testid="onboarding-identify-form"]').count() == 0
        _shot(page, "free_onboarding_redirected")

        # /onboarding/questions -> dashboard, not a fill-in form.
        page.goto(
            f"{django_server}/onboarding/questions", wait_until="domcontentloaded",
        )
        assert page.url.rstrip("/") == django_server.rstrip("/")
        assert page.locator(
            '[data-testid="questionnaire-response-form"]'
        ).count() == 0

    @pytest.mark.core
    def test_free_member_no_request_call_onboarding_cta(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset()
        _create_user("freerc982@test.com", tier_slug="free", email_verified=True)

        context = _auth_context(browser, "freerc982@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/request-a-call", wait_until="domcontentloaded",
        )
        # Not handed a "Finish onboarding" CTA into a flow they cannot enter.
        assert page.locator(
            '[data-testid="request-call-onboarding-cta"]'
        ).count() == 0
        _shot(page, "free_request_call_no_cta")


@pytest.mark.django_db(transaction=True)
class TestOverrideMatrix:
    @pytest.mark.core
    def test_active_override_member_gets_onboarding(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset()
        _create_user("ovactive982@test.com", tier_slug="free", email_verified=True)
        _add_override("ovactive982@test.com", "main")

        context = _auth_context(browser, "ovactive982@test.com")
        page = context.new_page()

        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        page.locator('[data-testid="onboarding-prompt"]').wait_for(state="visible")
        _shot(page, "override_dashboard_prompt")

        page.locator('[data-testid="onboarding-prompt-cta"]').click()
        page.locator('[data-testid="onboarding-title"]').wait_for(state="visible")
        assert page.locator('[data-testid="onboarding-identify-form"]').count() == 1

    @pytest.mark.core
    def test_expired_override_member_loses_onboarding(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset()
        _create_user("ovexpired982@test.com", tier_slug="free", email_verified=True)
        _add_override("ovexpired982@test.com", "main", days=-1)

        context = _auth_context(browser, "ovexpired982@test.com")
        page = context.new_page()

        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        assert page.locator('[data-testid="onboarding-prompt"]').count() == 0
        _shot(page, "expired_override_no_prompt")

        page.goto(f"{django_server}/onboarding/", wait_until="domcontentloaded")
        assert page.url.rstrip("/") == django_server.rstrip("/")
        assert page.locator('[data-testid="onboarding-title"]').count() == 0


@pytest.mark.django_db(transaction=True)
class TestAnonymous:
    @pytest.mark.core
    def test_anonymous_redirected_to_login(self, django_server, browser):
        context = browser.new_context()
        page = context.new_page()
        page.goto(f"{django_server}/onboarding/", wait_until="domcontentloaded")
        assert "/accounts/login/" in page.url
        assert page.locator('[data-testid="onboarding-title"]').count() == 0
