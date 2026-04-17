"""Tests for the analytics.aggregations helper module (#196).

These cover the unit-level guarantees the dashboard view relies on:
- Empty data returns 0 / Decimal('0') / 'n/a' rather than blowing up.
- First-touch vs last-touch swaps which UserAttribution fields are queried.
- Annual subscriptions are normalised to monthly when summing MRR.
- Conversion-rate formatter shows 'n/a' on zero denominators.
- Date-window helpers parse `7d`, `30d`, `90d`, and `custom` correctly.
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from analytics import aggregations
from analytics.models import CampaignVisit, UserAttribution
from integrations.models import UtmCampaign

User = get_user_model()


def _make_visit(slug, *, content='', source='newsletter', medium='email',
                anon='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', ts=None):
    visit = CampaignVisit.objects.create(
        utm_campaign=slug,
        utm_content=content,
        utm_source=source,
        utm_medium=medium,
        anonymous_id=anon,
    )
    if ts is not None:
        CampaignVisit.objects.filter(pk=visit.pk).update(ts=ts)
        visit.refresh_from_db()
    return visit


def _make_attribution(user, *, slug='launch_april', content='ai_hero_list',
                      source='newsletter', medium='email',
                      first_ts=None, last_slug=None, last_content=None,
                      last_source=None, last_medium=None, last_ts=None):
    """Create or replace a UserAttribution row for the given user."""
    UserAttribution.objects.filter(user=user).delete()
    attr = UserAttribution.objects.create(
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
    return attr


class ResolveWindowTest(TestCase):
    def test_default_range_is_30_days(self):
        now = datetime(2026, 4, 17, 12, 0, tzinfo=dt_timezone.utc)
        start, end = aggregations.resolve_window('30d', now=now)
        self.assertEqual((end - start).days, 30)

    def test_seven_day_window(self):
        now = datetime(2026, 4, 17, 12, 0, tzinfo=dt_timezone.utc)
        start, end = aggregations.resolve_window('7d', now=now)
        self.assertEqual((end - start).days, 7)

    def test_invalid_range_falls_back_to_default(self):
        now = datetime(2026, 4, 17, 12, 0, tzinfo=dt_timezone.utc)
        start, end = aggregations.resolve_window('garbage', now=now)
        self.assertEqual((end - start).days, 30)

    def test_custom_range_with_iso_dates(self):
        start, end = aggregations.resolve_window('custom', '2026-04-01', '2026-04-10')
        self.assertEqual(start.year, 2026)
        self.assertEqual(start.month, 4)
        self.assertEqual(start.day, 1)
        self.assertEqual(end.day, 10)
        # End should be end-of-day so the date is inclusive.
        self.assertEqual(end.hour, 23)

    def test_custom_range_with_invalid_dates_falls_back(self):
        now = datetime(2026, 4, 17, 12, 0, tzinfo=dt_timezone.utc)
        start, end = aggregations.resolve_window('custom', 'not-a-date', '', now=now)
        self.assertEqual((end - start).days, 30)


class ConversionRateTest(TestCase):
    def test_zero_denominator_returns_na(self):
        self.assertEqual(aggregations.conversion_rate(0, 0), 'n/a')
        self.assertEqual(aggregations.conversion_rate(5, 0), 'n/a')

    def test_normal_rate_one_decimal(self):
        self.assertEqual(aggregations.conversion_rate(10, 100), '10.0%')

    def test_fractional_rate(self):
        self.assertEqual(aggregations.conversion_rate(1, 3), '33.3%')

    def test_zero_numerator_returns_zero_pct(self):
        # Spec: "n/a" only when denominator is zero. With 0/100 we want "0.0%".
        self.assertEqual(aggregations.conversion_rate(0, 100), '0.0%')


class EmptyDataTest(TestCase):
    """All helpers must return safe defaults with no data in the DB."""

    def test_visit_count_is_zero(self):
        start = timezone.now() - timedelta(days=30)
        end = timezone.now()
        self.assertEqual(aggregations.visit_count(start, end), 0)

    def test_unique_visitor_count_is_zero(self):
        start = timezone.now() - timedelta(days=30)
        end = timezone.now()
        self.assertEqual(aggregations.unique_visitor_count(start, end), 0)

    def test_signup_count_is_zero(self):
        start = timezone.now() - timedelta(days=30)
        end = timezone.now()
        self.assertEqual(aggregations.signup_count(start, end), 0)

    def test_mrr_is_decimal_zero(self):
        start = timezone.now() - timedelta(days=30)
        end = timezone.now()
        self.assertEqual(aggregations.mrr_for(start, end), Decimal('0'))

    def test_campaign_rollup_is_empty_list(self):
        start = timezone.now() - timedelta(days=30)
        end = timezone.now()
        self.assertEqual(aggregations.campaign_rollup(start, end), [])

    def test_kpi_strip_all_zero(self):
        start = timezone.now() - timedelta(days=30)
        end = timezone.now()
        kpis = aggregations.kpi_strip(start, end)
        self.assertEqual(kpis['visits'], 0)
        self.assertEqual(kpis['unique_visitors'], 0)
        self.assertEqual(kpis['signups'], 0)
        self.assertEqual(kpis['conversions'], 0)
        self.assertEqual(kpis['mrr'], Decimal('0'))


class CampaignRollupTest(TestCase):
    """Top-level rollup with seeded data."""

    def setUp(self):
        UtmCampaign.objects.create(
            name='Launch April', slug='launch_april',
            default_utm_source='newsletter', default_utm_medium='email',
        )
        # 3 visits, 2 unique
        _make_visit('launch_april', anon='a1')
        _make_visit('launch_april', anon='a1')
        _make_visit('launch_april', anon='a2')

        # 1 unique visit on a different campaign
        _make_visit('older_campaign', anon='b1')

    def test_returns_one_row_per_campaign_with_visits(self):
        start = timezone.now() - timedelta(days=1)
        end = timezone.now() + timedelta(hours=1)
        rows = aggregations.campaign_rollup(start, end)
        slugs = [r['slug'] for r in rows]
        self.assertIn('launch_april', slugs)
        self.assertIn('older_campaign', slugs)

    def test_rows_sorted_by_visits_desc(self):
        start = timezone.now() - timedelta(days=1)
        end = timezone.now() + timedelta(hours=1)
        rows = aggregations.campaign_rollup(start, end)
        # launch_april has 3 visits, older_campaign has 1
        self.assertEqual(rows[0]['slug'], 'launch_april')
        self.assertEqual(rows[0]['visits'], 3)
        self.assertEqual(rows[0]['unique_visitors'], 2)

    def test_visit_to_signup_uses_unique_visitors(self):
        # Add a signup attributed to launch_april
        u = User.objects.create_user(email='alice@test.com', password='x')
        _make_attribution(u, slug='launch_april')
        start = timezone.now() - timedelta(days=1)
        end = timezone.now() + timedelta(hours=1)
        rows = aggregations.campaign_rollup(start, end)
        launch = next(r for r in rows if r['slug'] == 'launch_april')
        # 1 signup / 2 unique visitors = 50%
        self.assertEqual(launch['signups'], 1)
        self.assertEqual(launch['visit_to_signup_pct'], '50.0%')

    def test_zero_signups_shows_na_for_signup_to_paid(self):
        start = timezone.now() - timedelta(days=1)
        end = timezone.now() + timedelta(hours=1)
        rows = aggregations.campaign_rollup(start, end)
        for r in rows:
            self.assertEqual(r['signup_to_paid_pct'], 'n/a')


class FirstVsLastTouchTest(TestCase):
    """Toggling attribution swaps which UserAttribution fields are queried."""

    def setUp(self):
        # User who first arrived via campaign A and later via campaign B.
        self.user = User.objects.create_user(email='split@test.com', password='x')
        _make_attribution(
            self.user,
            slug='campaign_a', content='nl1', source='newsletter',
            last_slug='campaign_b', last_content='tw1', last_source='twitter',
        )

    def test_first_touch_credits_campaign_a(self):
        start = timezone.now() - timedelta(days=1)
        end = timezone.now() + timedelta(hours=1)
        a = aggregations.signup_count(start, end, campaign_slug='campaign_a',
                                      attribution='first_touch')
        b = aggregations.signup_count(start, end, campaign_slug='campaign_b',
                                      attribution='first_touch')
        self.assertEqual(a, 1)
        self.assertEqual(b, 0)

    def test_last_touch_credits_campaign_b(self):
        start = timezone.now() - timedelta(days=1)
        end = timezone.now() + timedelta(hours=1)
        a = aggregations.signup_count(start, end, campaign_slug='campaign_a',
                                      attribution='last_touch')
        b = aggregations.signup_count(start, end, campaign_slug='campaign_b',
                                      attribution='last_touch')
        self.assertEqual(a, 0)
        self.assertEqual(b, 1)


class MrrAnnualNormalisationTest(TestCase):
    """Annual subscriptions normalised to monthly when summing MRR."""

    def setUp(self):
        from payments.models import ConversionAttribution, Tier
        self.tier_basic = Tier.objects.create(
            slug='basic_test', name='Basic', level=99,
            price_eur_month=10, price_eur_year=120,
        )
        self.user1 = User.objects.create_user(email='a@test.com', password='x')
        self.user2 = User.objects.create_user(email='b@test.com', password='x')
        # Monthly conversion: mrr_eur stored as price_eur_month
        ConversionAttribution.objects.create(
            user=self.user1,
            stripe_session_id='cs_monthly_1',
            tier=self.tier_basic,
            billing_period='monthly',
            amount_eur=10,
            mrr_eur=10,
            first_touch_utm_campaign='launch_april',
        )
        # Annual conversion: mrr_eur stored as price_eur_year // 12
        ConversionAttribution.objects.create(
            user=self.user2,
            stripe_session_id='cs_annual_1',
            tier=self.tier_basic,
            billing_period='yearly',
            amount_eur=120,
            mrr_eur=120 // 12,
            first_touch_utm_campaign='launch_april',
        )

    def test_mrr_sums_monthly_plus_normalised_annual(self):
        start = timezone.now() - timedelta(days=1)
        end = timezone.now() + timedelta(hours=1)
        total = aggregations.mrr_for(start, end, campaign_slug='launch_april')
        # 10 (monthly) + 10 (120/12) = 20
        self.assertEqual(total, Decimal(20))

    def test_one_off_purchase_with_null_mrr_excluded(self):
        from payments.models import ConversionAttribution
        u3 = User.objects.create_user(email='c@test.com', password='x')
        ConversionAttribution.objects.create(
            user=u3,
            stripe_session_id='cs_course_1',
            tier=None,
            billing_period='',
            amount_eur=49,
            mrr_eur=None,
            first_touch_utm_campaign='launch_april',
        )
        start = timezone.now() - timedelta(days=1)
        end = timezone.now() + timedelta(hours=1)
        total = aggregations.mrr_for(start, end, campaign_slug='launch_april')
        # Same 20 as above — the one-off shouldn't add anything
        self.assertEqual(total, Decimal(20))


class SparklinePolylineTest(TestCase):
    def test_empty_returns_blank(self):
        self.assertEqual(aggregations.sparkline_polyline([]), '')

    def test_single_point_returns_blank(self):
        from datetime import date
        self.assertEqual(aggregations.sparkline_polyline([(date.today(), 5)]), '')

    def test_multi_point_renders_x_y_pairs(self):
        from datetime import date
        from datetime import timedelta as td
        today = date.today()
        buckets = [(today - td(days=i), i) for i in range(5)]
        result = aggregations.sparkline_polyline(buckets, width=100, height=20)
        # 5 points means 5 space-separated x,y pairs
        self.assertEqual(len(result.split(' ')), 5)
        # Last point should be at x=100 (rightmost)
        self.assertTrue(result.endswith(',0.0'))


class FilterOptionsTest(TestCase):
    """Source / medium dropdowns are populated from distinct visit values."""

    def setUp(self):
        _make_visit('c1', source='newsletter', medium='email')
        _make_visit('c1', source='twitter', medium='social')
        _make_visit('c1', source='newsletter', medium='email')  # dup

    def test_returns_distinct_sorted_values(self):
        start = timezone.now() - timedelta(days=1)
        end = timezone.now() + timedelta(hours=1)
        opts = aggregations.filter_options(start, end)
        self.assertEqual(opts['sources'], ['newsletter', 'twitter'])
        self.assertEqual(opts['mediums'], ['email', 'social'])
