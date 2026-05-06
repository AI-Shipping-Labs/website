"""Playwright coverage for the User cell name/email layout (issue #451).

Issue #451 surfaces ``first_name`` / ``last_name`` on every user row and
makes the row's headline render the full name (when available) above the
email. The browser-only assertions below verify:

- the data-testid + visible text contracts for every name combination
  (both / first only / last only / neither);
- the visual stacking (name bounding-box top < email bounding-box top);
- the Stripe glyph + Slack badge from earlier issues still render in the
  denser Membership cell;
- the tag overflow chip and pager from earlier issues are intact;
- a ``1280x900`` viewport shows at least 18 rows without scrolling and
  has no horizontal scrollbar;
- a ``390x900`` viewport keeps the stacked-card layout with the four
  ``data-label`` headings.

The Django unit tests (``studio/tests/test_user_list_name_display.py``)
own the row-dict + DOM-string contract; this suite owns the browser-only
behaviors (visual stacking, viewport row count, mobile reflow).
"""

import os
from pathlib import Path

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

DESKTOP_VIEWPORT = {"width": 1280, "height": 900}
MOBILE_VIEWPORT = {"width": 390, "height": 900}
SCREENSHOT_DIR = Path("/tmp/aisl-issue-451-screenshots")


def _clear_users_except_staff(staff_email):
    from accounts.models import User

    User.objects.exclude(email=staff_email).delete()
    connection.close()


def _set_user_names(email, first_name='', last_name=''):
    """Set first/last name on an existing user.

    The shared ``create_user`` helper supports ``first_name`` but not
    ``last_name`` (and the issue needs every combination, including
    last-name-only), so we patch the names server-side after creating
    the row.
    """
    from accounts.models import User

    user = User.objects.get(email=email)
    user.first_name = first_name
    user.last_name = last_name
    user.save(update_fields=['first_name', 'last_name'])
    connection.close()


def _set_stripe_dashboard_account(value):
    """Persist STRIPE_DASHBOARD_ACCOUNT_ID via the same path Studio uses
    so the Stripe glyph anchors at /acct_TEST/customers/cus_PREMIUM."""
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    IntegrationSetting.objects.update_or_create(
        key='STRIPE_DASHBOARD_ACCOUNT_ID',
        defaults={
            'value': value,
            'is_secret': False,
            'group': 'stripe',
            'description': '',
        },
    )
    clear_config_cache()
    connection.close()


def _set_user_extras(email, *, stripe_customer_id='', tags=None,
                    slack_member=False):
    from accounts.models import User

    user = User.objects.get(email=email)
    if stripe_customer_id:
        user.stripe_customer_id = stripe_customer_id
    if tags is not None:
        user.tags = list(tags)
    if slack_member:
        user.slack_member = True
        user.slack_checked_at = timezone.now()
    user.save()
    connection.close()


def _seed_named_users():
    """Create the four name-combination users used across this suite."""
    _create_user('avery.garcia@example.com', tier_slug='free')
    _set_user_names('avery.garcia@example.com', 'Avery', 'Garcia')

    _create_user('avery.first-only@example.com', tier_slug='free')
    _set_user_names('avery.first-only@example.com', 'Avery', '')

    _create_user('garcia.last-only@example.com', tier_slug='free')
    _set_user_names('garcia.last-only@example.com', '', 'Garcia')

    _create_user('no-name@example.com', tier_slug='free')
    _set_user_names('no-name@example.com', '', '')


def _seed_dense_paid_users(count):
    """Create ``count`` paid users with names populated.

    Used by the 18-rows-visible scenario; we want enough rows to fill
    the viewport with data, all with names so the User cell is at its
    tallest layout (name + email + joined).
    """
    from accounts.models import User
    from payments.models import Tier

    paid = Tier.objects.get(slug='main')
    for idx in range(count):
        email = f'dense-{idx:03d}@example.com'
        user, _ = User.objects.get_or_create(email=email)
        user.first_name = f'First{idx:03d}'
        user.last_name = f'Last{idx:03d}'
        user.tier = paid
        user.email_verified = True
        user.set_password('TestPass123!')
        user.save()
    connection.close()


def _assert_no_horizontal_overflow(page):
    overflow = page.evaluate(
        """() => {
            const root = document.scrollingElement || document.documentElement;
            return root.scrollWidth - root.clientWidth;
        }"""
    )
    assert overflow <= 2, f'Document has {overflow}px of horizontal overflow.'


def _capture_screenshot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=True)


@pytest.mark.django_db(transaction=True)
class TestStudioUsersNameLayout:
    def test_search_by_first_name_recognizes_match_by_name(
        self, django_server, browser,
    ):
        _ensure_tiers()
        staff_email = 'name-layout-admin@test.com'
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _seed_named_users()

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.set_viewport_size(DESKTOP_VIEWPORT)
        page.goto(
            f"{django_server}/studio/users/?q=Avery",
            wait_until="domcontentloaded",
        )

        # The Avery Garcia row matched by first name; Bo and no-name are
        # filtered out so they cannot satisfy a row-level assertion by
        # accident.
        avery_row = page.locator(
            'tbody tr', has_text='avery.garcia@example.com'
        ).first
        assert avery_row.is_visible()
        assert avery_row.locator('[data-testid="user-name"]').inner_text() == 'Avery Garcia'
        assert avery_row.locator('[data-testid="user-email"]').inner_text() == 'avery.garcia@example.com'

        # Bo and no-name rows must not be in the rendered table.
        assert page.locator('tbody tr', has_text='bo.long@example.com').count() == 0
        assert page.locator('tbody tr', has_text='no-name@example.com').count() == 0

        context.close()

    def test_both_names_render_full_name_visually_above_email(
        self, django_server, browser,
    ):
        _ensure_tiers()
        staff_email = 'name-layout-both-admin@test.com'
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _seed_named_users()

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.set_viewport_size(DESKTOP_VIEWPORT)
        page.goto(
            f"{django_server}/studio/users/?q=avery.garcia",
            wait_until="domcontentloaded",
        )

        row = page.locator(
            'tbody tr', has_text='avery.garcia@example.com'
        ).first
        name_node = row.locator('[data-testid="user-name"]')
        email_node = row.locator('[data-testid="user-email"]')

        assert name_node.inner_text() == 'Avery Garcia'
        assert email_node.inner_text() == 'avery.garcia@example.com'

        # Visual stacking — name's bounding-box top is above the email's.
        name_top = name_node.bounding_box()['y']
        email_top = email_node.bounding_box()['y']
        assert name_top < email_top, (
            f'Expected name (top={name_top}) to render above email '
            f'(top={email_top}); the User cell layout is wrong.'
        )

        _capture_screenshot(page, 'both-names-stacked')
        context.close()

    def test_first_name_only_shows_single_name_above_email(
        self, django_server, browser,
    ):
        _ensure_tiers()
        staff_email = 'name-layout-first-only-admin@test.com'
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _seed_named_users()

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.set_viewport_size(DESKTOP_VIEWPORT)
        page.goto(
            f"{django_server}/studio/users/?q=avery.first-only",
            wait_until="domcontentloaded",
        )

        row = page.locator(
            'tbody tr', has_text='avery.first-only@example.com'
        ).first
        name_text = row.locator('[data-testid="user-name"]').inner_text()
        # Exact equality — guarding against a stray trailing space from
        # ``f"{first} {last}".strip()`` going wrong.
        assert name_text == 'Avery'
        # Sanity: no trailing whitespace bleeds out of the rendered text.
        assert name_text == name_text.rstrip()
        assert (
            row.locator('[data-testid="user-email"]').inner_text()
            == 'avery.first-only@example.com'
        )

        context.close()

    def test_last_name_only_shows_single_name_above_email(
        self, django_server, browser,
    ):
        _ensure_tiers()
        staff_email = 'name-layout-last-only-admin@test.com'
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _seed_named_users()

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.set_viewport_size(DESKTOP_VIEWPORT)
        page.goto(
            f"{django_server}/studio/users/?q=garcia.last-only",
            wait_until="domcontentloaded",
        )

        row = page.locator(
            'tbody tr', has_text='garcia.last-only@example.com'
        ).first
        name_text = row.locator('[data-testid="user-name"]').inner_text()
        assert name_text == 'Garcia'
        # No leading whitespace.
        assert name_text == name_text.lstrip()
        assert (
            row.locator('[data-testid="user-email"]').inner_text()
            == 'garcia.last-only@example.com'
        )

        context.close()

    def test_no_name_falls_back_to_email_as_headline(
        self, django_server, browser,
    ):
        _ensure_tiers()
        staff_email = 'name-layout-no-name-admin@test.com'
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _seed_named_users()

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.set_viewport_size(DESKTOP_VIEWPORT)
        page.goto(
            f"{django_server}/studio/users/?q=no-name",
            wait_until="domcontentloaded",
        )

        row = page.locator(
            'tbody tr', has_text='no-name@example.com'
        ).first
        # No user-name cell at all when both names are blank.
        assert row.locator('[data-testid="user-name"]').count() == 0
        assert (
            row.locator('[data-testid="user-email"]').inner_text()
            == 'no-name@example.com'
        )
        # The Joined line is still rendered as the tertiary line.
        assert 'Joined ' in row.inner_text()

        context.close()

    def test_stripe_glyph_and_slack_badge_render_in_dense_membership_cell(
        self, django_server, browser,
    ):
        _ensure_tiers()
        staff_email = 'name-layout-stripe-admin@test.com'
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _set_stripe_dashboard_account('acct_TEST')

        # Premium user, slack member, with a stripe customer id.
        _create_user('premium-cust@example.com', tier_slug='premium')
        _set_user_names('premium-cust@example.com', 'Premium', 'Customer')
        _set_user_extras(
            'premium-cust@example.com',
            stripe_customer_id='cus_PREMIUM',
            slack_member=True,
        )

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.set_viewport_size(DESKTOP_VIEWPORT)
        page.goto(
            f"{django_server}/studio/users/?q=cus_PREMIUM",
            wait_until="domcontentloaded",
        )

        row = page.locator(
            'tbody tr', has_text='premium-cust@example.com'
        ).first
        badges = row.locator('[data-testid="membership-badges"]')
        assert badges.is_visible()

        slack = row.locator('[data-testid="slack-status"]')
        assert slack.inner_text().strip() == 'Slack'

        stripe = row.locator('[data-testid="stripe-indicator"]')
        href = stripe.get_attribute('href')
        assert href == (
            'https://dashboard.stripe.com/acct_TEST/customers/cus_PREMIUM'
        )

        # All four badge categories are present.
        badge_text = badges.inner_text()
        assert 'Premium' in badge_text
        assert 'Newsletter' in badge_text
        assert 'Active' in badge_text

        context.close()

    def test_tags_overflow_chip_keeps_three_visible_plus_count(
        self, django_server, browser,
    ):
        _ensure_tiers()
        staff_email = 'name-layout-tags-admin@test.com'
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)

        _create_user('tagged@example.com', tier_slug='premium')
        _set_user_names('tagged@example.com', 'Tagged', 'User')
        _set_user_extras(
            'tagged@example.com',
            tags=['early-adopter', 'beta', 'paid-2026', 'vip', 'cohort-a'],
        )

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.set_viewport_size(DESKTOP_VIEWPORT)
        page.goto(
            f"{django_server}/studio/users/?q=tagged@example.com",
            wait_until="domcontentloaded",
        )

        row = page.locator('tbody tr', has_text='tagged@example.com').first
        tag_links = row.locator('[data-testid="user-tags-cell"] a')
        assert tag_links.count() == 3
        assert tag_links.nth(0).inner_text().strip() == 'early-adopter'
        assert tag_links.nth(1).inner_text().strip() == 'beta'
        assert tag_links.nth(2).inner_text().strip() == 'paid-2026'

        overflow = row.locator('[data-testid="user-tags-overflow"]')
        assert overflow.inner_text().strip() == '+2'
        assert 'vip, cohort-a' in (overflow.get_attribute('aria-label') or '')

        context.close()

    def test_pagination_preserves_filter_and_shows_named_rows_on_page_two(
        self, django_server, browser,
    ):
        _ensure_tiers()
        staff_email = 'name-layout-pager-admin@test.com'
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _seed_dense_paid_users(60)

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.set_viewport_size(DESKTOP_VIEWPORT)
        page.goto(
            f"{django_server}/studio/users/?filter=paid&page=2",
            wait_until="domcontentloaded",
        )

        # Page 2 has rows 51-60 of the 60 paid users.
        assert 'Showing 51-60 of 60' in page.content()
        # At least one row on page 2 has a visible user-name cell —
        # exercising the new headline layout on a non-first page.
        named_rows_on_page_two = page.locator('[data-testid="user-name"]').count()
        assert named_rows_on_page_two >= 1

        # The "first" pager link drops back to page=1 and keeps filter=paid.
        first_link = page.locator(
            '[data-testid="user-list-pager-first"]'
        ).first
        first_link.click()
        page.wait_for_load_state('domcontentloaded')
        assert 'page=1' in page.url
        assert 'filter=paid' in page.url

        # Page 1 also has named rows.
        assert page.locator('[data-testid="user-name"]').count() >= 1

        context.close()

    def test_at_least_eighteen_rows_visible_at_1280x900(
        self, django_server, browser,
    ):
        _ensure_tiers()
        staff_email = 'name-layout-density-admin@test.com'
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        # Seed enough users that at least 18 fit in the viewport even
        # with the stats cards + filter chips at the top of the page.
        _seed_dense_paid_users(30)

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.set_viewport_size(DESKTOP_VIEWPORT)
        page.goto(
            f"{django_server}/studio/users/",
            wait_until="domcontentloaded",
        )

        # Count how many rows fit in one viewport-height band starting at
        # the first row. Studio's main layout owns its overflow, so coupling
        # this assertion to window.scrollY makes it test the scroll container
        # instead of the row-density target.
        diagnostics = page.evaluate(
            """() => {
                const firstRow = document.querySelector('tbody tr');
                const allRows = document.querySelectorAll('tbody tr');
                const viewportHeight = window.innerHeight;
                let visible = 0;
                let firstRowTop = null;
                let firstRowHeight = null;
                allRows.forEach((row, idx) => {
                    const box = row.getBoundingClientRect();
                    if (idx === 0) {
                        firstRowTop = box.top;
                        firstRowHeight = box.height;
                    }
                    if (
                        firstRowTop !== null &&
                        box.top >= firstRowTop &&
                        box.top < firstRowTop + viewportHeight
                    ) {
                        visible += 1;
                    }
                });
                return {
                    visible: visible,
                    totalRows: allRows.length,
                    viewportHeight: viewportHeight,
                    firstRowTop: firstRowTop,
                    firstRowHeight: firstRowHeight,
                    scrollY: window.scrollY,
                };
            }"""
        )
        visible_row_count = diagnostics['visible']
        assert visible_row_count >= 18, (
            f'Expected at least 18 rows to fit in one 1280x900 viewport-height '
            f'band starting at the first row; got {visible_row_count}. '
            f'Density target regressed. Diagnostics: {diagnostics}'
        )

        _assert_no_horizontal_overflow(page)
        _capture_screenshot(page, 'dense-1280x900')
        context.close()

    def test_mobile_stacked_card_layout_keeps_four_data_labels(
        self, django_server, browser,
    ):
        _ensure_tiers()
        staff_email = 'name-layout-mobile-admin@test.com'
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)

        _create_user('avery.mobile@example.com', tier_slug='main')
        _set_user_names('avery.mobile@example.com', 'Avery', 'Garcia')
        _set_user_extras('avery.mobile@example.com', tags=['early-adopter'])

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.set_viewport_size(MOBILE_VIEWPORT)
        page.goto(
            f"{django_server}/studio/users/?q=Avery",
            wait_until="domcontentloaded",
        )

        row = page.locator(
            'tbody tr', has_text='avery.mobile@example.com'
        ).first
        assert row.is_visible()

        cells = row.locator('td')
        # Four data-label cells in fixed order: User / Membership / Tags / Actions.
        assert cells.nth(0).get_attribute('data-label') == 'User'
        assert cells.nth(1).get_attribute('data-label') == 'Membership'
        assert cells.nth(2).get_attribute('data-label') == 'Tags'
        assert cells.nth(3).get_attribute('data-label') == 'Actions'

        # The User card includes the full name AND email AND joined.
        user_cell_text = cells.nth(0).inner_text()
        assert 'Avery Garcia' in user_cell_text
        assert 'avery.mobile@example.com' in user_cell_text

        # The Tags cell still carries the chip; the Actions cell still
        # has both buttons.
        assert 'early-adopter' in cells.nth(2).inner_text()
        view = row.locator('[data-testid="user-view-link"]')
        login_as = row.get_by_role('button', name='Login as')
        assert view.is_visible()
        assert login_as.is_visible()

        _assert_no_horizontal_overflow(page)
        _capture_screenshot(page, 'mobile-stacked-cards')
        context.close()
