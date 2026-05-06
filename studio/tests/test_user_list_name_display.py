"""Tests for the User cell name/email layout on /studio/users/ (issue #451).

Issue #451 surfaces ``first_name`` / ``last_name`` on every row dict and
makes the User cell show ``full_name`` (when set) as the headline above
the email. When neither name is set, email stays as the headline and the
``user-name`` test-id is not rendered at all. The Joined date stays
underneath both lines as the tertiary line.

The cell-padding / badge-padding / button-padding density tightening
shipped alongside this change is only asserted indirectly: the structural
markers (``data-testid``, ``aria-label``, ``truncate``, ``title``) are
the contract the Playwright scanability suite relies on, and those still
have to round-trip through this template.
"""

import re

from django.contrib.auth import get_user_model
from django.test import TestCase

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


class UserListingFullNameRowDictTest(TestCase):
    """``_build_user_listing`` must surface ``first_name``/``last_name``/``full_name``.

    The view-layer change is the source of truth for all four name
    combinations; the template branches on ``row.full_name`` truthiness,
    and the search-by-name regression already lives in
    ``test_user_list_search.py``.
    """

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
    """The User cell renders four distinct shapes for the four name combos."""

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

        # No ``user-name`` div at all — the User cell is just email + Joined.
        self.assertNotIn('data-testid="user-name"', row_html)
        # Email is still rendered with the same data-testid so the
        # Stripe/Slack/scanability tests keep working.
        self.assertEqual(
            _extract_div_text(row_html, 'user-email'),
            'no-name@example.com',
        )

    def test_no_name_row_still_shows_joined_date(self):
        response = self.client.get('/studio/users/?q=no-name')
        row_html = _row_html(response.content.decode(), self.neither.pk)
        self.assertIn('Joined ', row_html)

    def test_named_row_still_shows_joined_date(self):
        # The Joined line is the tertiary line under both name + email
        # for any row that has a name.
        response = self.client.get('/studio/users/?q=avery.garcia')
        row_html = _row_html(response.content.decode(), self.both.pk)
        self.assertIn('Joined ', row_html)


class UserListingNameSearchRoundTripTest(TestCase):
    """Search by name surfaces the matched row, and the row carries the name.

    The search-engine OR-match against ``first_name`` / ``last_name``
    already has dedicated coverage in ``test_user_list_search.py``; this
    test guards the additional contract introduced by issue #451 — when
    you search by name, the matched row's ``data-testid="user-name"``
    cell contains the matched substring.
    """

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


class UserListingPreservesPriorFeaturesTest(TestCase):
    """Prior issues' features must survive the density tightening.

    Density-only changes can silently drop test-ids or attributes when
    refactoring the markup. Lock the contract so the next density tweak
    fails fast instead of breaking the Playwright suite.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        # Premium user with: name set, slack member, stripe ID, several tags.
        cls.user = User.objects.create_user(
            email='avery.garcia@example.com', password='testpass',
            first_name='Avery', last_name='Garcia',
            stripe_customer_id='cus_PREMIUM',
        )
        cls.user.tags = ['early-adopter', 'beta', 'paid-2026', 'vip', 'cohort-a']
        cls.user.slack_member = True
        cls.user.slack_checked_at = cls.user.date_joined
        cls.user.save(update_fields=['tags', 'slack_member', 'slack_checked_at'])

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_slack_badge_still_renders_for_member(self):
        response = self.client.get('/studio/users/?q=avery.garcia')
        row_html = _row_html(response.content.decode(), self.user.pk)
        # Slack is a tri-state badge from issue #358; the testid + label
        # are the contract.
        self.assertIn('data-testid="slack-status"', row_html)
        self.assertIn('>Slack<', row_html)

    def test_stripe_indicator_renders_for_user_with_customer_id(self):
        # Stripe glyph from issue #441; visibility is keyed off
        # ``row.stripe_customer_id`` and unchanged by the density tweak.
        response = self.client.get('/studio/users/?q=cus_PREMIUM')
        row_html = _row_html(response.content.decode(), self.user.pk)
        self.assertIn('data-testid="stripe-indicator"', row_html)

    def test_tags_overflow_chip_caps_at_three_visible_plus_count(self):
        # Tag overflow from issue #410: 5 tags -> 3 visible + +2.
        response = self.client.get('/studio/users/?q=avery.garcia')
        row_html = _row_html(response.content.decode(), self.user.pk)
        self.assertIn('data-testid="user-tags-overflow">+2<', row_html)
        # The hidden-tag tooltip on the overflow chip stays as-is.
        self.assertIn('aria-label="2 more tags: vip, cohort-a"', row_html)

    def test_action_buttons_still_present_at_view_and_login_as(self):
        # Action button padding tightened, but both buttons stay visible
        # with text labels (no icon-only switch).
        response = self.client.get('/studio/users/?q=avery.garcia')
        row_html = _row_html(response.content.decode(), self.user.pk)
        self.assertIn('data-testid="user-view-link"', row_html)
        self.assertIn('Login as', row_html)

    def test_data_label_attributes_preserve_mobile_stacked_layout(self):
        # Mobile fallback CSS keys off data-label — the four cells' labels
        # must stay exactly User / Membership / Tags / Actions so the
        # cards keep their headings.
        response = self.client.get('/studio/users/?q=avery.garcia')
        row_html = _row_html(response.content.decode(), self.user.pk)
        self.assertIn('data-label="User"', row_html)
        self.assertIn('data-label="Membership"', row_html)
        self.assertIn('data-label="Tags"', row_html)
        self.assertIn('data-label="Actions"', row_html)


class UserListingDensityClassesTest(TestCase):
    """The density tightening reaches the rendered HTML.

    These assertions are the cheapest way to guard the new spacing
    contract from a future refactor that drifts the cell padding back to
    ``px-4 py-2.5``. The Playwright scanability test enforces the
    measured row-count target; this test enforces the class-name
    contract.
    """

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

    def test_membership_badges_use_tightened_padding(self):
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
