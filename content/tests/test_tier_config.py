from django.test import TestCase

from content.models import SiteConfig
from content.tier_config import get_activities, get_tiers, get_tiers_with_features

# Minimal valid tier data for tests
SAMPLE_TIERS_DATA = [
    {
        'name': 'Basic',
        'stripe_key': 'basic',
        'tagline': 'Content only',
        'price_monthly': 20,
        'price_annual': 200,
        'hook': 'Educational content.',
        'description': 'Access content.',
        'positioning': 'Best for self-paced.',
        'highlighted': False,
        'activities': [
            {
                'title': 'Activity A',
                'icon': 'book-open',
                'description': 'Description A.',
                'features': ['Feature A1', 'Feature A2'],
            },
        ],
    },
    {
        'name': 'Main',
        'stripe_key': 'main',
        'tagline': 'Community',
        'price_monthly': 50,
        'price_annual': 500,
        'hook': 'Build with community.',
        'description': 'Everything in Basic plus community.',
        'positioning': 'Best for teams.',
        'highlighted': True,
        'activities': [
            {
                'title': 'Activity B',
                'icon': 'users',
                'description': 'Description B.',
                'features': ['Feature B1'],
            },
        ],
    },
    {
        'name': 'Premium',
        'stripe_key': 'premium',
        'tagline': 'Courses',
        'price_monthly': 100,
        'price_annual': 1000,
        'hook': 'Structured learning.',
        'description': 'Everything in Main plus courses.',
        'positioning': 'Best for structured learners.',
        'highlighted': False,
        'activities': [
            {
                'title': 'Activity C',
                'icon': 'star',
                'description': 'Description C.',
                'features': ['Feature C1', 'Feature C2'],
            },
        ],
    },
]


def _seed_tiers(data=None):
    """Create a SiteConfig row with tier data."""
    SiteConfig.objects.update_or_create(
        key='tiers',
        defaults={'data': data if data is not None else SAMPLE_TIERS_DATA},
    )


class GetTiersTest(TestCase):
    """Tests for the get_tiers() function reading from the database."""

    def test_loads_tiers_from_db(self):
        _seed_tiers()
        tiers = get_tiers()
        self.assertEqual(len(tiers), 3)
        self.assertEqual(tiers[0]['name'], 'Basic')
        self.assertEqual(tiers[1]['name'], 'Main')
        self.assertEqual(tiers[2]['name'], 'Premium')

    def test_returns_empty_when_no_data_in_db(self):
        self.assertEqual(get_tiers(), [])

    def test_returns_empty_when_data_is_empty_list(self):
        _seed_tiers([])
        self.assertEqual(get_tiers(), [])


class GetTiersWithFeaturesTest(TestCase):
    """Tests for the get_tiers_with_features() function (homepage data)."""

    @classmethod
    def setUpTestData(cls):
        _seed_tiers()

    def test_basic_tier_has_no_inheritance_prefix(self):
        tiers = get_tiers_with_features()
        basic = tiers[0]
        feature_texts = [f['text'] for f in basic['features']]
        self.assertFalse(feature_texts[0].startswith('Everything in'))

    def test_main_tier_starts_with_everything_in_basic(self):
        tiers = get_tiers_with_features()
        main = tiers[1]
        self.assertEqual(main['features'][0]['text'], 'Everything in Basic')

    def test_premium_tier_starts_with_everything_in_main(self):
        tiers = get_tiers_with_features()
        premium = tiers[2]
        self.assertEqual(premium['features'][0]['text'], 'Everything in Main')

    def test_features_collected_from_activities(self):
        tiers = get_tiers_with_features()
        basic = tiers[0]
        feature_texts = [f['text'] for f in basic['features']]
        self.assertIn('Feature A1', feature_texts)
        self.assertIn('Feature A2', feature_texts)

    def test_all_features_have_included_true(self):
        tiers = get_tiers_with_features()
        for tier in tiers:
            for feature in tier['features']:
                self.assertTrue(feature['included'], f"Feature '{feature['text']}' not included")

    def test_feature_counts_per_tier(self):
        """Basic gets its own features, Main gets inheritance + own, Premium gets inheritance + own."""
        tiers = get_tiers_with_features()
        # Basic: 2 features (from Activity A)
        self.assertEqual(len(tiers[0]['features']), 2)
        # Main: 1 inheritance line + 1 feature (from Activity B) = 2
        self.assertEqual(len(tiers[1]['features']), 2)
        # Premium: 1 inheritance line + 2 features (from Activity C) = 3
        self.assertEqual(len(tiers[2]['features']), 3)


class GetActivitiesTest(TestCase):
    """Tests for the get_activities() function (activities page data)."""

    @classmethod
    def setUpTestData(cls):
        _seed_tiers()

    def test_returns_all_activities(self):
        activities = get_activities()
        self.assertEqual(len(activities), 3)
        titles = [a['title'] for a in activities]
        self.assertEqual(titles, ['Activity A', 'Activity B', 'Activity C'])

    def test_basic_activity_inherits_to_all_tiers(self):
        activities = get_activities()
        activity_a = activities[0]
        self.assertEqual(activity_a['tiers'], ['basic', 'main', 'premium'])

    def test_main_activity_inherits_to_main_and_premium(self):
        activities = get_activities()
        activity_b = activities[1]
        self.assertEqual(activity_b['tiers'], ['main', 'premium'])

    def test_premium_activity_only_in_premium(self):
        activities = get_activities()
        activity_c = activities[2]
        self.assertEqual(activity_c['tiers'], ['premium'])

    def test_activity_dict_has_required_keys(self):
        activities = get_activities()
        for activity in activities:
            self.assertIn('icon', activity)
            self.assertIn('title', activity)
            self.assertIn('description', activity)
            self.assertIn('tiers', activity)

    def test_description_is_stripped(self):
        SiteConfig.objects.update_or_create(
            key='tiers',
            defaults={'data': [
                {
                    'name': 'Basic',
                    'stripe_key': 'basic',
                    'tagline': 'T',
                    'price_monthly': 20,
                    'price_annual': 200,
                    'hook': 'H',
                    'description': 'D',
                    'positioning': 'P',
                    'highlighted': False,
                    'activities': [
                        {
                            'title': 'Padded',
                            'icon': 'x',
                            'description': '  padded text  \n',
                            'features': [],
                        },
                    ],
                },
            ]},
        )
        activities = get_activities()
        self.assertEqual(activities[0]['description'], 'padded text')

    def test_deduplicates_activities_by_title(self):
        """If the same title appears under multiple tiers, only the first occurrence is used."""
        SiteConfig.objects.update_or_create(
            key='tiers',
            defaults={'data': [
                {
                    'name': 'Basic',
                    'stripe_key': 'basic',
                    'tagline': 'T',
                    'price_monthly': 20,
                    'price_annual': 200,
                    'hook': 'H',
                    'description': 'D',
                    'positioning': 'P',
                    'highlighted': False,
                    'activities': [
                        {'title': 'Shared', 'icon': 'a', 'description': 'First.', 'features': []},
                    ],
                },
                {
                    'name': 'Main',
                    'stripe_key': 'main',
                    'tagline': 'T',
                    'price_monthly': 50,
                    'price_annual': 500,
                    'hook': 'H',
                    'description': 'D',
                    'positioning': 'P',
                    'highlighted': True,
                    'activities': [
                        {'title': 'Shared', 'icon': 'b', 'description': 'Duplicate.', 'features': []},
                    ],
                },
            ]},
        )
        activities = get_activities()
        shared = [a for a in activities if a['title'] == 'Shared']
        self.assertEqual(len(shared), 1)
        self.assertEqual(shared[0]['icon'], 'a')  # first occurrence wins


class ProductionYamlTest(TestCase):
    """Tests that production tiers.yaml data (loaded into DB) matches expected structure."""

    @classmethod
    def setUpTestData(cls):
        """Load the tiers.yaml fixture into the DB."""
        from pathlib import Path

        import yaml
        fixture_path = Path(__file__).parent / 'fixtures' / 'tiers.yaml'
        with open(fixture_path) as f:
            tiers_data = yaml.safe_load(f)
        SiteConfig.objects.create(key='tiers', data=tiers_data)

    def test_loads_production_data(self):
        tiers = get_tiers()
        self.assertIsInstance(tiers, list)
        self.assertEqual(len(tiers), 3)

    def test_tier_names_are_correct(self):
        tiers = get_tiers()
        names = [t['name'] for t in tiers]
        self.assertEqual(names, ['Basic', 'Main', 'Premium'])

    def test_tier_stripe_keys(self):
        tiers = get_tiers()
        keys = [t['stripe_key'] for t in tiers]
        self.assertEqual(keys, ['basic', 'main', 'premium'])

    def test_main_tier_is_highlighted(self):
        tiers = get_tiers()
        basic, main, premium = tiers
        self.assertFalse(basic['highlighted'])
        self.assertTrue(main['highlighted'])
        self.assertFalse(premium['highlighted'])

    def test_tier_prices(self):
        tiers = get_tiers()
        self.assertEqual(tiers[0]['price_monthly'], 20)
        self.assertEqual(tiers[0]['price_annual'], 200)
        self.assertEqual(tiers[1]['price_monthly'], 50)
        self.assertEqual(tiers[1]['price_annual'], 500)
        self.assertEqual(tiers[2]['price_monthly'], 100)
        self.assertEqual(tiers[2]['price_annual'], 1000)

    def test_activity_counts_per_tier(self):
        """Basic owns 3, Main owns 9, Premium owns 3 activities."""
        tiers = get_tiers()
        self.assertEqual(len(tiers[0]['activities']), 3)
        self.assertEqual(len(tiers[1]['activities']), 9)
        self.assertEqual(len(tiers[2]['activities']), 3)

    def test_total_activities_is_15(self):
        activities = get_activities()
        self.assertEqual(len(activities), 15)

    def test_activities_page_filter_counts(self):
        """Basic shows 3, Main shows 12 (3+9), Premium shows 15 (3+9+3)."""
        activities = get_activities()
        basic_count = len([a for a in activities if 'basic' in a['tiers']])
        main_count = len([a for a in activities if 'main' in a['tiers']])
        premium_count = len([a for a in activities if 'premium' in a['tiers']])
        self.assertEqual(basic_count, 3)
        self.assertEqual(main_count, 12)
        self.assertEqual(premium_count, 15)

    def test_homepage_basic_feature_count(self):
        """Basic tier should have 5 feature bullets on homepage."""
        tiers = get_tiers_with_features()
        basic_features = tiers[0]['features']
        self.assertEqual(len(basic_features), 5)

    def test_homepage_main_feature_count(self):
        """Main tier should have 10 feature bullets (1 inheritance + 9 own)."""
        tiers = get_tiers_with_features()
        main_features = tiers[1]['features']
        self.assertEqual(len(main_features), 10)

    def test_homepage_premium_feature_count(self):
        """Premium tier should have 6 feature bullets (1 inheritance + 5 own)."""
        tiers = get_tiers_with_features()
        premium_features = tiers[2]['features']
        self.assertEqual(len(premium_features), 6)

    def test_homepage_main_starts_with_everything_in_basic(self):
        tiers = get_tiers_with_features()
        self.assertEqual(tiers[1]['features'][0]['text'], 'Everything in Basic')

    def test_homepage_premium_starts_with_everything_in_main(self):
        tiers = get_tiers_with_features()
        self.assertEqual(tiers[2]['features'][0]['text'], 'Everything in Main')


class ActivitiesViewIntegrationTest(TestCase):
    """Test that the activities view correctly uses DB-backed data."""

    @classmethod
    def setUpTestData(cls):
        from pathlib import Path

        import yaml
        fixture_path = Path(__file__).parent / 'fixtures' / 'tiers.yaml'
        with open(fixture_path) as f:
            tiers_data = yaml.safe_load(f)
        SiteConfig.objects.create(key='tiers', data=tiers_data)

    def test_activities_page_shows_all_15_activities(self):
        response = self.client.get('/activities')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['activities']), 15)

    def test_activities_page_basic_count(self):
        response = self.client.get('/activities')
        self.assertEqual(response.context['basic_count'], 3)

    def test_activities_page_main_count(self):
        response = self.client.get('/activities')
        self.assertEqual(response.context['main_count'], 12)

    def test_activities_page_premium_count(self):
        response = self.client.get('/activities')
        self.assertEqual(response.context['premium_count'], 15)

    def test_activities_page_contains_tier_specific_activity(self):
        response = self.client.get('/activities')
        self.assertContains(response, 'Exclusive Substack Content')
        self.assertContains(response, 'Closed Community Access')
        self.assertContains(response, 'Mini-Courses on Specialized Topics')


class HomepageTiersIntegrationTest(TestCase):
    """Test that the homepage correctly uses DB-backed tier data."""

    @classmethod
    def setUpTestData(cls):
        from pathlib import Path

        import yaml
        fixture_path = Path(__file__).parent / 'fixtures' / 'tiers.yaml'
        with open(fixture_path) as f:
            tiers_data = yaml.safe_load(f)
        SiteConfig.objects.create(key='tiers', data=tiers_data)

    def test_homepage_has_three_tiers_in_context(self):
        response = self.client.get('/')
        tiers = response.context['tiers']
        self.assertEqual(len(tiers), 3)

    def test_homepage_tier_names(self):
        response = self.client.get('/')
        tiers = response.context['tiers']
        names = [t['name'] for t in tiers]
        self.assertEqual(names, ['Basic', 'Main', 'Premium'])

    def test_homepage_tiers_have_payment_links(self):
        response = self.client.get('/')
        tiers = response.context['tiers']
        for tier in tiers:
            self.assertIn('payment_link_monthly', tier)
            self.assertIn('payment_link_annual', tier)

    def test_homepage_tiers_have_features(self):
        response = self.client.get('/')
        tiers = response.context['tiers']
        for tier in tiers:
            self.assertIn('features', tier)
            self.assertIsInstance(tier['features'], list)
            self.assertGreater(len(tier['features']), 0)

    def test_homepage_renders_tier_names(self):
        response = self.client.get('/')
        self.assertContains(response, 'Basic')
        self.assertContains(response, 'Main')
        self.assertContains(response, 'Premium')

    def test_homepage_renders_tier_prices(self):
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('20', content)
        self.assertIn('50', content)
        self.assertIn('100', content)
