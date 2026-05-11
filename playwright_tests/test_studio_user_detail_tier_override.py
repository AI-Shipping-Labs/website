"""End-to-end coverage for the inline tier-override block on the Studio
user detail page (issue #562).

The standalone /studio/users/tier-override/ page is regression-locked
in the last scenario: this issue ADDS an in-context surface, it must
not change the standalone page's redirects or visible controls.

Usage:
    uv run pytest playwright_tests/test_studio_user_detail_tier_override.py -v
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


def _make_override(user_email, tier_slug, granted_by_email, days=30):
    """Create an active TierOverride for the test fixtures."""
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
        is_active=True,
    )
    connection.close()
    return override


def _set_stripe_customer(email, stripe_id):
    from accounts.models import User

    User.objects.filter(email=email).update(stripe_customer_id=stripe_id)
    connection.close()


def _user_id_for(email):
    from accounts.models import User

    pk = User.objects.get(email=email).pk
    connection.close()
    return pk


def _active_overrides(email):
    """Return the list of active TierOverride rows for assertions."""
    from accounts.models import TierOverride, User

    user = User.objects.get(email=email)
    rows = list(
        TierOverride.objects
        .filter(user=user, is_active=True)
        .select_related('override_tier'),
    )
    connection.close()
    return rows


def _all_overrides(email):
    from accounts.models import TierOverride, User

    user = User.objects.get(email=email)
    rows = list(
        TierOverride.objects
        .filter(user=user)
        .select_related('override_tier'),
    )
    connection.close()
    return rows


def _csrf_cookie(context):
    for cookie in context.cookies():
        if cookie['name'] == 'csrftoken':
            return cookie['value']
    return ''


@pytest.mark.django_db(transaction=True)
class TestAdminUpgradesFreeMemberInline:
    """Admin upgrades a Free member's tier without leaving the user page."""

    def test_upgrade_main_one_month(self, django_server, browser):
        _ensure_tiers()
        staff_email = 'tier-admin@test.com'
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _create_user('free@test.com', tier_slug='free')
        member_pk = _user_id_for('free@test.com')

        context = _auth_context(browser, staff_email)
        page = context.new_page()

        page.goto(
            f'{django_server}/studio/users/{member_pk}/',
            wait_until='domcontentloaded',
        )

        # 1. Tier row shows Free with the Default badge (no Stripe id).
        tier_cell = page.locator('[data-testid="user-detail-tier"]')
        assert tier_cell.count() == 1
        assert 'Free' in tier_cell.inner_text()
        badge = page.locator('[data-testid="user-detail-tier-badge"]')
        assert badge.get_attribute('data-tier-source') == 'default'
        assert 'Default' in badge.inner_text()

        # 2. The inline form lists Basic, Main, Premium and five durations.
        tier_options = page.locator(
            '[data-testid="user-detail-tier-override-tier-option"]',
        )
        assert tier_options.count() == 3
        slugs = [
            tier_options.nth(i).get_attribute('data-tier-slug')
            for i in range(tier_options.count())
        ]
        assert slugs == ['basic', 'main', 'premium']
        durations = page.locator(
            '[data-testid="user-detail-tier-override-duration"]',
        )
        assert durations.count() == 5
        durations_text = [
            durations.nth(i).get_attribute('data-duration')
            for i in range(durations.count())
        ]
        assert durations_text == [
            '14 days', '1 month', '3 months', '6 months', '12 months',
        ]

        # 3. Select Main and click 1 month. The radio is visually hidden
        # (peer sr-only) and the wrapping <label> is the click target,
        # so we check(force=True) to bypass the visibility heuristic.
        page.locator(
            'input[name="tier_id"][data-tier-slug="main"]',
        ).check(force=True)
        page.locator(
            '[data-testid="user-detail-tier-override-duration"]'
            '[data-duration="1 month"]',
        ).click()
        page.wait_for_load_state('domcontentloaded')

        # 4. The page reloads at the same URL.
        assert page.url.rstrip('/').endswith(f'/studio/users/{member_pk}')

        # 5. Success flash mentions the user and the new tier.
        body = page.content()
        assert 'free@test.com' in body
        assert 'Main' in body

        # 6. Tier row now shows Main (override) with the Override badge.
        tier_cell = page.locator('[data-testid="user-detail-tier"]')
        assert 'Main (override)' in tier_cell.inner_text()
        badge = page.locator('[data-testid="user-detail-tier-badge"]')
        assert badge.get_attribute('data-tier-source') == 'override'
        assert 'Override' in badge.inner_text()

        # 7. The Base line shows the stored subscription tier.
        base = page.locator('[data-testid="user-detail-tier-base"]')
        assert base.count() == 1
        assert 'Base: Free' in base.inner_text()

        # 8. The inline create form is gone; the revoke button is visible.
        assert page.locator(
            '[data-testid="user-detail-tier-override-form"]',
        ).count() == 0
        assert page.locator(
            '[data-testid="user-detail-tier-override-revoke"]',
        ).count() == 1

        # 9. A TierOverride row exists in the DB.
        rows = _active_overrides('free@test.com')
        assert len(rows) == 1
        assert rows[0].override_tier.slug == 'main'
        assert rows[0].granted_by.email == staff_email

        context.close()


@pytest.mark.django_db(transaction=True)
class TestAdminRevokesActiveOverride:
    """Admin revokes an active override from the user detail page."""

    def test_revoke_returns_to_detail(self, django_server, browser):
        _ensure_tiers()
        staff_email = 'revoke-admin@test.com'
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _create_user('main-override@test.com', tier_slug='basic')
        _make_override('main-override@test.com', 'main', staff_email, days=30)
        member_pk = _user_id_for('main-override@test.com')

        context = _auth_context(browser, staff_email)
        page = context.new_page()

        page.goto(
            f'{django_server}/studio/users/{member_pk}/',
            wait_until='domcontentloaded',
        )

        # Tier row shows Main (override) with the Override badge.
        tier_cell = page.locator('[data-testid="user-detail-tier"]')
        assert 'Main (override)' in tier_cell.inner_text()
        badge = page.locator('[data-testid="user-detail-tier-badge"]')
        assert badge.get_attribute('data-tier-source') == 'override'

        # Auto-accept the confirm() prompt and click revoke.
        page.once('dialog', lambda d: d.accept())
        page.locator(
            '[data-testid="user-detail-tier-override-revoke"]',
        ).click()
        page.wait_for_load_state('domcontentloaded')

        # Still on the detail page (not redirected to standalone).
        assert page.url.rstrip('/').endswith(f'/studio/users/{member_pk}')
        assert '/studio/users/tier-override/' not in page.url

        # Tier row now shows Basic (no override). Stripe not set, so
        # the badge is Default; this user was originally on Basic.
        tier_cell = page.locator('[data-testid="user-detail-tier"]')
        assert 'Basic' in tier_cell.inner_text()
        assert 'override' not in tier_cell.inner_text().lower()
        badge = page.locator('[data-testid="user-detail-tier-badge"]')
        assert badge.get_attribute('data-tier-source') in ('stripe', 'default')

        # The inline create form is back; revoke button is gone.
        assert page.locator(
            '[data-testid="user-detail-tier-override-form"]',
        ).count() == 1
        assert page.locator(
            '[data-testid="user-detail-tier-override-revoke"]',
        ).count() == 0

        # DB row is inactive.
        assert _active_overrides('main-override@test.com') == []

        context.close()


@pytest.mark.django_db(transaction=True)
class TestPremiumMemberNoUpgradePath:
    """Premium member: inline block shows the highest-tier message."""

    def test_highest_tier_message(self, django_server, browser):
        _ensure_tiers()
        staff_email = 'peak-admin@test.com'
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _create_user('premium-user@test.com', tier_slug='premium')
        member_pk = _user_id_for('premium-user@test.com')

        context = _auth_context(browser, staff_email)
        page = context.new_page()

        page.goto(
            f'{django_server}/studio/users/{member_pk}/',
            wait_until='domcontentloaded',
        )

        highest = page.locator(
            '[data-testid="user-detail-tier-override-highest"]',
        )
        assert highest.count() == 1
        assert 'highest tier' in highest.inner_text().lower()

        assert page.locator(
            '[data-testid="user-detail-tier-override-tier-option"]',
        ).count() == 0
        assert page.locator(
            '[data-testid="user-detail-tier-override-duration"]',
        ).count() == 0

        tier_cell = page.locator('[data-testid="user-detail-tier"]')
        assert 'Premium' in tier_cell.inner_text()
        badge = page.locator('[data-testid="user-detail-tier-badge"]')
        assert badge.get_attribute('data-tier-source') != 'override'

        context.close()


@pytest.mark.django_db(transaction=True)
class TestNoDowngradeOrHoldSteady:
    """Inline form must never offer same/lower tiers; POSTs are rejected."""

    def test_main_user_only_sees_premium_option(
        self, django_server, browser,
    ):
        _ensure_tiers()
        staff_email = 'down-admin@test.com'
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _create_user('main@test.com', tier_slug='main')
        member_pk = _user_id_for('main@test.com')

        context = _auth_context(browser, staff_email)
        page = context.new_page()

        page.goto(
            f'{django_server}/studio/users/{member_pk}/',
            wait_until='domcontentloaded',
        )

        options = page.locator(
            '[data-testid="user-detail-tier-override-tier-option"]',
        )
        assert options.count() == 1
        assert options.first.get_attribute('data-tier-slug') == 'premium'

        # Hand-crafted POST with Basic should be rejected.
        from payments.models import Tier
        basic_id = Tier.objects.get(slug='basic').pk
        connection.close()

        csrf = _csrf_cookie(context)
        response = context.request.post(
            f'{django_server}/studio/users/{member_pk}/tier-override/create',
            form={
                'csrfmiddlewaretoken': csrf,
                'tier_id': str(basic_id),
                'duration': '1 month',
            },
            headers={'X-CSRFToken': csrf, 'Referer': django_server},
            max_redirects=0,
        )
        # Server redirects back to the detail page (302), no TierOverride row.
        assert response.status in (302, 303)
        assert f'/studio/users/{member_pk}/' in response.headers.get(
            'location', '',
        )
        assert _all_overrides('main@test.com') == []

        # Reload the detail page: tier row still Main, no Override badge.
        page.goto(
            f'{django_server}/studio/users/{member_pk}/',
            wait_until='domcontentloaded',
        )
        tier_cell = page.locator('[data-testid="user-detail-tier"]')
        assert 'Main' in tier_cell.inner_text()
        assert 'override' not in tier_cell.inner_text().lower()

        context.close()


@pytest.mark.django_db(transaction=True)
class TestReplaceExistingOverride:
    """Creating a new override replaces the existing active one.

    The middle navigation step (history link round-trip) is intentional —
    it exercises the cross-link to the standalone page and confirms the
    inline form remains hidden while an override is active.
    """

    def test_full_replace_flow(self, django_server, browser):
        _ensure_tiers()
        staff_email = 'replace-admin@test.com'
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _create_user('replaceme@test.com', tier_slug='free')
        _make_override('replaceme@test.com', 'basic', staff_email, days=14)
        member_pk = _user_id_for('replaceme@test.com')

        context = _auth_context(browser, staff_email)
        page = context.new_page()

        page.goto(
            f'{django_server}/studio/users/{member_pk}/',
            wait_until='domcontentloaded',
        )
        # Active override visible.
        tier_cell = page.locator('[data-testid="user-detail-tier"]')
        assert 'Basic (override)' in tier_cell.inner_text()
        # Create form NOT rendered while an active override exists.
        assert page.locator(
            '[data-testid="user-detail-tier-override-form"]',
        ).count() == 0

        # Click "View full override history" — confirms the link target.
        history_link = page.locator(
            '[data-testid="user-detail-tier-override-history-link"]',
        )
        assert history_link.count() == 1
        assert (
            'replaceme@test.com'
            in history_link.get_attribute('href')
        )
        history_link.click()
        page.wait_for_load_state('domcontentloaded')
        assert '/studio/users/tier-override/' in page.url

        # Back to the detail page; revoke; create a new override.
        page.goto(
            f'{django_server}/studio/users/{member_pk}/',
            wait_until='domcontentloaded',
        )
        page.once('dialog', lambda d: d.accept())
        page.locator(
            '[data-testid="user-detail-tier-override-revoke"]',
        ).click()
        page.wait_for_load_state('domcontentloaded')

        # Pick Main and click 3 months (radio is sr-only -> force check).
        page.locator(
            'input[name="tier_id"][data-tier-slug="main"]',
        ).check(force=True)
        page.locator(
            '[data-testid="user-detail-tier-override-duration"]'
            '[data-duration="3 months"]',
        ).click()
        page.wait_for_load_state('domcontentloaded')

        # Exactly one active override, pointing at Main.
        active = _active_overrides('replaceme@test.com')
        assert len(active) == 1
        assert active[0].override_tier.slug == 'main'

        # Original Basic override is inactive.
        all_rows = _all_overrides('replaceme@test.com')
        basic_rows = [r for r in all_rows if r.override_tier.slug == 'basic']
        assert len(basic_rows) == 1
        assert basic_rows[0].is_active is False

        tier_cell = page.locator('[data-testid="user-detail-tier"]')
        assert 'Main (override)' in tier_cell.inner_text()

        context.close()


@pytest.mark.django_db(transaction=True)
class TestTierSourceBadge:
    """Stripe-derived vs default badge for non-overridden users."""

    def test_stripe_vs_default(self, django_server, browser):
        _ensure_tiers()
        staff_email = 'badge-admin@test.com'
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _create_user('stripe-paid@test.com', tier_slug='basic')
        _set_stripe_customer('stripe-paid@test.com', 'cus_test_123')
        _create_user('default-free@test.com', tier_slug='free')

        paid_pk = _user_id_for('stripe-paid@test.com')
        default_pk = _user_id_for('default-free@test.com')

        context = _auth_context(browser, staff_email)
        page = context.new_page()

        page.goto(
            f'{django_server}/studio/users/{paid_pk}/',
            wait_until='domcontentloaded',
        )
        tier_cell = page.locator('[data-testid="user-detail-tier"]')
        assert 'Basic' in tier_cell.inner_text()
        badge = page.locator('[data-testid="user-detail-tier-badge"]')
        assert badge.get_attribute('data-tier-source') == 'stripe'
        assert 'From Stripe' in badge.inner_text()

        page.goto(
            f'{django_server}/studio/users/{default_pk}/',
            wait_until='domcontentloaded',
        )
        tier_cell = page.locator('[data-testid="user-detail-tier"]')
        assert 'Free' in tier_cell.inner_text()
        badge = page.locator('[data-testid="user-detail-tier-badge"]')
        assert badge.get_attribute('data-tier-source') == 'default'
        assert 'Default' in badge.inner_text()

        context.close()


@pytest.mark.django_db(transaction=True)
class TestNonStaffCannotReachEndpoint:
    """Non-staff users cannot POST to the inline override endpoint."""

    def test_member_post_is_blocked(self, django_server, browser):
        _ensure_tiers()
        staff_email = 'gate-admin@test.com'
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _create_user('member@test.com', tier_slug='free')
        _create_user('victim@test.com', tier_slug='free')
        victim_pk = _user_id_for('victim@test.com')

        from payments.models import Tier
        main_id = Tier.objects.get(slug='main').pk
        connection.close()

        # Authenticate as a regular (non-staff) member.
        context = _auth_context(browser, 'member@test.com')
        csrf = _csrf_cookie(context)
        response = context.request.post(
            f'{django_server}/studio/users/{victim_pk}/tier-override/create',
            form={
                'csrfmiddlewaretoken': csrf,
                'tier_id': str(main_id),
                'duration': '1 month',
            },
            headers={'X-CSRFToken': csrf, 'Referer': django_server},
            max_redirects=0,
        )
        # @staff_required: non-staff -> 403; anonymous -> login redirect.
        # In either case, no TierOverride row may exist for victim.
        assert response.status in (302, 303, 403)
        assert _all_overrides('victim@test.com') == []

        context.close()


@pytest.mark.django_db(transaction=True)
class TestStandalonePageUnchanged:
    """Issue #562 must not change /studio/users/tier-override/ semantics."""

    def test_standalone_create_and_revoke_redirect_to_standalone(
        self, django_server, browser,
    ):
        _ensure_tiers()
        staff_email = 'legacy-admin@test.com'
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _create_user('legacy@test.com', tier_slug='free')
        legacy_pk = _user_id_for('legacy@test.com')

        context = _auth_context(browser, staff_email)
        page = context.new_page()

        # Open the standalone page with the email prefilled.
        page.goto(
            f'{django_server}/studio/users/tier-override/'
            f'?email=legacy@test.com',
            wait_until='domcontentloaded',
        )

        # The standalone page still renders its create form heading.
        body = page.content()
        assert 'Tier Overrides' in body
        # No <h2>Active Override</h2> visible yet (the HTML COMMENT
        # ``<!-- Active Override -->`` is always present in the template
        # markup, so we look for the heading element specifically).
        assert page.locator('h2', has_text='Active Override').count() == 0
        # Pick Basic radio and click 1 month duration. The standalone
        # form re-uses the same DURATION_CHOICES so the button label
        # is the same.
        from payments.models import Tier
        basic_id = Tier.objects.get(slug='basic').pk
        connection.close()
        page.locator(
            f'input[name="tier_id"][value="{basic_id}"]',
        ).check(force=True)
        page.locator('button[type="submit"][value="1 month"]').click()
        page.wait_for_load_state('domcontentloaded')

        # Redirect back to the standalone page (NOT the detail page).
        assert '/studio/users/tier-override/' in page.url
        assert 'email=legacy%40test.com' in page.url.replace('@', '%40') \
            or 'email=legacy@test.com' in page.url
        assert f'/studio/users/{legacy_pk}/' not in page.url

        # Active override card visible with Revoke Override button.
        # Look for the heading element so we don't match the HTML
        # comment marker in the template source.
        assert page.locator('h2', has_text='Active Override').count() == 1
        assert page.locator(
            'button[type="submit"]', has_text='Revoke Override',
        ).count() == 1

        # Click Revoke Override; confirm; still on the standalone page.
        page.once('dialog', lambda d: d.accept())
        page.locator('button[type="submit"]', has_text='Revoke Override').click()
        page.wait_for_load_state('domcontentloaded')
        assert '/studio/users/tier-override/' in page.url
        assert f'/studio/users/{legacy_pk}/' not in page.url

        context.close()
