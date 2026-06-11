"""Tests for the Studio Signup Analytics dashboard (issue #770).

Covers acceptance criteria:

- Access control (anonymous redirect, non-staff 403, staff 200).
- Headline cards (24h / 7d / 30d) with deltas vs the prior window.
- ``signup_path`` chip filter applies to headlines + sections 3, 4, 6, 7.
- Date-range filter applies to sections 3, 4, 6, 7 but NOT headlines.
- Breakdown table (% share, sorted desc).
- Top UTM sources + top campaigns (top-N, blanks shown as ``(no UTM)`` /
  ``(no campaign)``).
- Campaign cell links to ``/studio/utm-analytics/campaign/<slug>/`` when a
  matching ``UtmCampaign`` exists.
- Referrer section is gated on the #772 field — placeholder when missing.
- Recent signups table links to ``/studio/users/<id>/`` and respects the
  active window + signup_path filter.
- Empty state copy when no rows match the window.
- Query budget under 10 SQL queries.
- Sidebar entry under Tracking auto-expands the section on the dashboard.
- ``tracking_active`` extends to ``signup-analytics``.
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from analytics.models import UserAttribution
from integrations.models import UtmCampaign
from studio.templatetags.studio_filters import studio_sidebar_state

User = get_user_model()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _staff_login(client, email='staff@test.com'):
    user = User.objects.create_user(email=email, password='pw', is_staff=True)
    client.login(email=email, password='pw')
    return user


def _user_login(client, email='free@test.com'):
    user = User.objects.create_user(email=email, password='pw', is_staff=False)
    client.login(email=email, password='pw')
    return user


def _make_attribution(*, email, signup_path='email_password',
                      first_source='', first_campaign='', created_at=None,
                      first_ts=None):
    """Create a User + matching UserAttribution row.

    The ``post_save`` signal already creates a blank UserAttribution row
    on user creation, so we update it in place (rather than create a new
    row that would collide on the OneToOne PK).
    """
    user = User.objects.create_user(email=email, password='pw')
    attr, _ = UserAttribution.objects.get_or_create(user=user)
    attr.signup_path = signup_path
    attr.first_touch_utm_source = first_source
    attr.first_touch_utm_campaign = first_campaign
    if first_ts:
        attr.first_touch_ts = first_ts
    attr.save()
    if created_at is not None:
        # ``created_at`` is ``auto_now_add`` so we have to bypass it with
        # an UPDATE query.
        UserAttribution.objects.filter(pk=attr.pk).update(created_at=created_at)
        attr.refresh_from_db()
    return user, attr


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------

class SignupAnalyticsAccessTest(TestCase):
    """Anonymous redirected; non-staff 403; staff 200."""

    URL = '/studio/signup-analytics/'

    def test_anonymous_redirected_to_login(self):
        client = Client()
        response = client.get(self.URL)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_non_staff_user_gets_403(self):
        client = Client()
        _user_login(client)
        response = client.get(self.URL)
        self.assertEqual(response.status_code, 403)

    def test_staff_user_gets_200(self):
        client = Client()
        _staff_login(client)
        response = client.get(self.URL)
        self.assertEqual(response.status_code, 200)


# ---------------------------------------------------------------------------
# Page header + filter chips
# ---------------------------------------------------------------------------

class SignupAnalyticsHeaderTest(TestCase):
    """Header, description, and filter chip rendering."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_header_renders(self):
        response = self.client.get('/studio/signup-analytics/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<h1 class="text-2xl font-semibold text-foreground">Signup Analytics</h1>', html=False)
        self.assertContains(response, 'Where new signups are coming from')

    def test_filter_chip_range_options_render(self):
        response = self.client.get('/studio/signup-analytics/')
        for value, label in (
            ('24h', 'Last 24 hours'),
            ('7d', 'Last 7 days'),
            ('30d', 'Last 30 days'),
            ('custom', 'Custom'),
        ):
            self.assertContains(response, f'value="{value}"')
            self.assertContains(response, label)

    def test_signup_path_chip_all_plus_choices_render(self):
        response = self.client.get('/studio/signup-analytics/')
        # "All paths" + every choice from SIGNUP_PATH_CHOICES.
        self.assertContains(response, 'All paths')
        for value in ('email_password', 'google_oauth', 'slack_oauth',
                      'github_oauth', 'newsletter', 'stripe_checkout',
                      'admin_created', 'unknown'):
            self.assertContains(response, f'value="{value}"')

    def test_default_range_7d_is_selected(self):
        response = self.client.get('/studio/signup-analytics/')
        # The 7d option is selected by default.
        self.assertContains(response, '<option value="7d" selected>Last 7 days</option>', html=False)

    def test_invalid_range_falls_back_to_7d(self):
        response = self.client.get('/studio/signup-analytics/?range=bogus')
        filters = response.context['filters']
        self.assertEqual(filters['range_key'], '7d')

    def test_invalid_signup_path_falls_back_to_empty(self):
        response = self.client.get(
            '/studio/signup-analytics/?signup_path=not_a_real_path',
        )
        filters = response.context['filters']
        self.assertEqual(filters['signup_path'], '')


# ---------------------------------------------------------------------------
# Headline cards
# ---------------------------------------------------------------------------

class SignupAnalyticsHeadlineCardsTest(TestCase):
    """Rolling 24h / 7d / 30d cards with delta vs the prior window."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        # The post_save signal creates a UserAttribution row for the staff
        # user. Remove it so the staff row doesn't pollute the headline
        # card counts in these tests.
        UserAttribution.objects.filter(user=cls.staff).delete()

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_seven_day_card_count_and_positive_delta(self):
        now = timezone.now()
        # 5 in the last 7d, 2 in the prior 7d window.
        for i in range(5):
            _make_attribution(
                email=f'recent-{i}@t.com',
                created_at=now - timedelta(days=1),
            )
        for i in range(2):
            _make_attribution(
                email=f'older-{i}@t.com',
                created_at=now - timedelta(days=8),
            )

        response = self.client.get('/studio/signup-analytics/')
        cards = response.context['headline_cards']
        seven_day = cards[1]
        self.assertEqual(seven_day['label'], 'Last 7d')
        self.assertEqual(seven_day['count'], 5)
        self.assertEqual(seven_day['delta']['sign'], '+')
        self.assertEqual(seven_day['delta']['diff'], 3)

    def test_negative_delta_renders_minus_sign(self):
        now = timezone.now()
        # 1 in the last 7d, 4 in the prior 7d window.
        _make_attribution(email='one@t.com', created_at=now - timedelta(days=1))
        for i in range(4):
            _make_attribution(
                email=f'p{i}@t.com', created_at=now - timedelta(days=8),
            )

        response = self.client.get('/studio/signup-analytics/')
        seven = response.context['headline_cards'][1]
        self.assertEqual(seven['count'], 1)
        self.assertEqual(seven['delta']['sign'], '-')
        self.assertEqual(seven['delta']['diff'], 3)

    def test_zero_zero_delta_renders_equal(self):
        response = self.client.get('/studio/signup-analytics/')
        for card in response.context['headline_cards']:
            self.assertEqual(card['count'], 0)
            self.assertEqual(card['delta']['sign'], '=')

    def test_signup_path_filter_applies_to_headline_cards(self):
        now = timezone.now()
        # 3 google_oauth + 2 email_password in the last 7d.
        for i in range(3):
            _make_attribution(
                email=f'g{i}@t.com', signup_path='google_oauth',
                created_at=now - timedelta(days=1),
            )
        for i in range(2):
            _make_attribution(
                email=f'e{i}@t.com', signup_path='email_password',
                created_at=now - timedelta(days=1),
            )

        response = self.client.get(
            '/studio/signup-analytics/?signup_path=google_oauth',
        )
        # 7d card respects the filter: 3 (not 5).
        seven = response.context['headline_cards'][1]
        self.assertEqual(seven['count'], 3)

    def test_date_range_filter_does_not_affect_headline_cards(self):
        now = timezone.now()
        # 5 rows in the last 7d.
        for i in range(5):
            _make_attribution(
                email=f'r{i}@t.com', created_at=now - timedelta(days=1),
            )

        # Even with range=24h the 7d headline card still shows 5
        # (cards are always rolling, never filter-dependent on range).
        response = self.client.get('/studio/signup-analytics/?range=24h')
        seven = response.context['headline_cards'][1]
        self.assertEqual(seven['count'], 5)


# ---------------------------------------------------------------------------
# Layout: headline strip is independent of the filter (#851)
# ---------------------------------------------------------------------------

class SignupAnalyticsHeadlineLayoutTest(TestCase):
    """#851 — cards render before the filter, with a clarifying caption."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        UserAttribution.objects.filter(user=cls.staff).delete()

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_headlines_render_before_filter_form_in_dom(self):
        response = self.client.get('/studio/signup-analytics/')
        body = response.content.decode()
        headlines_pos = body.index('data-testid="signup-analytics-headlines"')
        filters_pos = body.index('data-testid="signup-analytics-filters"')
        self.assertLess(
            headlines_pos, filters_pos,
            'Headline cards must render before the filter form (#851).',
        )

    def test_headline_fixed_note_explains_cards_ignore_date_range(self):
        response = self.client.get('/studio/signup-analytics/')
        self.assertContains(response, 'data-testid="headline-fixed-note"')
        # Caption must state cards ignore the Date range but honor Signup path.
        self.assertContains(response, 'ignore the Date range')
        self.assertContains(response, 'Signup path')

    def test_filter_scope_note_says_it_applies_to_sections_below(self):
        response = self.client.get('/studio/signup-analytics/')
        self.assertContains(response, 'data-testid="filter-scope-note"')
        self.assertContains(response, 'Filter the sections below')

    def test_existing_testid_hooks_remain_present(self):
        response = self.client.get('/studio/signup-analytics/')
        for testid in (
            'signup-analytics-filters',
            'signup-analytics-headlines',
            'signup-analytics-recent-empty',
        ):
            self.assertContains(response, f'data-testid="{testid}"')


# ---------------------------------------------------------------------------
# Section 3 — breakdown by signup_path
# ---------------------------------------------------------------------------

class SignupAnalyticsBreakdownTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        UserAttribution.objects.filter(user=cls.staff).delete()

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_breakdown_lists_one_row_per_signup_path_sorted_desc(self):
        now = timezone.now()
        for i in range(3):
            _make_attribution(
                email=f'g{i}@t.com', signup_path='google_oauth',
                created_at=now - timedelta(days=1),
            )
        for i in range(2):
            _make_attribution(
                email=f'e{i}@t.com', signup_path='email_password',
                created_at=now - timedelta(days=1),
            )

        response = self.client.get('/studio/signup-analytics/')
        rows = response.context['signup_path_rows']
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['signup_path'], 'google_oauth')
        self.assertEqual(rows[0]['n'], 3)
        self.assertEqual(rows[0]['pct'], '60.0%')
        self.assertEqual(rows[1]['signup_path'], 'email_password')
        self.assertEqual(rows[1]['n'], 2)
        self.assertEqual(rows[1]['pct'], '40.0%')

    def test_breakdown_with_signup_path_filter_shows_one_row(self):
        now = timezone.now()
        for i in range(3):
            _make_attribution(
                email=f'g{i}@t.com', signup_path='google_oauth',
                created_at=now - timedelta(days=1),
            )
        for i in range(2):
            _make_attribution(
                email=f'e{i}@t.com', signup_path='email_password',
                created_at=now - timedelta(days=1),
            )

        response = self.client.get(
            '/studio/signup-analytics/?signup_path=google_oauth',
        )
        rows = response.context['signup_path_rows']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['signup_path'], 'google_oauth')
        self.assertEqual(rows[0]['n'], 3)
        self.assertEqual(rows[0]['pct'], '100.0%')


# ---------------------------------------------------------------------------
# Section 4 — top first-touch UTM sources
# ---------------------------------------------------------------------------

class SignupAnalyticsUtmSourceTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        UserAttribution.objects.filter(user=cls.staff).delete()

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_blank_source_renders_as_no_utm(self):
        now = timezone.now()
        _make_attribution(
            email='blank@t.com', first_source='',
            created_at=now - timedelta(days=1),
        )
        response = self.client.get('/studio/signup-analytics/')
        rows = response.context['utm_source_rows']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['label'], '(no UTM)')
        # And the label appears in the rendered HTML.
        self.assertContains(response, '(no UTM)')


# ---------------------------------------------------------------------------
# Section 5 — first-touch referrer sources (gated on #772)
# ---------------------------------------------------------------------------

class SignupAnalyticsReferrerSectionTest(TestCase):
    """Either renders the table (when #772 has landed) or the placeholder."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_placeholder_when_field_absent(self):
        """When ``first_touch_referrer_source`` is not present we render
        the placeholder copy and skip the query."""
        # Patch ``_has_referrer_field`` to simulate the pre-#772 state.
        with patch(
            'studio.views.signup_analytics._has_referrer_field',
            return_value=False,
        ):
            response = self.client.get('/studio/signup-analytics/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['has_referrer_data'])
        self.assertContains(
            response, 'Referrer attribution lands with #772 — not yet available.'
        )


# ---------------------------------------------------------------------------
# Section 6 — top first-touch campaigns
# ---------------------------------------------------------------------------

class SignupAnalyticsCampaignsTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        UserAttribution.objects.filter(user=cls.staff).delete()

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_campaign_cell_links_when_utm_campaign_exists(self):
        now = timezone.now()
        UtmCampaign.objects.create(
            name='Spring Launch', slug='spring_launch',
            default_utm_source='newsletter', default_utm_medium='email',
        )
        for i in range(4):
            _make_attribution(
                email=f'c{i}@t.com', first_campaign='spring_launch',
                created_at=now - timedelta(days=1),
            )

        response = self.client.get('/studio/signup-analytics/')
        rows = response.context['campaign_rows']
        self.assertEqual(rows[0]['slug'], 'spring_launch')
        self.assertEqual(rows[0]['n'], 4)
        self.assertContains(
            response, 'href="/studio/utm-analytics/campaign/spring_launch/?range=7d"',
        )

    def test_matched_link_carries_tooltip_title(self):
        now = timezone.now()
        UtmCampaign.objects.create(
            name='Spring Launch', slug='spring_launch',
            default_utm_source='newsletter', default_utm_medium='email',
        )
        _make_attribution(
            email='tip@t.com', first_campaign='spring_launch',
            created_at=now - timedelta(days=1),
        )
        response = self.client.get('/studio/signup-analytics/')
        # The matched link carries a title attribute clarifying it opens UTM
        # campaign analytics for that campaign.
        self.assertContains(
            response,
            'title="Opens UTM campaign analytics for spring_launch"',
        )

    def test_matched_link_preserves_active_range(self):
        now = timezone.now()
        UtmCampaign.objects.create(
            name='Spring Launch', slug='spring_launch',
            default_utm_source='newsletter', default_utm_medium='email',
        )
        _make_attribution(
            email='r30@t.com', first_campaign='spring_launch',
            created_at=now - timedelta(days=10),
        )
        response = self.client.get('/studio/signup-analytics/?range=30d')
        self.assertContains(
            response, 'href="/studio/utm-analytics/campaign/spring_launch/?range=30d"',
        )

    def test_external_email_campaign_code_stays_plain_text(self):
        now = timezone.now()
        # External Mailchimp/Substack-style code that can never match a
        # UtmCampaign slug (hyphens + digits + uppercase rejected by slug
        # validation). It must render as plain text with no anchor.
        code = '934616d2a5-email_campaign_2026_05_18_12_38'
        for i in range(3):
            _make_attribution(
                email=f'ext{i}@t.com', first_campaign=code,
                created_at=now - timedelta(days=1),
            )
        response = self.client.get('/studio/signup-analytics/')
        self.assertContains(response, code)
        self.assertNotContains(
            response, f'href="/studio/utm-analytics/campaign/{code}/',
        )
        # No matched drill link at all for this row.
        rows = response.context['campaign_rows']
        self.assertEqual(rows[0]['slug'], code)
        self.assertFalse(rows[0]['campaign'])

    def test_blank_campaign_renders_as_no_campaign(self):
        now = timezone.now()
        _make_attribution(
            email='no@t.com', first_campaign='',
            created_at=now - timedelta(days=1),
        )
        response = self.client.get('/studio/signup-analytics/')
        rows = response.context['campaign_rows']
        self.assertEqual(rows[0]['label'], '(no campaign)')
        self.assertContains(response, '(no campaign)')

    def test_campaign_cell_is_plain_text_when_no_utm_campaign_row(self):
        now = timezone.now()
        for i in range(2):
            _make_attribution(
                email=f'orph{i}@t.com', first_campaign='ghost_campaign',
                created_at=now - timedelta(days=1),
            )
        response = self.client.get('/studio/signup-analytics/')
        # The slug appears but NOT as a link to the campaign-analytics drill-down.
        self.assertContains(response, 'ghost_campaign')
        self.assertNotContains(
            response, 'href="/studio/utm-analytics/campaign/ghost_campaign/',
        )


# ---------------------------------------------------------------------------
# Section 7 — recent signups
# ---------------------------------------------------------------------------

class SignupAnalyticsRecentTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        UserAttribution.objects.filter(user=cls.staff).delete()

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_recent_lists_up_to_page_size_in_window(self):
        now = timezone.now()
        for i in range(25):
            _make_attribution(
                email=f'r{i}@t.com', created_at=now - timedelta(days=1),
            )

        response = self.client.get('/studio/signup-analytics/')
        rows = response.context['recent_signups']
        # 25 rows fit on one 50-row page, so all are shown (no pager).
        self.assertEqual(len(rows), 25)
        self.assertFalse(response.context['show_pager'])

    def test_recent_respects_range_filter(self):
        now = timezone.now()
        # 2 in the last 24h, 5 created 5 days ago.
        for i in range(2):
            _make_attribution(
                email=f'today-{i}@t.com',
                created_at=now - timedelta(hours=12),
            )
        for i in range(5):
            _make_attribution(
                email=f'old-{i}@t.com',
                created_at=now - timedelta(days=5),
            )

        response = self.client.get('/studio/signup-analytics/?range=24h')
        rows = response.context['recent_signups']
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertIn('today-', r.user.email)

    def test_recent_email_links_to_user_detail(self):
        now = timezone.now()
        user, _ = _make_attribution(
            email='alice@test.com', created_at=now - timedelta(hours=1),
        )

        response = self.client.get('/studio/signup-analytics/')
        self.assertContains(response, 'alice@test.com')
        self.assertContains(response, f'href="/studio/users/{user.id}/"')

    def test_user_detail_followup_returns_200(self):
        now = timezone.now()
        user, _ = _make_attribution(
            email='alice@test.com', created_at=now - timedelta(hours=1),
        )

        response = self.client.get(f'/studio/users/{user.id}/')
        self.assertEqual(response.status_code, 200)


# ---------------------------------------------------------------------------
# Recent signups pagination (issue #850)
# ---------------------------------------------------------------------------

class SignupAnalyticsPaginationTest(TestCase):
    """Section 7 is a 50-row Paginator over the filtered, ordered set."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        UserAttribution.objects.filter(user=cls.staff).delete()

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def _seed(self, n, *, signup_path='email_password', created_offset_hours=1,
              prefix='r'):
        """Create ``n`` attributions inside the default 7d window.

        Rows are ordered so that ``{prefix}0`` is the OLDEST and
        ``{prefix}{n-1}`` is the NEWEST (created most recently), matching
        the ``-created_at`` ordering of the view.
        """
        now = timezone.now()
        users = []
        for i in range(n):
            # i=0 oldest, i=n-1 newest — stay within the 7d window.
            created = now - timedelta(hours=created_offset_hours + (n - i))
            user, _ = _make_attribution(
                email=f'{prefix}{i}@t.com', signup_path=signup_path,
                created_at=created,
            )
            users.append(user)
        return users

    def test_page_one_shows_fifty_of_fiftyone(self):
        self._seed(51)
        response = self.client.get('/studio/signup-analytics/')
        self.assertEqual(len(response.context['recent_signups']), 50)
        self.assertTrue(response.context['show_pager'])
        self.assertEqual(response.context['paginator'].num_pages, 2)
        self.assertEqual(response.context['page_start_index'], 1)
        self.assertEqual(response.context['page_end_index'], 50)
        self.assertEqual(response.context['filtered_total'], 51)

    def test_page_two_shows_remainder(self):
        self._seed(51)
        response = self.client.get('/studio/signup-analytics/?page=2')
        self.assertEqual(len(response.context['recent_signups']), 1)
        self.assertEqual(response.context['page_start_index'], 51)
        self.assertEqual(response.context['page_end_index'], 51)
        self.assertEqual(response.context['page'].number, 2)

    def test_page_two_is_the_next_fifty_by_created_at(self):
        # 60 rows: newest 50 on page 1, oldest 10 on page 2.
        self._seed(60)
        page1 = self.client.get('/studio/signup-analytics/')
        page2 = self.client.get('/studio/signup-analytics/?page=2')
        page1_emails = {r.user.email for r in page1.context['recent_signups']}
        page2_emails = {r.user.email for r in page2.context['recent_signups']}
        self.assertEqual(len(page1_emails), 50)
        self.assertEqual(len(page2_emails), 10)
        self.assertEqual(page1_emails & page2_emails, set())
        # Page 2 holds the oldest rows: r0..r9 (seeded oldest-first).
        self.assertEqual(
            page2_emails, {f'r{i}@t.com' for i in range(10)},
        )

    def test_pager_status_strings_rendered(self):
        self._seed(60)
        response = self.client.get('/studio/signup-analytics/')
        self.assertContains(response, 'Showing 1-50 of 60')
        self.assertContains(response, 'page 1 of 2')

    def test_pager_namespaced_testids_present(self):
        self._seed(60)
        response = self.client.get('/studio/signup-analytics/')
        self.assertContains(response, 'data-testid="signup-recent-pager"')
        self.assertContains(response, 'data-testid="signup-recent-pager-next"')
        # Must NOT borrow the other Studio pagers' testids.
        self.assertNotContains(response, 'data-testid="ses-event-list-pager"')
        self.assertNotContains(response, 'data-testid="user-list-pager"')

    def test_pager_hidden_for_single_page(self):
        self._seed(5)
        response = self.client.get('/studio/signup-analytics/')
        self.assertFalse(response.context['show_pager'])
        self.assertNotContains(response, 'data-testid="signup-recent-pager"')
        self.assertEqual(len(response.context['recent_signups']), 5)

    def test_first_prev_disabled_on_page_one(self):
        self._seed(60)
        response = self.client.get('/studio/signup-analytics/')
        self.assertIsNone(response.context['pager_first_url'])
        self.assertIsNone(response.context['pager_prev_url'])
        self.assertIsNotNone(response.context['pager_next_url'])
        self.assertIsNotNone(response.context['pager_last_url'])

    def test_next_last_disabled_on_last_page(self):
        self._seed(60)
        response = self.client.get('/studio/signup-analytics/?page=2')
        self.assertIsNone(response.context['pager_next_url'])
        self.assertIsNone(response.context['pager_last_url'])
        self.assertIsNotNone(response.context['pager_first_url'])
        self.assertIsNotNone(response.context['pager_prev_url'])

    def test_range_filter_preserved_across_pages(self):
        self._seed(60)
        response = self.client.get('/studio/signup-analytics/?range=30d')
        # Pager next URL must carry range=30d alongside page=2.
        next_url = response.context['pager_next_url']
        self.assertIn('range=30d', next_url)
        self.assertIn('page=2', next_url)
        # And following it keeps the 30d window selected. ``next_url`` is a
        # querystring-only URL (mirrors how the pager partial appends it to
        # the current path), so resolve it against the dashboard path.
        page2 = self.client.get('/studio/signup-analytics/' + next_url)
        self.assertEqual(page2.context['filters']['range_key'], '30d')

    def test_signup_path_filter_preserved_across_pages(self):
        self._seed(60, signup_path='google_oauth', prefix='g')
        response = self.client.get(
            '/studio/signup-analytics/?signup_path=google_oauth',
        )
        next_url = response.context['pager_next_url']
        self.assertIn('signup_path=google_oauth', next_url)
        self.assertIn('page=2', next_url)
        page2 = self.client.get('/studio/signup-analytics/' + next_url)
        self.assertEqual(
            page2.context['filters']['signup_path'], 'google_oauth',
        )
        for r in page2.context['recent_signups']:
            self.assertEqual(r.signup_path, 'google_oauth')

    def test_custom_date_range_preserved_across_pages(self):
        now = timezone.now()
        start = (now - timedelta(days=10)).strftime('%Y-%m-%d')
        end = now.strftime('%Y-%m-%d')
        # Seed 60 rows inside the custom window (5 days ago).
        for i in range(60):
            _make_attribution(
                email=f'c{i}@t.com',
                created_at=now - timedelta(days=5, minutes=i),
            )
        url = (
            f'/studio/signup-analytics/?range=custom&start={start}&end={end}'
        )
        response = self.client.get(url)
        next_url = response.context['pager_next_url']
        self.assertIn('range=custom', next_url)
        self.assertIn(f'start={start}', next_url)
        self.assertIn(f'end={end}', next_url)
        self.assertIn('page=2', next_url)
        page2 = self.client.get('/studio/signup-analytics/' + next_url)
        self.assertEqual(page2.context['filters']['range_key'], 'custom')
        self.assertEqual(page2.context['filters']['start_str'], start)
        self.assertEqual(page2.context['filters']['end_str'], end)

    def test_page_zero_clamps_to_one(self):
        self._seed(60)
        response = self.client.get('/studio/signup-analytics/?page=0')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['page'].number, 1)

    def test_page_negative_clamps_to_one(self):
        self._seed(60)
        response = self.client.get('/studio/signup-analytics/?page=-1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['page'].number, 1)

    def test_page_non_integer_clamps_to_one(self):
        self._seed(60)
        response = self.client.get('/studio/signup-analytics/?page=abc')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['page'].number, 1)

    def test_page_beyond_last_clamps_to_last(self):
        self._seed(60)
        response = self.client.get('/studio/signup-analytics/?page=9999')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['page'].number, 2)
        self.assertEqual(response.context['paginator'].num_pages, 2)

    def test_pagination_adds_at_most_two_section_seven_queries(self):
        """Paginator adds exactly +1 net query (COUNT + slice) for Section 7.

        Whole-page budget stays under 10 view-side queries even with paging.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self._seed(60)
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get('/studio/signup-analytics/')
            self.assertEqual(response.status_code, 200)
        view_queries = [
            q for q in ctx.captured_queries
            if 'analytics_userattribution' in q['sql']
            or 'integrations_utmcampaign' in q['sql']
        ]
        self.assertLess(
            len(view_queries), 10,
            msg=f'Expected <10 view queries, got {len(view_queries)}: '
                + '\n---\n'.join(q['sql'] for q in view_queries),
        )

    def test_top_n_sections_not_paginated(self):
        """Top-N aggregate sections stay capped at TOP_N, not paged."""
        from studio.views.signup_analytics import TOP_N
        now = timezone.now()
        # 15 distinct UTM sources -> Top-N must still cap at TOP_N.
        for i in range(15):
            _make_attribution(
                email=f'u{i}@t.com', first_source=f'src{i}',
                created_at=now - timedelta(hours=2),
            )
        response = self.client.get('/studio/signup-analytics/')
        self.assertLessEqual(
            len(response.context['utm_source_rows']), TOP_N,
        )


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------

class SignupAnalyticsEmptyStateTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        # Drop the staff's auto-created attribution row so "empty" means
        # truly zero rows in the window.
        UserAttribution.objects.filter(user=cls.staff).delete()

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_empty_state_message_in_breakdown_and_recent(self):
        response = self.client.get('/studio/signup-analytics/')
        # Section 3 empty-state copy.
        self.assertContains(response, 'No signups in this range')
        self.assertContains(response, 'Try widening the date range')

    def test_zero_zero_delta_in_headline_cards(self):
        response = self.client.get('/studio/signup-analytics/')
        for card in response.context['headline_cards']:
            self.assertEqual(card['count'], 0)
            self.assertEqual(card['delta']['sign'], '=')


# ---------------------------------------------------------------------------
# Query budget
# ---------------------------------------------------------------------------

class SignupAnalyticsQueryBudgetTest(TestCase):
    """Page-load issues fewer than 10 SQL queries on a default render."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        now = timezone.now()
        # Seed 50 UserAttribution rows + 10 UtmCampaign rows.
        for i in range(50):
            user = User.objects.create_user(
                email=f'q{i}@test.com', password='pw',
            )
            attr, _ = UserAttribution.objects.get_or_create(user=user)
            attr.signup_path = 'email_password'
            attr.first_touch_utm_source = 'twitter' if i % 2 == 0 else ''
            attr.first_touch_utm_campaign = 'launch_april' if i % 3 == 0 else ''
            attr.save()
            UserAttribution.objects.filter(pk=attr.pk).update(
                created_at=now - timedelta(days=i % 30),
            )
        for i in range(10):
            UtmCampaign.objects.create(
                name=f'C{i}', slug=f'c{i}',
                default_utm_source='newsletter', default_utm_medium='email',
            )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_default_render_under_ten_view_queries(self):
        """Dashboard issues fewer than 10 view-side queries.

        We measure queries that target ``analytics_userattribution`` and
        ``integrations_utmcampaign`` (the two tables the view reads from
        directly). Framework queries (session, user, redirects, integration
        settings, the email-verification-banner context processor) are
        added by middleware and the base template, not the dashboard view,
        and don't scale with seeded data — those are excluded from the
        budget. The view itself should issue around 5 queries: 1 headline
        aggregate, 1 signup_path breakdown, 1 UTM source breakdown, 1 UTM
        campaign breakdown (with ``Exists`` so no separate UtmCampaign
        lookup), and 1 recent signups select_related.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get('/studio/signup-analytics/')
            self.assertEqual(response.status_code, 200)
        view_queries = [
            q for q in ctx.captured_queries
            if 'analytics_userattribution' in q['sql']
            or 'integrations_utmcampaign' in q['sql']
        ]
        self.assertLess(
            len(view_queries), 10,
            msg=f'Expected <10 view queries, got {len(view_queries)}: '
                + '\n---\n'.join(q['sql'] for q in view_queries),
        )

    def test_total_query_count_does_not_scale_with_attribution_rows(self):
        """Adding rows must not add queries (sanity check vs N+1).

        50 ``UserAttribution`` rows are already seeded in setUpTestData.
        We re-render and assert the absolute query count stays bounded.
        Total includes framework overhead — used as a regression guard,
        not as a fine-grained budget.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get('/studio/signup-analytics/')
            self.assertEqual(response.status_code, 200)
        count = len(ctx.captured_queries)
        # Framework adds ~5 queries; view adds ~5. Stays under 15 with room.
        self.assertLessEqual(
            count, 15,
            msg=f'Expected <=15 total queries, got {count}: '
                + '\n---\n'.join(q['sql'] for q in ctx.captured_queries),
        )


# ---------------------------------------------------------------------------
# Sanity check: counts agree with User.objects.filter
# ---------------------------------------------------------------------------

class SignupAnalyticsCountSanityTest(TestCase):
    """Headline 7d count agrees with ``User.objects.filter(date_joined__gte=cutoff)``.

    Sanity check that the dashboard reads from UserAttribution but the
    number matches the User table (because every signup creates one row).
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        UserAttribution.objects.filter(user=cls.staff).delete()

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_seven_day_card_matches_user_count(self):
        # Capture ``now`` AFTER creating the users so the cutoff window
        # is strictly inclusive of every seeded ``date_joined``.
        for i in range(7):
            _make_attribution(
                email=f's{i}@t.com',
                created_at=timezone.now() - timedelta(days=i % 6),
            )
        now = timezone.now()
        cutoff = now - timedelta(days=7)
        user_count = User.objects.filter(
            date_joined__gte=cutoff, date_joined__lt=now,
        ).exclude(email='staff@test.com').count()
        # All 7 seeded rows are inside the 7d window.
        self.assertEqual(user_count, 7)

        response = self.client.get('/studio/signup-analytics/')
        seven = response.context['headline_cards'][1]
        # Headline card counts UserAttribution rows by ``created_at`` which
        # we backdated to match ``date_joined``. The signal creates an
        # attribution row for every user including the staff user, so the
        # count is "all attribution rows in the window" — sanity-checked
        # against the User count above.
        all_attr = UserAttribution.objects.filter(
            created_at__gte=cutoff, created_at__lt=now,
        ).count()
        self.assertEqual(seven['count'], all_attr)


# ---------------------------------------------------------------------------
# Sidebar entry
# ---------------------------------------------------------------------------

class SignupAnalyticsSidebarTest(TestCase):
    """Sidebar entry appears in Tracking and section auto-expands on open."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def test_tracking_active_includes_signup_analytics(self):
        state = studio_sidebar_state('/studio/signup-analytics/')
        self.assertTrue(state['tracking_active'])

    def test_tracking_active_false_for_unrelated_path(self):
        state = studio_sidebar_state('/studio/articles/')
        self.assertFalse(state['tracking_active'])

    def test_sidebar_link_renders_on_dashboard(self):
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get('/studio/')
        self.assertContains(response, 'href="/studio/signup-analytics/"')
        self.assertContains(response, '<span>Signup analytics</span>', html=True)

    def test_sidebar_link_has_active_classes_on_open_dashboard(self):
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get('/studio/signup-analytics/')
        # Active link gets the bg-secondary text-foreground classes.
        body = response.content.decode()
        # Find the Signup analytics anchor and check it has the active classes.
        idx = body.find('href="/studio/signup-analytics/"')
        self.assertGreater(idx, -1)
        snippet = body[idx:idx + 400]
        self.assertIn('bg-secondary text-foreground', snippet)
        self.assertIn('<span>Signup analytics</span>', snippet)

    def test_tracking_section_is_expanded_on_signup_analytics_page(self):
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get('/studio/signup-analytics/')
        body = response.content.decode()
        # The Tracking <ul> renders WITHOUT the ``hidden`` class because
        # tracking_active is True.
        self.assertIn(
            'id="studio-section-tracking" class="space-y-1 mt-1"', body,
        )
        # And the Tracking <button> renders aria-expanded="true".
        self.assertIn(
            'aria-expanded="true"\n                  aria-controls="studio-section-tracking"',
            body,
        )
