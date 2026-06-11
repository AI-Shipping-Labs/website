"""Tests for the /studio/users/ list density rework (issue #451).

Issue #451 reshapes the user list table from four columns
(User / Membership / Tags / Actions) to four leaner columns
(User / Status / Last login / Actions). The User cell now carries the
identity AND the tier pill (and an icon-only override pill) inline; the
Status column gets its own slot; the Last login column surfaces a fact
that was previously invisible; and a row-level ``<tr title="...">``
tooltip carries Slack ID, Stripe customer ID, Newsletter state, and
Slack workspace state on hover.

Tests in this module own:

- the table-header contract (exactly four headers, in order, with the
  Membership / Tags headers explicitly gone)
- the User cell render matrix (full_name vs email fallback)
- the tier + override pill placement inside the User cell
- the Status pill values
- the Last login two-line format and ``-- never --`` fallback
- the row tooltip surface

The Django unit tests own the rendered-string contract. The Playwright
suite owns visual stacking, viewport row counts, and mobile reflow.
"""

import datetime
import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import TierOverride
from payments.models import Tier
from studio.views.users import _row_tooltip

User = get_user_model()


def _row_html(html, user_pk):
    """Return the ``<tr>...</tr>`` slice for a given user pk.

    Scoping every assertion to the row prevents text from another row
    (or from the search-input placeholder, the active-tag chip, etc.)
    from satisfying an assertion that is meant to be about this user.
    """
    pattern = (
        r'<tr[^>]*data-testid="user-row-' + str(user_pk) + r'"[^>]*>'
        r'(.*?)'
        r'</tr>'
    )
    match = re.search(pattern, html, re.DOTALL)
    if not match:
        raise AssertionError(
            f'Could not locate user-row-{user_pk} row in rendered HTML.'
        )
    return match.group(0)


def _extract_div_text(row_html, testid):
    """Return the inner text of the first ``<div data-testid="X">`` in a row.

    Used to assert exact text (no leading / trailing whitespace) without
    pulling in a full HTML parser. Returns ``None`` when the test-id
    isn't present in the row, so callers can also use this to assert
    absence.
    """
    pattern = (
        r'<div[^>]*data-testid="' + re.escape(testid) + r'"[^>]*>'
        r'(.*?)'
        r'</div>'
    )
    match = re.search(pattern, row_html, re.DOTALL)
    if match is None:
        return None
    return match.group(1)


def _extract_tr_attrs(html, user_pk):
    """Return the attribute string of the ``<tr ...>`` open tag for a row."""
    pattern = r'<tr([^>]*data-testid="user-row-' + str(user_pk) + r'"[^>]*)>'
    match = re.search(pattern, html)
    if not match:
        raise AssertionError(
            f'Could not locate user-row-{user_pk} <tr> in rendered HTML.'
        )
    return match.group(1)


class UserListHeaderRowTest(TestCase):
    """The ``<thead>`` row must carry exactly the four new column headers.

    The chosen layout is documented in issue #451 and the spec asks for
    a hard regression guard so a future refactor cannot re-introduce the
    Membership or Tags columns without flipping this test.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        # A handful of users so the listing actually renders rows; the
        # table-header contract is independent of row content.
        cls.free = User.objects.create_user(
            email='free@example.com', password='testpass',
        )
        cls.paid = User.objects.create_user(
            email='paid@example.com', password='testpass',
            tier=Tier.objects.get(slug='main'),
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def _header_cells(self):
        response = self.client.get('/studio/users/')
        html = response.content.decode()
        # Scope to the user TABLE's thead (``bg-secondary``) so neither a
        # stray ``<th>`` elsewhere nor the membership-breakdown table's plain
        # ``<thead>`` (issue #923) can satisfy the assertion.
        thead_match = re.search(
            r'<thead class="bg-secondary"[^>]*>(.*?)</thead>', html, re.DOTALL,
        )
        self.assertIsNotNone(
            thead_match, 'No user-table <thead> in rendered users list.',
        )
        thead_html = thead_match.group(1)
        return re.findall(
            r'<th[^>]*>\s*(.*?)\s*</th>', thead_html, re.DOTALL,
        )

    def test_header_row_has_exactly_four_cells_in_order(self):
        cells = self._header_cells()
        self.assertEqual(
            cells,
            ['User', 'Status', 'Last login', 'Actions'],
        )

    def test_header_does_not_contain_membership(self):
        cells = self._header_cells()
        self.assertNotIn('Membership', cells)

    def test_header_does_not_contain_tags(self):
        cells = self._header_cells()
        self.assertNotIn('Tags', cells)


class UserListingFullNameRowDictTest(TestCase):
    """``_build_user_listing`` must surface every name field on every row."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        # Four users covering every name combination.
        cls.both = User.objects.create_user(
            email='avery.garcia@example.com', password='testpass',
            first_name='Avery', last_name='Garcia',
        )
        cls.first_only = User.objects.create_user(
            email='avery.first-only@example.com', password='testpass',
            first_name='Avery', last_name='',
        )
        cls.last_only = User.objects.create_user(
            email='garcia.last-only@example.com', password='testpass',
            first_name='', last_name='Garcia',
        )
        cls.neither = User.objects.create_user(
            email='no-name@example.com', password='testpass',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def _row_for(self, email):
        response = self.client.get('/studio/users/', {'q': email})
        for row in response.context['user_rows']:
            if row['email'] == email:
                return row
        raise AssertionError(f'No row for {email} in user_rows')

    def test_row_dict_exposes_full_name_when_both_names_set(self):
        row = self._row_for('avery.garcia@example.com')
        self.assertEqual(row['first_name'], 'Avery')
        self.assertEqual(row['last_name'], 'Garcia')
        self.assertEqual(row['full_name'], 'Avery Garcia')

    def test_row_dict_full_name_is_first_name_alone_when_last_blank(self):
        # Single-name users must NOT carry a stray trailing space — the
        # template renders ``row.full_name`` raw, so any whitespace would
        # show up in the DOM.
        row = self._row_for('avery.first-only@example.com')
        self.assertEqual(row['first_name'], 'Avery')
        self.assertEqual(row['last_name'], '')
        self.assertEqual(row['full_name'], 'Avery')

    def test_row_dict_full_name_is_last_name_alone_when_first_blank(self):
        # Same contract on the other side: no leading space.
        row = self._row_for('garcia.last-only@example.com')
        self.assertEqual(row['first_name'], '')
        self.assertEqual(row['last_name'], 'Garcia')
        self.assertEqual(row['full_name'], 'Garcia')

    def test_row_dict_full_name_empty_when_neither_name_set(self):
        # Empty string is the truthy sentinel the template branches on.
        row = self._row_for('no-name@example.com')
        self.assertEqual(row['first_name'], '')
        self.assertEqual(row['last_name'], '')
        self.assertEqual(row['full_name'], '')


class UserListingNameRenderingTest(TestCase):
    """The User cell renders different shapes for the four name combos."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.both = User.objects.create_user(
            email='avery.garcia@example.com', password='testpass',
            first_name='Avery', last_name='Garcia',
        )
        cls.first_only = User.objects.create_user(
            email='avery.first-only@example.com', password='testpass',
            first_name='Avery', last_name='',
        )
        cls.last_only = User.objects.create_user(
            email='garcia.last-only@example.com', password='testpass',
            first_name='', last_name='Garcia',
        )
        cls.neither = User.objects.create_user(
            email='no-name@example.com', password='testpass',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_both_names_render_full_name_above_email(self):
        response = self.client.get('/studio/users/?q=avery.garcia')
        row_html = _row_html(response.content.decode(), self.both.pk)

        # Both lines exist, with the documented attributes.
        self.assertIn('data-testid="user-name"', row_html)
        self.assertIn('data-testid="user-email"', row_html)
        # Exact-text assertions (no extra whitespace).
        self.assertEqual(_extract_div_text(row_html, 'user-name'), 'Avery Garcia')
        self.assertEqual(
            _extract_div_text(row_html, 'user-email'),
            'avery.garcia@example.com',
        )

    def test_both_names_name_div_appears_before_email_div(self):
        # The chosen layout puts the name above the email; the template
        # source order must reflect that so the visual stack is correct.
        response = self.client.get('/studio/users/?q=avery.garcia')
        row_html = _row_html(response.content.decode(), self.both.pk)
        name_idx = row_html.index('data-testid="user-name"')
        email_idx = row_html.index('data-testid="user-email"')
        self.assertLess(name_idx, email_idx)

    def test_both_names_use_truncate_with_title_attributes(self):
        # Long names/emails must not push the cell wider; both lines
        # carry the full string in the title attribute so a hover still
        # surfaces the full value.
        response = self.client.get('/studio/users/?q=avery.garcia')
        row_html = _row_html(response.content.decode(), self.both.pk)

        name_match = re.search(
            r'<div([^>]*data-testid="user-name"[^>]*)>',
            row_html,
        )
        self.assertIsNotNone(name_match)
        name_attrs = name_match.group(1)
        self.assertIn('truncate', name_attrs)
        self.assertIn('title="Avery Garcia"', name_attrs)
        self.assertIn('aria-label="Name Avery Garcia"', name_attrs)

        email_match = re.search(
            r'<div([^>]*data-testid="user-email"[^>]*)>',
            row_html,
        )
        self.assertIsNotNone(email_match)
        email_attrs = email_match.group(1)
        self.assertIn('truncate', email_attrs)
        self.assertIn('title="avery.garcia@example.com"', email_attrs)
        self.assertIn(
            'aria-label="Email avery.garcia@example.com"',
            email_attrs,
        )

    def test_first_name_only_renders_single_name_above_email(self):
        response = self.client.get('/studio/users/?q=avery.first-only')
        row_html = _row_html(response.content.decode(), self.first_only.pk)

        # The name line is present and is exactly "Avery" — no trailing
        # whitespace from a "first last".strip() with an empty last name.
        self.assertEqual(_extract_div_text(row_html, 'user-name'), 'Avery')
        self.assertEqual(
            _extract_div_text(row_html, 'user-email'),
            'avery.first-only@example.com',
        )
        # The title attribute also stays clean (no "Avery " with a space).
        self.assertIn('title="Avery"', row_html)
        self.assertNotIn('title="Avery "', row_html)

    def test_last_name_only_renders_single_name_above_email(self):
        response = self.client.get('/studio/users/?q=garcia.last-only')
        row_html = _row_html(response.content.decode(), self.last_only.pk)

        # Same contract as first-only but on the other side: no leading
        # whitespace.
        self.assertEqual(_extract_div_text(row_html, 'user-name'), 'Garcia')
        self.assertEqual(
            _extract_div_text(row_html, 'user-email'),
            'garcia.last-only@example.com',
        )
        self.assertIn('title="Garcia"', row_html)
        self.assertNotIn('title=" Garcia"', row_html)

    def test_no_name_falls_back_to_email_as_headline(self):
        response = self.client.get('/studio/users/?q=no-name')
        row_html = _row_html(response.content.decode(), self.neither.pk)

        # No ``user-name`` div at all — the User cell is just email + tier.
        self.assertNotIn('data-testid="user-name"', row_html)
        # Email is still rendered with the same data-testid so the
        # Stripe/Slack/scanability tests keep working.
        self.assertEqual(
            _extract_div_text(row_html, 'user-email'),
            'no-name@example.com',
        )

    def test_no_name_row_renders_email_exactly_once(self):
        # Issue #451: email-as-headline rows must NOT emit a secondary
        # email line. Two ``user-email`` divs in one row is the bug.
        response = self.client.get('/studio/users/?q=no-name')
        row_html = _row_html(response.content.decode(), self.neither.pk)
        self.assertEqual(row_html.count('data-testid="user-email"'), 1)

    def test_named_row_does_not_render_joined_line(self):
        # Issue #451 dropped the tertiary Joined line in favour of the
        # Last login column. Regression guard so nobody puts it back.
        response = self.client.get('/studio/users/?q=avery.garcia')
        row_html = _row_html(response.content.decode(), self.both.pk)
        self.assertNotIn('Joined ', row_html)


class UserListTierPillInsideUserCellTest(TestCase):
    """The tier pill (and icon-only override pill) sit inside the User cell."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.main = Tier.objects.get(slug='main')
        cls.premium = Tier.objects.get(slug='premium')

        cls.free_named = User.objects.create_user(
            email='free@example.com', password='testpass',
            first_name='Free', last_name='User',
        )
        cls.premium_named = User.objects.create_user(
            email='premium@example.com', password='testpass',
            first_name='Premium', last_name='User',
            tier=cls.premium,
        )

        # User on Free with an active upgrade to Premium via override.
        cls.upgraded = User.objects.create_user(
            email='upgraded@example.com', password='testpass',
            first_name='Upgraded', last_name='User',
        )
        TierOverride.objects.create(
            user=cls.upgraded,
            original_tier=None,
            override_tier=cls.premium,
            expires_at=timezone.now() + datetime.timedelta(days=30),
            granted_by=cls.staff,
            is_active=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_tier_pill_carries_data_attributes_and_label(self):
        response = self.client.get('/studio/users/?q=premium@example.com')
        row_html = _row_html(response.content.decode(), self.premium_named.pk)
        # The pill carries the documented test-id and data-tier value.
        pill_match = re.search(
            r'<span([^>]*data-testid="user-list-tier-pill"[^>]*)>([^<]*)</span>',
            row_html,
        )
        self.assertIsNotNone(pill_match)
        attrs = pill_match.group(1)
        self.assertIn('data-tier="premium"', attrs)
        # Tier-coloured pill text equals the canonical tier display name.
        self.assertEqual(pill_match.group(2).strip(), 'Premium')

    def test_tier_pill_renders_inside_user_cell(self):
        # The User cell is the first <td data-label="User"> in the row;
        # the tier pill must live inside that cell so the operator scans
        # name + tier as a single visual unit.
        response = self.client.get('/studio/users/?q=premium@example.com')
        row_html = _row_html(response.content.decode(), self.premium_named.pk)
        user_cell_match = re.search(
            r'<td[^>]*data-label="User"[^>]*>(.*?)</td>',
            row_html,
            re.DOTALL,
        )
        self.assertIsNotNone(user_cell_match)
        user_cell = user_cell_match.group(1)
        self.assertIn('data-testid="user-list-tier-pill"', user_cell)

    def test_email_as_headline_row_still_carries_tier_pill_inline(self):
        # No name → primary line is email; tier pill still sits inline.
        unnamed_premium = User.objects.create_user(
            email='premium-anon@example.com', password='testpass',
            tier=self.premium,
        )
        response = self.client.get(
            '/studio/users/?q=premium-anon@example.com',
        )
        row_html = _row_html(response.content.decode(), unnamed_premium.pk)
        # The User cell's primary line carries both email and tier pill.
        user_cell_match = re.search(
            r'<td[^>]*data-label="User"[^>]*>(.*?)</td>',
            row_html,
            re.DOTALL,
        )
        self.assertIsNotNone(user_cell_match)
        user_cell = user_cell_match.group(1)
        self.assertIn('data-testid="user-email"', user_cell)
        self.assertIn('data-testid="user-list-tier-pill"', user_cell)

    def test_active_override_renders_icon_only_pill_in_user_cell(self):
        response = self.client.get('/studio/users/?q=upgraded@example.com')
        row_html = _row_html(response.content.decode(), self.upgraded.pk)

        # Override pill has the documented test-id and title tooltip.
        pill_match = re.search(
            r'<span([^>]*data-testid="user-list-tier-override-pill"[^>]*)>(.*?)</span>',
            row_html,
            re.DOTALL,
        )
        self.assertIsNotNone(pill_match)
        attrs = pill_match.group(1)
        self.assertIn('title="Tier override active"', attrs)
        # Icon-only: the pill body is a <i data-lucide="shield-check"> and
        # no human-readable "Override" text leaks into the DOM. The
        # tooltip on the pill itself is what the operator sees on hover.
        pill_body = pill_match.group(2)
        self.assertIn('data-lucide="shield-check"', pill_body)
        # Strip whitespace and the icon tag, then assert the remaining
        # visible text is empty.
        visible_text = re.sub(r'<[^>]+>', '', pill_body).strip()
        self.assertEqual(visible_text, '')

    def test_user_with_no_override_omits_override_pill(self):
        response = self.client.get('/studio/users/?q=premium@example.com')
        row_html = _row_html(response.content.decode(), self.premium_named.pk)
        self.assertNotIn(
            'data-testid="user-list-tier-override-pill"', row_html,
        )


class UserListStatusColumnTest(TestCase):
    """The Status column renders the correct pill for each status value."""

    @classmethod
    def setUpTestData(cls):
        cls.viewer = User.objects.create_user(
            email='viewer@test.com', password='testpass', is_staff=True,
        )
        cls.staff = User.objects.create_user(
            email='staff-user@example.com', password='testpass', is_staff=True,
        )
        cls.active = User.objects.create_user(
            email='active@example.com', password='testpass',
        )
        cls.inactive = User.objects.create_user(
            email='inactive@example.com', password='testpass',
            is_active=False,
        )

    def setUp(self):
        self.client.login(email='viewer@test.com', password='testpass')

    def _status_text(self, html, user_pk):
        row_html = _row_html(html, user_pk)
        # Status pill is a <span data-testid="user-status">...</span>.
        match = re.search(
            r'<span[^>]*data-testid="user-status"[^>]*>([^<]*)</span>',
            row_html,
        )
        if match is None:
            return None
        return match.group(1).strip()

    def test_status_renders_staff_pill_for_staff_user(self):
        response = self.client.get('/studio/users/?q=staff-user@example.com')
        self.assertEqual(
            self._status_text(response.content.decode(), self.staff.pk),
            'Staff',
        )

    def test_status_renders_active_pill_for_non_staff_active_user(self):
        response = self.client.get('/studio/users/?q=active@example.com')
        self.assertEqual(
            self._status_text(response.content.decode(), self.active.pk),
            'Active',
        )

    def test_status_renders_inactive_pill_for_disabled_user(self):
        response = self.client.get('/studio/users/?q=inactive@example.com')
        self.assertEqual(
            self._status_text(response.content.decode(), self.inactive.pk),
            'Inactive',
        )

    def test_status_pill_inside_status_cell(self):
        # The pill must live inside the new <td data-label="Status"> cell;
        # regression guard against accidentally putting it back in the
        # User cell as part of a future refactor.
        response = self.client.get('/studio/users/?q=active@example.com')
        row_html = _row_html(response.content.decode(), self.active.pk)
        status_cell_match = re.search(
            r'<td[^>]*data-label="Status"[^>]*>(.*?)</td>',
            row_html,
            re.DOTALL,
        )
        self.assertIsNotNone(status_cell_match)
        self.assertIn(
            'data-testid="user-status"', status_cell_match.group(1),
        )


class UserListLastLoginColumnTest(TestCase):
    """The Last login cell renders date+time or ``-- never --``."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.recent = User.objects.create_user(
            email='recent@example.com', password='testpass',
        )
        cls.recent.last_login = datetime.datetime(
            2026, 5, 19, 14, 22, 11, tzinfo=datetime.timezone.utc,
        )
        cls.recent.save(update_fields=['last_login'])

        cls.never = User.objects.create_user(
            email='never@example.com', password='testpass',
        )
        # ``create_user`` leaves last_login as None — the explicit assign
        # below documents the contract.
        cls.never.last_login = None
        cls.never.save(update_fields=['last_login'])

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def _last_login_cell(self, html, user_pk):
        row_html = _row_html(html, user_pk)
        match = re.search(
            r'<td[^>]*data-testid="user-last-login"[^>]*>(.*?)</td>',
            row_html,
            re.DOTALL,
        )
        self.assertIsNotNone(match, 'Last login cell missing from row.')
        return match.group(1)

    def test_recent_login_renders_date_on_first_line_and_time_on_second(self):
        response = self.client.get('/studio/users/?q=recent@example.com')
        cell = self._last_login_cell(
            response.content.decode(), self.recent.pk,
        )
        # Both the date and the HH:mm time appear in the cell.
        self.assertIn('2026-05-19', cell)
        # The exact rendered hour depends on TIME_ZONE; the issue spec
        # uses operator timezone. In CI the default settings.TIME_ZONE
        # is UTC so HH:mm is exactly 14:22.
        self.assertIn('14:22', cell)
        # Two visible lines: the date sits inside a div, the time inside
        # a separate div. Two ``<div`` tags is the minimum.
        self.assertGreaterEqual(cell.count('<div'), 2)

    def test_null_last_login_renders_literal_never(self):
        response = self.client.get('/studio/users/?q=never@example.com')
        cell = self._last_login_cell(
            response.content.decode(), self.never.pk,
        )
        self.assertIn('-- never --', cell)
        # And no digits — would imply a stray date/time slipped through.
        text = re.sub(r'<[^>]+>', '', cell)
        self.assertFalse(
            re.search(r'\d', text),
            f'Expected no digits in the Last login cell, got: {text!r}',
        )


class RowTooltipHelperTest(TestCase):
    """``_row_tooltip`` produces the newline-joined hover string."""

    @classmethod
    def setUpTestData(cls):
        cls.full = User.objects.create_user(
            email='full@example.com', password='testpass',
            stripe_customer_id='cus_ABC',
            slack_user_id='U01ABC123',
        )
        cls.full.slack_member = True
        cls.full.slack_checked_at = timezone.now()
        cls.full.save(update_fields=['slack_member', 'slack_checked_at'])

        cls.minimal = User.objects.create_user(
            email='minimal@example.com', password='testpass',
            unsubscribed=True,
        )
        # Never checked Slack workspace.
        cls.minimal.slack_checked_at = None
        cls.minimal.save(update_fields=['slack_checked_at'])

    def test_tooltip_has_all_four_lines_when_slack_and_stripe_set(self):
        tooltip = _row_tooltip(self.full, 'Member')
        self.assertIn('Slack ID: U01ABC123', tooltip)
        self.assertIn('Stripe customer: cus_ABC', tooltip)
        self.assertIn('Newsletter: subscribed', tooltip)
        self.assertIn('Slack workspace: Member', tooltip)

    def test_tooltip_omits_slack_id_line_when_user_has_no_slack_id(self):
        tooltip = _row_tooltip(self.minimal, 'Never checked')
        self.assertNotIn('Slack ID:', tooltip)

    def test_tooltip_omits_stripe_line_when_user_has_no_customer_id(self):
        tooltip = _row_tooltip(self.minimal, 'Never checked')
        self.assertNotIn('Stripe customer:', tooltip)

    def test_tooltip_always_includes_newsletter_and_slack_workspace(self):
        # Even with neither Slack ID nor Stripe ID, hover must not be empty.
        tooltip = _row_tooltip(self.minimal, 'Never checked')
        self.assertIn('Newsletter: unsubscribed', tooltip)
        self.assertIn('Slack workspace: Never checked', tooltip)

    def test_tooltip_lines_are_newline_joined(self):
        # The template renders ``title="{{ row.row_tooltip }}"`` raw, so
        # newline-joining is the right operator-visible separator. Most
        # browsers render \n inside title attributes as a line break.
        tooltip = _row_tooltip(self.full, 'Member')
        # All four parts present and separated by single newlines.
        self.assertEqual(tooltip.count('\n'), 3)


class RowTooltipRenderedOnTrTest(TestCase):
    """Each ``<tr>`` carries the row tooltip as a ``title`` attribute."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.with_ids = User.objects.create_user(
            email='with-ids@example.com', password='testpass',
            stripe_customer_id='cus_ABC',
            slack_user_id='U01ABC123',
        )
        cls.with_ids.slack_member = True
        cls.with_ids.slack_checked_at = timezone.now()
        cls.with_ids.save(update_fields=['slack_member', 'slack_checked_at'])

        cls.minimal = User.objects.create_user(
            email='minimal-row@example.com', password='testpass',
            unsubscribed=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_tr_title_contains_slack_id_when_set(self):
        response = self.client.get('/studio/users/?q=with-ids@example.com')
        attrs = _extract_tr_attrs(
            response.content.decode(), self.with_ids.pk,
        )
        self.assertIn('Slack ID: U01ABC123', attrs)

    def test_tr_title_contains_stripe_customer_when_set(self):
        response = self.client.get('/studio/users/?q=with-ids@example.com')
        attrs = _extract_tr_attrs(
            response.content.decode(), self.with_ids.pk,
        )
        self.assertIn('Stripe customer: cus_ABC', attrs)

    def test_tr_title_contains_newsletter_state(self):
        response = self.client.get('/studio/users/?q=with-ids@example.com')
        attrs = _extract_tr_attrs(
            response.content.decode(), self.with_ids.pk,
        )
        self.assertIn('Newsletter: subscribed', attrs)

    def test_tr_title_contains_slack_workspace_state(self):
        response = self.client.get('/studio/users/?q=with-ids@example.com')
        attrs = _extract_tr_attrs(
            response.content.decode(), self.with_ids.pk,
        )
        self.assertIn('Slack workspace: Member', attrs)

    def test_tr_title_omits_slack_and_stripe_lines_when_unset(self):
        response = self.client.get('/studio/users/?q=minimal-row@example.com')
        attrs = _extract_tr_attrs(
            response.content.decode(), self.minimal.pk,
        )
        # No Slack ID, no Stripe — those substrings must not appear in
        # the title at all.
        self.assertNotIn('Slack ID:', attrs)
        self.assertNotIn('Stripe customer:', attrs)
        # Newsletter + Slack workspace always present.
        self.assertIn('Newsletter: unsubscribed', attrs)
        self.assertIn('Slack workspace: Never checked', attrs)


class UserListingNameSearchRoundTripTest(TestCase):
    """Search by name surfaces the matched row, and the row carries the name."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.avery = User.objects.create_user(
            email='avery.garcia@example.com', password='testpass',
            first_name='Avery', last_name='Garcia',
        )
        cls.bo = User.objects.create_user(
            email='bo.long@example.com', password='testpass',
            first_name='Bo', last_name='Long',
        )
        cls.no_name = User.objects.create_user(
            email='no-name@example.com', password='testpass',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_search_by_first_name_returns_only_matching_named_row(self):
        response = self.client.get('/studio/users/?q=Avery')
        emails = {row['email'] for row in response.context['user_rows']}
        self.assertEqual(emails, {'avery.garcia@example.com'})

        row_html = _row_html(response.content.decode(), self.avery.pk)
        # The matched substring is in the headline cell.
        self.assertIn(
            'Avery',
            _extract_div_text(row_html, 'user-name') or '',
        )

    def test_search_by_first_name_does_not_match_unrelated_users(self):
        # Negative side of the regression guard: bo / no-name do NOT
        # appear in the filtered listing, so their rows can't satisfy a
        # template-level assertion by accident.
        response = self.client.get('/studio/users/?q=Avery')
        emails = {row['email'] for row in response.context['user_rows']}
        self.assertNotIn('bo.long@example.com', emails)
        self.assertNotIn('no-name@example.com', emails)


class UserListingDensityClassesTest(TestCase):
    """The density tightening reaches the rendered HTML."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.user = User.objects.create_user(
            email='dense@example.com', password='testpass',
            first_name='Dense', last_name='Row',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_table_cells_use_tightened_padding(self):
        response = self.client.get('/studio/users/?q=dense')
        row_html = _row_html(response.content.decode(), self.user.pk)
        # Every <td> uses px-3 py-1.5; we don't insist on count, only
        # that the new combo appears and the old combo doesn't.
        self.assertIn('px-3 py-1.5', row_html)
        self.assertNotIn('px-4 py-2.5', row_html)

    def test_tier_pill_uses_tightened_padding(self):
        response = self.client.get('/studio/users/?q=dense')
        row_html = _row_html(response.content.decode(), self.user.pk)
        # Badges drop from px-2 to px-1.5 and leading-5 to leading-4.
        self.assertIn('px-1.5 py-0.5', row_html)
        self.assertIn('leading-4', row_html)
        # Locking out the previous combo prevents partial drift.
        self.assertNotIn('leading-5', row_html)

    def test_action_button_padding_is_tightened(self):
        # ACTION_BASE_CLASS in studio_filters.py drives every Studio list
        # action; the contract is a single px-2.5 py-1 string in the
        # rendered class attribute.
        response = self.client.get('/studio/users/?q=dense')
        self.assertContains(response, 'px-2.5 py-1')


class UserListFilteredCountMatchesRowCountTest(TestCase):
    """The chip stat count equals the rendered tbody row count."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.main = Tier.objects.get(slug='main')
        # 4 paid users (active Stripe subscription), 3 free.
        for idx in range(4):
            User.objects.create_user(
                email=f'paid-{idx}@example.com', password='testpass',
                tier=cls.main, subscription_id=f'sub_{idx}',
            )
        for idx in range(3):
            User.objects.create_user(
                email=f'free-{idx}@example.com', password='testpass',
            )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_paid_filter_row_count_equals_paid_chip_stat(self):
        response = self.client.get('/studio/users/?filter=paid')
        # Paid stat in the context is 4.
        self.assertEqual(response.context['paid_count'], 4)
        # Rendered user rows are 4. ``user-row-N`` is unique to the user
        # table, so count it across the whole page (the issue #923
        # membership-breakdown table has no such rows).
        html = response.content.decode()
        row_count = len(re.findall(
            r'<tr[^>]*data-testid="user-row-\d+"', html,
        ))
        self.assertEqual(row_count, 4)
