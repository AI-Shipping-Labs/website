"""Tests for the Studio UTM Analytics views (#196).

Covers acceptance criteria:
- Access control (anonymous, free user, staff)
- Empty-state rendering
- Campaign rollup table sort + content
- Date range filter (7d / 30d / 90d / custom)
- First-touch / last-touch toggle
- utm_source / utm_medium dropdown filters
- Drill-down navigation (campaign + link)
- Pagination on link detail visits table
- KPI strip rendering
- Conversion-rate "n/a" semantics in template output
- Sidebar "Analytics" section presence
- Sparkline column renders inline SVG (no JS lib)
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from analytics.models import CampaignVisit, UserAttribution
from integrations.models import UtmCampaign, UtmCampaignLink
from payments.models import ConversionAttribution, Tier

User = get_user_model()


def _staff_login(client, email='staff@test.com'):
    user = User.objects.create_user(email=email, password='pw', is_staff=True)
    client.login(email=email, password='pw')
    return user


def _user_login(client, email='free@test.com'):
    user = User.objects.create_user(email=email, password='pw', is_staff=False)
    client.login(email=email, password='pw')
    return user


def _seed_visit(slug, *, content='', source='newsletter', medium='email',
                anon='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', ts=None):
    v = CampaignVisit.objects.create(
        utm_campaign=slug,
        utm_content=content,
        utm_source=source,
        utm_medium=medium,
        anonymous_id=anon,
    )
    if ts is not None:
        CampaignVisit.objects.filter(pk=v.pk).update(ts=ts)
    return v


def _seed_attribution(user, *, slug, content='', source='newsletter',
                      medium='email', last_slug=None, last_content=None,
                      last_source=None, last_medium=None,
                      first_ts=None, last_ts=None):
    UserAttribution.objects.filter(user=user).delete()
    return UserAttribution.objects.create(
        user=user,
        first_touch_utm_campaign=slug,
        first_touch_utm_content=content,
        first_touch_utm_source=source,
        first_touch_utm_medium=medium,
        first_touch_ts=first_ts or timezone.now(),
        last_touch_utm_campaign=last_slug or slug,
        last_touch_utm_content=last_content or content,
        last_touch_utm_source=last_source or source,
        last_touch_utm_medium=last_medium or medium,
        last_touch_ts=last_ts or first_ts or timezone.now(),
        signup_path='email_password',
    )


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------

class UtmAnalyticsAccessTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.campaign = UtmCampaign.objects.create(
            name='Test', slug='launch_april',
            default_utm_source='newsletter', default_utm_medium='email',
        )
        self.link = UtmCampaignLink.objects.create(
            campaign=self.campaign, utm_content='ai_hero_list',
            destination='/events/launch',
        )

    def _paths(self):
        return [
            '/studio/utm-analytics/',
            f'/studio/utm-analytics/campaign/{self.campaign.slug}/',
            f'/studio/utm-analytics/campaign/{self.campaign.slug}/link/{self.link.pk}/',
        ]

    def test_anonymous_redirected_to_login(self):
        for path in self._paths():
            response = self.client.get(path)
            self.assertEqual(response.status_code, 302)
            self.assertIn('/accounts/login/', response['Location'])

    def test_free_user_gets_403(self):
        _user_login(self.client)
        for path in self._paths():
            response = self.client.get(path)
            self.assertEqual(response.status_code, 403, path)

    def test_staff_user_gets_200(self):
        _staff_login(self.client)
        for path in self._paths():
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)


# ---------------------------------------------------------------------------
# Dashboard view
# ---------------------------------------------------------------------------

class UtmDashboardTest(TestCase):
    def setUp(self):
        self.client = Client()
        _staff_login(self.client)

    def test_empty_state_message_and_link(self):
        response = self.client.get('/studio/utm-analytics/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No visits captured in this window')
        self.assertContains(response, 'UTM Campaigns')
        # CTA link to the builder
        self.assertContains(response, 'href="/studio/utm-campaigns/"')

    def test_kpi_strip_shows_zero_for_empty(self):
        response = self.client.get('/studio/utm-analytics/')
        kpis = response.context['kpis']
        self.assertEqual(kpis['visits'], 0)
        self.assertEqual(kpis['unique_visitors'], 0)
        self.assertEqual(kpis['signups'], 0)

    def test_table_lists_one_row_per_campaign_sorted_desc(self):
        UtmCampaign.objects.create(
            name='Launch April', slug='launch_april',
            default_utm_source='newsletter', default_utm_medium='email',
        )
        UtmCampaign.objects.create(
            name='Older', slug='older_campaign',
            default_utm_source='newsletter', default_utm_medium='email',
        )
        # 100 visits to launch_april, 50 to older
        for i in range(100):
            _seed_visit('launch_april', anon=f'anon-{i}')
        for i in range(50):
            _seed_visit('older_campaign', anon=f'older-{i}')

        response = self.client.get('/studio/utm-analytics/')
        rows = response.context['rows']
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['slug'], 'launch_april')
        self.assertEqual(rows[0]['visits'], 100)
        self.assertEqual(rows[1]['slug'], 'older_campaign')
        self.assertEqual(rows[1]['visits'], 50)

    def test_conversion_rate_shows_na_when_zero_denominator(self):
        # 1 visit, 0 signups: visit_to_signup is 0.0% (denominator=1).
        # signup_to_paid_pct is "n/a" because signups=0 (denominator=0).
        UtmCampaign.objects.create(
            name='Z', slug='zero_camp',
            default_utm_source='newsletter', default_utm_medium='email',
        )
        _seed_visit('zero_camp', anon='zz1')
        response = self.client.get('/studio/utm-analytics/')
        rows = response.context['rows']
        self.assertEqual(rows[0]['signups'], 0)
        self.assertEqual(rows[0]['visit_to_signup_pct'], '0.0%')
        self.assertEqual(rows[0]['signup_to_paid_pct'], 'n/a')
        # And in the rendered HTML
        self.assertContains(response, 'n/a')

    def test_date_range_filter_changes_window(self):
        # Visit from 60 days ago — outside 30d but inside 90d
        old_ts = timezone.now() - timedelta(days=60)
        _seed_visit('historic', anon='h1', ts=old_ts)
        # Visit today
        _seed_visit('recent', anon='r1')

        # 30d window: only `recent`
        response = self.client.get('/studio/utm-analytics/?range=30d')
        slugs = [r['slug'] for r in response.context['rows']]
        self.assertIn('recent', slugs)
        self.assertNotIn('historic', slugs)

        # 90d window: both
        response = self.client.get('/studio/utm-analytics/?range=90d')
        slugs = [r['slug'] for r in response.context['rows']]
        self.assertIn('recent', slugs)
        self.assertIn('historic', slugs)

    def test_first_vs_last_touch_toggle(self):
        # Seed a visit so the campaigns appear in the rollup
        _seed_visit('campaign_a', anon='split-1')
        _seed_visit('campaign_b', anon='split-2')

        u = User.objects.create_user(email='split@test.com', password='x')
        _seed_attribution(
            u, slug='campaign_a', last_slug='campaign_b',
            source='newsletter', last_source='twitter',
        )

        # First touch credits A
        response = self.client.get('/studio/utm-analytics/?attribution=first_touch')
        rows = {r['slug']: r for r in response.context['rows']}
        self.assertEqual(rows['campaign_a']['signups'], 1)
        self.assertEqual(rows['campaign_b']['signups'], 0)

        # Last touch credits B
        response = self.client.get('/studio/utm-analytics/?attribution=last_touch')
        rows = {r['slug']: r for r in response.context['rows']}
        self.assertEqual(rows['campaign_a']['signups'], 0)
        self.assertEqual(rows['campaign_b']['signups'], 1)

    def test_utm_source_filter_populated_from_window(self):
        _seed_visit('c1', source='newsletter', anon='a1')
        _seed_visit('c1', source='twitter', anon='a2')
        _seed_visit('c1', source='linkedin', anon='a3')
        response = self.client.get('/studio/utm-analytics/')
        sources = response.context['source_options']
        self.assertEqual(sources, ['linkedin', 'newsletter', 'twitter'])

    def test_utm_source_filter_narrows_table(self):
        _seed_visit('c1', source='newsletter', anon='nl1')
        _seed_visit('c2', source='twitter', anon='tw1')
        response = self.client.get('/studio/utm-analytics/?utm_source=newsletter')
        slugs = [r['slug'] for r in response.context['rows']]
        self.assertIn('c1', slugs)
        self.assertNotIn('c2', slugs)

    def test_sparkline_renders_inline_svg(self):
        _seed_visit('c1', anon='s1')
        _seed_visit('c1', anon='s2', ts=timezone.now() - timedelta(days=2))
        response = self.client.get('/studio/utm-analytics/')
        # Must render <svg> inline, not load any JS chart lib
        self.assertContains(response, '<svg')
        self.assertContains(response, '<polyline')
        self.assertNotContains(response, 'chart.js')
        self.assertNotContains(response, 'd3.min')

    def test_includes_paid_columns_when_conversion_data_available(self):
        response = self.client.get('/studio/utm-analytics/')
        # ConversionAttribution model exists -> has_conversion_data is True
        self.assertTrue(response.context['has_conversion_data'])
        self.assertContains(response, 'Paid Conversions')
        self.assertContains(response, 'MRR')


# ---------------------------------------------------------------------------
# Shared metrics table component
# ---------------------------------------------------------------------------

class SharedMetricsTableTest(TestCase):
    def setUp(self):
        self.client = Client()
        _staff_login(self.client)
        self.campaign = UtmCampaign.objects.create(
            name='Launch April', slug='launch_april',
            default_utm_source='newsletter', default_utm_medium='email',
        )
        self.link = UtmCampaignLink.objects.create(
            campaign=self.campaign, utm_content='ai_hero_list',
            destination='/events/launch', label='AI Hero list',
        )
        _seed_visit('launch_april', content='ai_hero_list', anon='shared-1')
        _seed_visit(
            'launch_april', content='ai_hero_list',
            anon='shared-2', ts=timezone.now() - timedelta(days=2),
        )

    def test_dashboard_and_campaign_detail_use_shared_metrics_table(self):
        pages = [
            self.client.get('/studio/utm-analytics/'),
            self.client.get('/studio/utm-analytics/campaign/launch_april/'),
        ]

        for response in pages:
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(
                response, 'studio/utm_analytics/_metrics_table.html'
            )
            self.assertTemplateUsed(
                response, 'studio/utm_analytics/_metrics_row.html'
            )
            self.assertTemplateUsed(
                response, 'studio/utm_analytics/_sparkline.html'
            )
            self.assertContains(response, 'Visits')
            self.assertContains(response, 'Unique')
            self.assertContains(response, 'Signups')
            self.assertContains(response, 'Visit -&gt; Signup')
            self.assertContains(response, '<svg')
            self.assertContains(response, '<polyline')

    def test_shared_conversion_columns_render_on_both_pages(self):
        tier = Tier.objects.create(
            slug='tier_shared_mrr', name='Shared', level=98,
            price_eur_month=10, price_eur_year=120,
        )
        user = User.objects.create_user(email='shared-paid@test.com', password='x')
        ConversionAttribution.objects.create(
            user=user, stripe_session_id='cs_shared_1',
            tier=tier, billing_period='monthly',
            amount_eur=10, mrr_eur=10,
            first_touch_utm_campaign='launch_april',
            first_touch_utm_content='ai_hero_list',
        )

        for path in (
            '/studio/utm-analytics/',
            '/studio/utm-analytics/campaign/launch_april/',
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Paid')
            self.assertContains(response, 'Signup -&gt; Paid')
            self.assertContains(response, 'MRR EUR')
            self.assertContains(response, 'EUR 10')

    def test_shared_conversion_columns_hide_on_both_pages_without_data(self):
        with patch('analytics.aggregations.has_conversion_data', return_value=False):
            for path in (
                '/studio/utm-analytics/',
                '/studio/utm-analytics/campaign/launch_april/',
            ):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, 'Signup -&gt; Paid')
                self.assertNotContains(response, 'MRR EUR')
                self.assertNotContains(response, 'EUR 0')

    def test_parent_pages_keep_page_specific_cells_and_actions(self):
        _seed_visit(
            'launch_april', content='unminted_list', anon='unminted-1',
        )

        dashboard = self.client.get('/studio/utm-analytics/?range=7d')
        self.assertContains(dashboard, 'Launch April')
        self.assertContains(dashboard, 'launch_april')
        self.assertContains(dashboard, 'newsletter')
        self.assertContains(dashboard, '/ email')
        self.assertContains(
            dashboard,
            'href="/studio/utm-analytics/campaign/launch_april/?range=7d"',
        )
        self.assertContains(
            dashboard,
            f'href="/studio/utm-campaigns/{self.campaign.pk}/"',
        )

        detail = self.client.get('/studio/utm-analytics/campaign/launch_april/')
        self.assertContains(detail, 'ai_hero_list')
        self.assertContains(detail, 'AI Hero list')
        self.assertContains(detail, '/events/launch')
        self.assertContains(
            detail,
            f'/studio/utm-analytics/campaign/launch_april/link/{self.link.pk}/',
        )
        self.assertContains(detail, 'unminted_list')
        self.assertContains(detail, 'No minted link')


# ---------------------------------------------------------------------------
# Campaign drill-down
# ---------------------------------------------------------------------------

class UtmCampaignDetailViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        _staff_login(self.client)
        self.campaign = UtmCampaign.objects.create(
            name='Launch April', slug='launch_april',
            default_utm_source='newsletter', default_utm_medium='email',
        )
        self.link_hero = UtmCampaignLink.objects.create(
            campaign=self.campaign, utm_content='ai_hero_list',
            destination='/events/launch', label='AI Hero list',
        )
        self.link_maven = UtmCampaignLink.objects.create(
            campaign=self.campaign, utm_content='maven_list',
            destination='/events/launch',
        )
        # 60 visits to ai_hero_list (40 unique), 35 to maven_list (35 unique)
        for i in range(60):
            anon = f'hero-{i % 40}'
            _seed_visit('launch_april', content='ai_hero_list', anon=anon)
        for i in range(35):
            _seed_visit('launch_april', content='maven_list', anon=f'maven-{i}')

    def test_breadcrumb_contains_campaign_name(self):
        response = self.client.get('/studio/utm-analytics/campaign/launch_april/')
        self.assertContains(response, 'UTM Analytics')
        self.assertContains(response, 'Launch April')

    def test_one_row_per_utm_content_sorted_desc(self):
        response = self.client.get('/studio/utm-analytics/campaign/launch_april/')
        rows = response.context['rows']
        contents = [r['utm_content'] for r in rows]
        self.assertEqual(contents, ['ai_hero_list', 'maven_list'])
        self.assertEqual(rows[0]['visits'], 60)
        self.assertEqual(rows[1]['visits'], 35)

    def test_view_link_action_present_when_link_exists(self):
        response = self.client.get('/studio/utm-analytics/campaign/launch_april/')
        # Link to the link detail page should appear
        self.assertContains(
            response,
            f'/studio/utm-analytics/campaign/launch_april/link/{self.link_hero.pk}/',
        )

    def test_label_shown_beneath_utm_content(self):
        response = self.client.get('/studio/utm-analytics/campaign/launch_april/')
        self.assertContains(response, 'AI Hero list')

    def test_destination_shown_truncated(self):
        response = self.client.get('/studio/utm-analytics/campaign/launch_april/')
        self.assertContains(response, '/events/launch')

    def test_unknown_campaign_slug_renders_with_blank_table(self):
        response = self.client.get('/studio/utm-analytics/campaign/never_existed/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['rows'], [])


# ---------------------------------------------------------------------------
# Link drill-down
# ---------------------------------------------------------------------------

class UtmLinkDetailViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        _staff_login(self.client)
        self.campaign = UtmCampaign.objects.create(
            name='Launch April', slug='launch_april',
            default_utm_source='newsletter', default_utm_medium='email',
        )
        self.link = UtmCampaignLink.objects.create(
            campaign=self.campaign, utm_content='ai_hero_list',
            destination='/events/launch', label='AI Hero list',
        )
        # 60 visits
        for i in range(60):
            _seed_visit(
                'launch_april', content='ai_hero_list',
                anon=f'visitor-{i:02d}',
            )

    def test_assembled_url_shown_with_copy_button(self):
        response = self.client.get(
            f'/studio/utm-analytics/campaign/launch_april/link/{self.link.pk}/'
        )
        self.assertContains(response, 'utm_campaign=launch_april')
        self.assertContains(response, 'utm_content=ai_hero_list')
        self.assertContains(response, 'Copy')

    def test_visits_table_paginates_at_20(self):
        response = self.client.get(
            f'/studio/utm-analytics/campaign/launch_april/link/{self.link.pk}/'
        )
        self.assertEqual(response.context['paginator'].num_pages, 3)
        self.assertEqual(len(response.context['visits_page'].object_list), 20)

    def test_pagination_page_2_shows_next_20(self):
        response = self.client.get(
            f'/studio/utm-analytics/campaign/launch_april/link/{self.link.pk}/?page=2'
        )
        self.assertEqual(response.context['visits_page'].number, 2)
        self.assertEqual(len(response.context['visits_page'].object_list), 20)

    def test_conversion_rows_for_attributed_users(self):
        # 8 users attributed to ai_hero_list
        for i in range(8):
            u = User.objects.create_user(
                email=f'paid-{i}@test.com', password='x',
            )
            _seed_attribution(
                u, slug='launch_april', content='ai_hero_list',
            )
        response = self.client.get(
            f'/studio/utm-analytics/campaign/launch_april/link/{self.link.pk}/'
        )
        self.assertEqual(len(response.context['conversion_rows']), 8)
        for row in response.context['conversion_rows']:
            self.assertIn('@test.com', row['email'])

    def test_link_not_under_campaign_returns_404(self):
        other = UtmCampaign.objects.create(
            name='Other', slug='other_camp',
            default_utm_source='newsletter', default_utm_medium='email',
        )
        other_link = UtmCampaignLink.objects.create(
            campaign=other, utm_content='x', destination='/x',
        )
        response = self.client.get(
            f'/studio/utm-analytics/campaign/launch_april/link/{other_link.pk}/'
        )
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# Filter preservation across drill-downs
# ---------------------------------------------------------------------------

class FilterPreservationTest(TestCase):
    def setUp(self):
        self.client = Client()
        _staff_login(self.client)
        UtmCampaign.objects.create(
            name='Launch April', slug='launch_april',
            default_utm_source='newsletter', default_utm_medium='email',
        )
        for i in range(5):
            _seed_visit('launch_april', anon=f'a{i}')

    def test_dashboard_drill_down_link_preserves_range(self):
        response = self.client.get('/studio/utm-analytics/?range=7d')
        # The drill-down link in the row must include ?range=7d
        self.assertContains(
            response,
            'href="/studio/utm-analytics/campaign/launch_april/?range=7d"',
        )

    def test_campaign_drill_down_link_preserves_attribution(self):
        response = self.client.get(
            '/studio/utm-analytics/?attribution=last_touch'
        )
        self.assertContains(response, 'attribution=last_touch')


# ---------------------------------------------------------------------------
# Conversion data graceful degradation
# ---------------------------------------------------------------------------

class GracefulDegradationTest(TestCase):
    """When ConversionAttribution data is absent, MRR + Paid columns hide.

    We can't physically remove the model so we monkeypatch the
    ``has_conversion_data`` check used by the dashboard view + template.
    """

    def setUp(self):
        self.client = Client()
        _staff_login(self.client)

    def test_paid_columns_hidden_when_conversion_data_absent(self):
        from analytics import aggregations
        original = aggregations.has_conversion_data
        aggregations.has_conversion_data = lambda: False
        try:
            response = self.client.get('/studio/utm-analytics/')
            self.assertEqual(response.status_code, 200)
            # KPI cards for paid + MRR should NOT be rendered
            self.assertNotContains(response, 'Paid Conversions')
            self.assertNotContains(response, 'MRR Added')
        finally:
            aggregations.has_conversion_data = original


# ---------------------------------------------------------------------------
# MRR rendering
# ---------------------------------------------------------------------------

class MrrRenderingTest(TestCase):
    def setUp(self):
        self.client = Client()
        _staff_login(self.client)
        self.campaign = UtmCampaign.objects.create(
            name='Launch April', slug='launch_april',
            default_utm_source='newsletter', default_utm_medium='email',
        )
        self.tier = Tier.objects.create(
            slug='tier_test_mrr', name='Test', level=98,
            price_eur_month=10, price_eur_year=120,
        )
        # Generate one visit so launch_april appears in the rollup
        _seed_visit('launch_april', anon='vmrr1')
        u = User.objects.create_user(email='paid-mrr@test.com', password='x')
        ConversionAttribution.objects.create(
            user=u, stripe_session_id='cs_mrr_1',
            tier=self.tier, billing_period='monthly',
            amount_eur=10, mrr_eur=10,
            first_touch_utm_campaign='launch_april',
        )
        u2 = User.objects.create_user(email='paid-mrr-2@test.com', password='x')
        ConversionAttribution.objects.create(
            user=u2, stripe_session_id='cs_mrr_2',
            tier=self.tier, billing_period='yearly',
            amount_eur=120, mrr_eur=10,
            first_touch_utm_campaign='launch_april',
        )

    def test_dashboard_shows_mrr_eur_summed(self):
        response = self.client.get('/studio/utm-analytics/')
        rows = response.context['rows']
        launch = next(r for r in rows if r['slug'] == 'launch_april')
        # 10 (monthly) + 10 (annual normalised) = 20
        self.assertEqual(launch['mrr'], 20)
        self.assertEqual(launch['conversions'], 2)
        self.assertContains(response, 'EUR 20')


# ---------------------------------------------------------------------------
# Sidebar nav (acceptance criterion)
# ---------------------------------------------------------------------------

class SidebarTest(TestCase):
    def setUp(self):
        self.client = Client()
        _staff_login(self.client)

    def test_dashboard_sidebar_has_analytics_section(self):
        response = self.client.get('/studio/')
        self.assertEqual(response.status_code, 200)
        # The "Analytics" group label
        self.assertContains(response, 'Analytics')
        # Both links present
        self.assertContains(response, 'href="/studio/utm-campaigns/"')
        self.assertContains(response, 'href="/studio/utm-analytics/"')
