"""End-to-end coverage for the cross-cutting "Active overrides" list on
the standalone tier-override page (issue #567).

The list lives on /studio/users/tier-override/ and shows every
TierOverride with ``is_active=True`` and ``expires_at > now()``.

Usage:
    uv run pytest playwright_tests/test_studio_tier_overrides_list.py -v
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


def _clear_users_except_staff(staff_email):
    """Reset the users table so we control the row count exactly."""
    from accounts.models import User

    User.objects.exclude(email=staff_email).delete()
    connection.close()


def _clear_overrides():
    """Reset the TierOverride table so the list starts empty."""
    from accounts.models import TierOverride

    TierOverride.objects.all().delete()
    connection.close()


def _make_override(
    user_email, tier_slug, granted_by_email, *, days=30, is_active=True,
):
    """Create a TierOverride row with the given parameters."""
    from accounts.models import TierOverride, User
    from payments.models import Tier

    user = User.objects.get(email=user_email)
    tier = Tier.objects.get(slug=tier_slug)
    granted_by = User.objects.get(email=granted_by_email)
    override = TierOverride.objects.create(
        user=user,
        original_tier=user.tier,
        override_tier=tier,
        expires_at=timezone.now() + timedelta(days=days),
        granted_by=granted_by,
        is_active=is_active,
    )
    connection.close()
    return override


def _make_expired_override(user_email, tier_slug, granted_by_email):
    """Create a TierOverride row with is_active=True but expires_at in the past."""
    from accounts.models import TierOverride, User
    from payments.models import Tier

    user = User.objects.get(email=user_email)
    tier = Tier.objects.get(slug=tier_slug)
    granted_by = User.objects.get(email=granted_by_email)
    override = TierOverride.objects.create(
        user=user,
        original_tier=user.tier,
        override_tier=tier,
        expires_at=timezone.now() - timedelta(minutes=5),
        granted_by=granted_by,
        is_active=True,
    )
    connection.close()
    return override


def _user_id_for(email):
    from accounts.models import User

    pk = User.objects.get(email=email).pk
    connection.close()
    return pk


def _active_override_for(email):
    """Return the most recent active TierOverride row for ``email`` (or None)."""
    from accounts.models import TierOverride, User

    user = User.objects.get(email=email)
    override = (
        TierOverride.objects
        .filter(user=user, is_active=True)
        .select_related('override_tier')
        .order_by('-created_at')
        .first()
    )
    connection.close()
    return override


def _row_emails(page):
    """Return the ordered list of user emails currently in the list."""
    rows = page.locator('[data-testid="active-override-row"]')
    return [
        rows.nth(i).get_attribute('data-user-email')
        for i in range(rows.count())
    ]


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestAdminSeesEveryActiveOverrideSortedByExpiry:
    """Active overrides surface every active row, sorted soonest-first."""

    def test_list_contents_and_order(self, django_server, browser):
        _ensure_tiers()
        staff_email = 'list-admin@test.com'
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _clear_overrides()

        _create_user('urgent@test.com', tier_slug='free')
        _create_user('soon@test.com', tier_slug='free')
        _create_user('later@test.com', tier_slug='basic')
        _create_user('latest@test.com', tier_slug='free')
        _create_user('revoked@test.com', tier_slug='free')
        _create_user('expired@test.com', tier_slug='free')

        # Create out of order to prove sort comes from the queryset.
        _make_override('latest@test.com', 'main', staff_email, days=90)
        _make_override('urgent@test.com', 'basic', staff_email, days=2)
        _make_override('later@test.com', 'premium', staff_email, days=30)
        _make_override('soon@test.com', 'main', staff_email, days=14)

        # Two rows that must NOT appear in the list.
        _make_override(
            'revoked@test.com', 'main', staff_email, days=20, is_active=False,
        )
        _make_expired_override('expired@test.com', 'main', staff_email)

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f'{django_server}/studio/users/tier-override/',
            wait_until='domcontentloaded',
        )

        section = page.locator('[data-testid="active-overrides-section"]')
        assert section.count() == 1

        # Count badge shows "4 active".
        count_badge = page.locator('[data-testid="active-overrides-count"]')
        assert '4 active' in count_badge.inner_text()

        # Exactly four rows, in soonest-first order.
        emails = _row_emails(page)
        assert emails == [
            'urgent@test.com',
            'soon@test.com',
            'later@test.com',
            'latest@test.com',
        ]

        # Excluded rows do not appear.
        body = page.content()
        assert 'revoked@test.com' not in body
        assert 'expired@test.com' not in body

        # Each row shows the effective tier name and the granting admin.
        rows = page.locator('[data-testid="active-override-row"]')
        # Urgent row first: Basic, granted by staff.
        urgent = rows.nth(0)
        assert 'Basic' in urgent.locator(
            '[data-testid="active-override-effective-tier"]',
        ).inner_text()
        assert staff_email in urgent.locator(
            '[data-testid="active-override-set-by"]',
        ).inner_text()
        # Expires-at column shows both an absolute and relative hint.
        expires_cell = urgent.locator(
            '[data-testid="active-override-expires-at"]',
        ).inner_text()
        assert 'in ' in expires_cell

        context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestAdminRevokesOverrideFromList:
    """Revoke action removes the row and flips ``is_active`` to False."""

    def test_revoke_from_list(self, django_server, browser):
        _ensure_tiers()
        staff_email = 'revoke-list-admin@test.com'
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _clear_overrides()

        _create_user('revokeme@test.com', tier_slug='free')
        override = _make_override(
            'revokeme@test.com', 'main', staff_email, days=30,
        )

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f'{django_server}/studio/users/tier-override/',
            wait_until='domcontentloaded',
        )

        # The row is present.
        assert _row_emails(page) == ['revokeme@test.com']

        # Click Revoke in that row and accept the confirm() prompt.
        page.once('dialog', lambda d: d.accept())
        row = page.locator(
            '[data-testid="active-override-row"]'
            '[data-user-email="revokeme@test.com"]',
        )
        row.locator('[data-testid="active-override-revoke"]').click()
        page.wait_for_load_state('domcontentloaded')

        # We land back on the standalone page (with or without ?email).
        assert '/studio/users/tier-override/' in page.url

        # Success flash mentions the revoked user.
        body = page.content()
        assert 'revokeme@test.com' in body
        assert 'revoked' in body.lower()

        # The row is gone, empty state takes over.
        assert _row_emails(page) == []
        assert page.locator(
            '[data-testid="active-overrides-empty"]',
        ).count() == 1

        # The DB row is now inactive.
        override.refresh_from_db()
        assert override.is_active is False

        context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestAdminFollowsUserLinkToDetailPage:
    """Clicking the email column lands on the Studio user detail page."""

    def test_user_link_navigates_to_detail(self, django_server, browser):
        _ensure_tiers()
        staff_email = 'jump-admin@test.com'
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _clear_overrides()

        _create_user('jumpto@test.com', tier_slug='free')
        _make_override('jumpto@test.com', 'basic', staff_email, days=14)
        member_pk = _user_id_for('jumpto@test.com')

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f'{django_server}/studio/users/tier-override/',
            wait_until='domcontentloaded',
        )

        row = page.locator(
            '[data-testid="active-override-row"]'
            '[data-user-email="jumpto@test.com"]',
        )
        link = row.locator('[data-testid="active-override-user-link"]')
        assert link.get_attribute('href').rstrip('/').endswith(
            f'/studio/users/{member_pk}',
        )
        link.click()
        page.wait_for_load_state('domcontentloaded')

        assert page.url.rstrip('/').endswith(f'/studio/users/{member_pk}')
        # The detail page renders user information for this member.
        body = page.content()
        assert 'jumpto@test.com' in body

        context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestEmptyStateIsFriendly:
    """No active overrides -> a muted empty message, not a broken table."""

    def test_empty_state_renders(self, django_server, browser):
        _ensure_tiers()
        staff_email = 'empty-admin@test.com'
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _clear_overrides()

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f'{django_server}/studio/users/tier-override/',
            wait_until='domcontentloaded',
        )

        # Section is visible.
        assert page.locator(
            '[data-testid="active-overrides-section"]',
        ).count() == 1
        # Empty state copy.
        empty = page.locator('[data-testid="active-overrides-empty"]')
        assert empty.count() == 1
        assert 'No active overrides right now' in empty.inner_text()
        # Header badge reads "No active overrides".
        count_badge = page.locator('[data-testid="active-overrides-count"]')
        assert 'No active overrides' in count_badge.inner_text()
        # No table is rendered when empty.
        assert page.locator(
            '[data-testid="active-overrides-table"]',
        ).count() == 0
        assert page.locator(
            '[data-testid="active-override-row"]',
        ).count() == 0
        # The existing search-by-email form is still visible below.
        assert page.locator('form[method="get"] input[name="email"]').count() >= 1

        context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestCreationFlowStillWorks:
    """Granting a new override surfaces it in the list immediately."""

    def test_create_via_existing_form_appears_in_list(
        self, django_server, browser,
    ):
        _ensure_tiers()
        staff_email = 'create-admin@test.com'
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _clear_overrides()

        _create_user('newgrant@test.com', tier_slug='free')

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f'{django_server}/studio/users/tier-override/',
            wait_until='domcontentloaded',
        )

        # List starts empty.
        assert page.locator(
            '[data-testid="active-overrides-empty"]',
        ).count() == 1

        # Search for the user.
        page.fill(
            'form[method="get"] input[name="email"]', 'newgrant@test.com',
        )
        page.locator(
            'form[method="get"] button[type="submit"]',
        ).first.click()
        page.wait_for_load_state('domcontentloaded')

        # User detail + create form render below.
        body = page.content()
        assert 'newgrant@test.com' in body
        assert 'Create Override' in body

        # Pick Main and click the "1 month" duration submit button.
        from payments.models import Tier
        main_id = Tier.objects.get(slug='main').pk
        connection.close()
        page.locator(
            f'input[name="tier_id"][value="{main_id}"]',
        ).check(force=True)
        page.locator(
            'button[type="submit"][value="1 month"]',
        ).click()
        page.wait_for_load_state('domcontentloaded')

        # Redirected back to the standalone page with ?email=...
        assert '/studio/users/tier-override/' in page.url
        assert 'newgrant' in page.url

        # The new override appears in the cross-cutting list.
        assert _row_emails(page) == ['newgrant@test.com']
        row = page.locator(
            '[data-testid="active-override-row"]'
            '[data-user-email="newgrant@test.com"]',
        )
        assert 'Main' in row.locator(
            '[data-testid="active-override-effective-tier"]',
        ).inner_text()

        # The per-user Active Override card below the list also shows the
        # same override (existing behaviour preserved).
        assert page.get_by_role('heading', name='Active Override', exact=True).count() == 1

        # DB sanity: exactly one active row, expiring ~1 month from now.
        override = _active_override_for('newgrant@test.com')
        assert override is not None
        assert override.override_tier.slug == 'main'
        delta_days = (override.expires_at - timezone.now()).days
        # 1 month is at least 27 days even in February; cap at 32.
        assert 27 <= delta_days <= 32

        context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestExpiredButActiveRowsExcluded:
    """Rows past their expiry are filtered out even if ``is_active=True``."""

    def test_expired_rows_excluded(self, django_server, browser):
        _ensure_tiers()
        staff_email = 'expired-admin@test.com'
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _clear_overrides()

        _create_user('keepme@test.com', tier_slug='free')
        _create_user('stale@test.com', tier_slug='free')

        _make_override('keepme@test.com', 'main', staff_email, days=7)
        _make_expired_override('stale@test.com', 'main', staff_email)

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f'{django_server}/studio/users/tier-override/',
            wait_until='domcontentloaded',
        )

        assert _row_emails(page) == ['keepme@test.com']
        body = page.content()
        assert 'stale@test.com' not in body

        context.close()
