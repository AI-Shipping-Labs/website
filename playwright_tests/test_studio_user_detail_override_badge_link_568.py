"""End-to-end coverage for the clickable Override badge on the Studio
user detail page (issue #568).

The badge added in #562 is decorative text; #568 wraps it in an ``<a>``
that points at the EXISTING per-user surface at
``/studio/users/tier-override/?email=<email>``. No new route, no new
view, no new template — the badge is a shortcut to a page that already
exists. These tests lock in the wiring:

- the override badge is an anchor with the correct ``?email=...`` href
- emails with ``+`` and ``@`` are URL-encoded (``%2B``, ``%40``)
- clicking the badge lands on the existing per-user lookup result
- non-override badges (``stripe``, ``default``) stay plain ``<span>``
- non-staff cannot reach the user detail page in the first place

Usage:
    uv run pytest playwright_tests/test_studio_user_detail_override_badge_link_568.py -v
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


def _user_id_for(email):
    from accounts.models import User

    pk = User.objects.get(email=email).pk
    connection.close()
    return pk


@pytest.mark.django_db(transaction=True)
class TestOverrideBadgeIsAClickableAnchor:
    """The Override badge is wrapped in an <a> with the correct href."""

    def test_badge_anchor_targets_tier_override_page_for_user(
        self, django_server, browser,
    ):
        _ensure_tiers()
        staff_email = 'badge-link-admin@test.com'
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _create_user('overridden@test.com', tier_slug='free')
        _make_override('overridden@test.com', 'main', staff_email, days=30)
        member_pk = _user_id_for('overridden@test.com')

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f'{django_server}/studio/users/{member_pk}/',
            wait_until='domcontentloaded',
        )

        # 1. Badge present and marked as an override badge.
        badge = page.locator('[data-testid="user-detail-tier-badge"]')
        assert badge.count() == 1
        assert badge.get_attribute('data-tier-source') == 'override'
        assert 'Override' in badge.inner_text()

        # 2. The badge sits inside an <a> wrapper (NOT a plain <span>).
        #    Use the anchor's own testid so we anchor on the actual
        #    clickable element rather than a parent traversal heuristic.
        anchor = page.locator(
            '[data-testid="user-detail-tier-badge-link"]',
        )
        assert anchor.count() == 1
        assert anchor.evaluate('el => el.tagName').lower() == 'a'

        # 3. The href points at the existing tier-override page with the
        #    user's email URL-encoded as a query parameter.
        href = anchor.get_attribute('href')
        assert href is not None
        assert href.startswith('/studio/users/tier-override/?email=')
        # @ -> %40 (urlencode), so the raw '@' must NOT appear in the
        # email= portion of the query string.
        assert 'email=overridden%40test.com' in href
        assert 'email=overridden@test.com' not in href

        # 4. The hover-affordance title is set.
        assert (
            anchor.get_attribute('title')
            == "View this user's tier overrides"
        )

        context.close()

    def test_clicking_badge_navigates_to_per_user_overrides_page(
        self, django_server, browser,
    ):
        _ensure_tiers()
        staff_email = 'click-badge-admin@test.com'
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _create_user('clickme@test.com', tier_slug='free')
        _make_override('clickme@test.com', 'main', staff_email, days=30)
        member_pk = _user_id_for('clickme@test.com')

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f'{django_server}/studio/users/{member_pk}/',
            wait_until='domcontentloaded',
        )

        page.locator(
            '[data-testid="user-detail-tier-badge-link"]',
        ).click()
        page.wait_for_load_state('domcontentloaded')

        # Landed on the existing tier-override page with ?email=.
        assert '/studio/users/tier-override/' in page.url
        assert (
            'email=clickme%40test.com' in page.url
            or 'email=clickme@test.com' in page.url
        )

        # The destination page renders the per-user blocks. We assert
        # on the heading + the revoke button (Active Override card) and
        # the email surfaces somewhere in the rendered body.
        body = page.content()
        assert 'clickme@test.com' in body
        # Active Override card visible because the fixture override is
        # still active.
        assert page.get_by_role(
            'heading', name='Active Override', exact=True,
        ).count() == 1
        assert page.locator(
            'button[type="submit"]', has_text='Revoke Override',
        ).count() == 1

        context.close()


@pytest.mark.django_db(transaction=True)
class TestOverrideBadgeHrefUrlEncodesEmails:
    """Emails with '+' and '@' must round-trip via the urlencode filter."""

    def test_plus_and_at_are_percent_encoded(
        self, django_server, browser,
    ):
        _ensure_tiers()
        staff_email = 'plus-admin@test.com'
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _create_user('alex+test@example.com', tier_slug='free')
        _make_override(
            'alex+test@example.com', 'basic', staff_email, days=14,
        )
        member_pk = _user_id_for('alex+test@example.com')

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f'{django_server}/studio/users/{member_pk}/',
            wait_until='domcontentloaded',
        )

        anchor = page.locator(
            '[data-testid="user-detail-tier-badge-link"]',
        )
        href = anchor.get_attribute('href')
        # '+' must be %2B, NOT '+' or '%20', and '@' must be %40.
        assert 'email=alex%2Btest%40example.com' in href, (
            f'href={href!r} did not contain the percent-encoded form '
            'email=alex%2Btest%40example.com — the |urlencode filter is '
            'either missing or the wrong filter was used.'
        )
        # Sanity: the raw email shape must NOT be present, otherwise the
        # server would parse the '+' as a space and the lookup would fail.
        assert 'email=alex+test@example.com' not in href

        # Click through and confirm the destination page finds the user.
        anchor.click()
        page.wait_for_load_state('domcontentloaded')
        assert '/studio/users/tier-override/' in page.url
        # The lookup result must show the user's literal email, not an
        # empty search or an error.
        body = page.content()
        assert 'alex+test@example.com' in body
        # Active Override card is rendered for the located user.
        assert page.get_by_role(
            'heading', name='Active Override', exact=True,
        ).count() == 1

        context.close()


@pytest.mark.django_db(transaction=True)
class TestNonOverrideBadgesStayPlainSpans:
    """Stripe-derived and Default badges must NOT be wrapped in <a>."""

    def test_stripe_and_default_badges_are_spans(
        self, django_server, browser,
    ):
        _ensure_tiers()
        staff_email = 'plain-admin@test.com'
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        # Default badge: free user, no Stripe customer id, no override.
        _create_user('plain-free@test.com', tier_slug='free')
        # Stripe badge: paid user with a Stripe customer id, no override.
        paid = _create_user('plain-paid@test.com', tier_slug='basic')
        from accounts.models import User
        User.objects.filter(pk=paid.pk).update(
            stripe_customer_id='cus_test_568',
        )
        connection.close()

        default_pk = _user_id_for('plain-free@test.com')
        paid_pk = _user_id_for('plain-paid@test.com')

        context = _auth_context(browser, staff_email)
        page = context.new_page()

        # --- Default badge user ---
        page.goto(
            f'{django_server}/studio/users/{default_pk}/',
            wait_until='domcontentloaded',
        )
        badge = page.locator('[data-testid="user-detail-tier-badge"]')
        assert badge.count() == 1
        assert badge.get_attribute('data-tier-source') == 'default'
        # The badge itself is a <span> (not an <a>).
        assert badge.evaluate('el => el.tagName').lower() == 'span'
        # No badge-link anchor wrapper is rendered for non-override badges.
        assert page.locator(
            '[data-testid="user-detail-tier-badge-link"]',
        ).count() == 0

        # --- Stripe badge user ---
        page.goto(
            f'{django_server}/studio/users/{paid_pk}/',
            wait_until='domcontentloaded',
        )
        badge = page.locator('[data-testid="user-detail-tier-badge"]')
        assert badge.count() == 1
        assert badge.get_attribute('data-tier-source') == 'stripe'
        assert badge.evaluate('el => el.tagName').lower() == 'span'
        assert page.locator(
            '[data-testid="user-detail-tier-badge-link"]',
        ).count() == 0

        context.close()


@pytest.mark.django_db(transaction=True)
class TestNonStaffCannotReachUserDetailPage:
    """Existing @staff_required gating: non-staff never see the badge."""

    def test_member_redirected_away_from_user_detail(
        self, django_server, browser,
    ):
        _ensure_tiers()
        staff_email = 'gate-badge-admin@test.com'
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _create_user('regular@test.com', tier_slug='free')
        _create_user('victim@test.com', tier_slug='free')
        _make_override('victim@test.com', 'main', staff_email, days=30)
        victim_pk = _user_id_for('victim@test.com')

        # Authenticate as a non-staff member.
        context = _auth_context(browser, 'regular@test.com')
        page = context.new_page()
        response = page.goto(
            f'{django_server}/studio/users/{victim_pk}/',
            wait_until='domcontentloaded',
        )

        # Either the response itself was a 403 or the browser was
        # redirected to a login / non-studio destination. In every case
        # the badge anchor must NOT be rendered in the response body.
        if response is not None:
            # The studio user detail page is gated; non-staff hits get
            # either a 403 page or a redirect to login/dashboard.
            assert response.status in (200, 302, 303, 403)
        assert (
            f'/studio/users/{victim_pk}/' not in page.url
            or response is None
            or response.status in (302, 303, 403)
        )
        # Authoritative check: no override badge anchor anywhere in the
        # rendered HTML the non-staff user receives.
        assert page.locator(
            '[data-testid="user-detail-tier-badge-link"]',
        ).count() == 0
        assert page.locator(
            '[data-testid="user-detail-tier-badge"]',
        ).count() == 0

        context.close()
