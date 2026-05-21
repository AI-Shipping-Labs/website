"""Playwright coverage for the Studio bounce-state surfaces (issue #766).

Five scenarios per the groomed spec:

1. Operator triages a permanently bounced user from the list (filter +
   tooltip + detail card).
2. Clearing the bounce filter returns every user and drops the param.
3. A clean user shows no bounce section at all.
4. The Soft / Permanent filters narrow correctly.
5. A soft-bounced user's detail page shows the SOFT card (not Permanent).
"""

import os
from datetime import timedelta

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402

# Local-only because this module seeds DB state and uses session-cookie
# injection. See ``_docs/testing-guidelines.md``.
pytestmark = pytest.mark.local_only

ADMIN_EMAIL = "bounce-admin@test.com"
BOUNCER_EMAIL = "bouncer@test.com"
SOFT_EMAIL = "softie@test.com"
CLEAN_EMAIL = "clean@test.com"


def _clear_users_except(staff_email):
    from accounts.models import User

    User.objects.exclude(email=staff_email).delete()
    connection.close()


def _seed_bouncer(email, *, state, recorded_at, diagnostic):
    """Seed an unverified-but-real bouncing user so the row hits the list."""
    from accounts.models import User

    _create_user(email, email_verified=True)
    user = User.objects.get(email=email)
    user.bounce_state = state
    user.bounce_recorded_at = recorded_at
    user.last_bounce_diagnostic = diagnostic
    user.save(update_fields=[
        "bounce_state", "bounce_recorded_at", "last_bounce_diagnostic",
    ])
    pk = user.pk
    connection.close()
    return pk


@pytest.mark.django_db(transaction=True)
class TestStudioUserBounceState:
    def test_operator_triages_permanent_bounce_from_list_and_detail(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _create_staff_user(ADMIN_EMAIL)
        _clear_users_except(ADMIN_EMAIL)
        recorded_at = timezone.now() - timedelta(hours=4)
        perm_pk = _seed_bouncer(
            BOUNCER_EMAIL,
            state="permanent",
            recorded_at=recorded_at,
            diagnostic="550 5.1.1 Mailbox does not exist",
        )
        _create_user(CLEAN_EMAIL, email_verified=True)

        context = _auth_context(browser, ADMIN_EMAIL)
        page = context.new_page()

        # Filter by ``bounce=permanent``.
        page.goto(
            f"{django_server}/studio/users/?bounce=permanent",
            wait_until="domcontentloaded",
        )
        row = page.locator(f'[data-testid="user-row-{perm_pk}"]')
        assert row.is_visible(), "bouncer row missing from permanent filter"
        # Clean row is filtered out.
        assert page.locator("tbody tr", has_text=CLEAN_EMAIL).count() == 0

        # Tooltip includes the new bounce line alongside the existing
        # newsletter / slack lines.
        tooltip = row.get_attribute("title") or ""
        assert "Bounce: permanent" in tooltip
        assert "Newsletter:" in tooltip

        # Click into the detail page; the new card is visible.
        row.locator('[data-testid="user-view-link"]').click()
        page.wait_for_load_state("domcontentloaded")
        card = page.locator('[data-testid="user-detail-bounce-section"]')
        assert card.is_visible()
        assert "Permanent" in card.inner_text()
        assert "550 5.1.1 Mailbox does not exist" in card.inner_text()

    def test_clearing_filter_returns_every_user(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _create_staff_user(ADMIN_EMAIL)
        _clear_users_except(ADMIN_EMAIL)
        _seed_bouncer(
            BOUNCER_EMAIL,
            state="permanent",
            recorded_at=timezone.now() - timedelta(hours=4),
            diagnostic="550 5.1.1",
        )
        _create_user(CLEAN_EMAIL, email_verified=True)

        context = _auth_context(browser, ADMIN_EMAIL)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/?bounce=permanent",
            wait_until="domcontentloaded",
        )
        # Confirm the filter chip row is rendered.
        chip_row = page.locator('[data-testid="user-bounce-filter-chips"]')
        assert chip_row.is_visible()
        # Click "Any" to clear.
        chip_row.locator('a[data-bounce-filter="any"]').click()
        page.wait_for_load_state("domcontentloaded")

        # The clean row reappears.
        assert page.locator("tbody tr", has_text=CLEAN_EMAIL).count() >= 1
        # ``bounce=permanent`` is no longer the active state.
        assert "bounce=permanent" not in page.url

    def test_clean_user_has_no_bounce_section(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _create_staff_user(ADMIN_EMAIL)
        _clear_users_except(ADMIN_EMAIL)
        _create_user(CLEAN_EMAIL, email_verified=True)
        from accounts.models import User

        clean_pk = User.objects.get(email=CLEAN_EMAIL).pk
        connection.close()

        context = _auth_context(browser, ADMIN_EMAIL)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{clean_pk}/",
            wait_until="domcontentloaded",
        )

        # The Bounce status section is not present; verification / Slack
        # / tags sections still are.
        assert page.locator(
            '[data-testid="user-detail-bounce-section"]'
        ).count() == 0
        assert page.locator(
            '[data-testid="user-tags-section"]'
        ).is_visible()

    def test_filter_chips_narrow_by_soft_and_permanent_state(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _create_staff_user(ADMIN_EMAIL)
        _clear_users_except(ADMIN_EMAIL)
        _seed_bouncer(
            SOFT_EMAIL,
            state="soft",
            recorded_at=timezone.now() - timedelta(hours=2),
            diagnostic="421 4.4.5 Server busy",
        )
        _seed_bouncer(
            BOUNCER_EMAIL,
            state="permanent",
            recorded_at=timezone.now() - timedelta(hours=4),
            diagnostic="550 5.1.1 Mailbox does not exist",
        )

        context = _auth_context(browser, ADMIN_EMAIL)
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/users/?bounce=soft",
            wait_until="domcontentloaded",
        )
        assert page.locator("tbody tr", has_text=SOFT_EMAIL).count() >= 1
        assert page.locator("tbody tr", has_text=BOUNCER_EMAIL).count() == 0

        page.goto(
            f"{django_server}/studio/users/?bounce=permanent",
            wait_until="domcontentloaded",
        )
        assert page.locator("tbody tr", has_text=BOUNCER_EMAIL).count() >= 1
        assert page.locator("tbody tr", has_text=SOFT_EMAIL).count() == 0

    def test_soft_bouncer_detail_shows_soft_card_not_permanent(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _create_staff_user(ADMIN_EMAIL)
        _clear_users_except(ADMIN_EMAIL)
        soft_pk = _seed_bouncer(
            SOFT_EMAIL,
            state="soft",
            recorded_at=timezone.now() - timedelta(hours=2),
            diagnostic="421 4.4.5 Server busy",
        )

        context = _auth_context(browser, ADMIN_EMAIL)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{soft_pk}/",
            wait_until="domcontentloaded",
        )

        card = page.locator('[data-testid="user-detail-bounce-section"]')
        assert card.is_visible()
        # The label is the Soft display value; Permanent must not appear
        # in the card body.
        card_text = card.inner_text()
        assert "Soft" in card_text
        assert "Permanent" not in card_text
        assert "421 4.4.5 Server busy" in card_text
