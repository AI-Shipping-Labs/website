"""View, gating, and sitemap tests for company interviews (issue #1712)."""

import xml.etree.ElementTree as ET

from django.test import TestCase, tag

from accounts.models import User
from content.models import InterviewCategory, InterviewCompany
from tests.fixtures import TierSetupMixin, set_membership


def _company(slug, **overrides):
    fields = {
        'company': overrides.pop('company', slug.title()),
        'roles_json': overrides.pop(
            'roles_json', ['Senior AI Engineer'],
        ),
        'process_summary': overrides.pop(
            'process_summary',
            'Three interviews over two weeks, no take-home.',
        ),
        'steps_json': overrides.pop('steps_json', [
            {
                'name': 'Recruiter screen',
                'duration': '30 min',
                'details': 'Intro call with the recruiter.',
            },
            {
                'name': 'Live coding',
                'duration': '1 hour',
                'details': 'Build a working solution via screen sharing.',
            },
        ]),
        'notable': overrides.pop(
            'notable', 'Explicitly states no live coding.',
        ),
        'source_files_json': overrides.pop(
            'source_files_json', ['1393425_example.yaml'],
        ),
        'status': overrides.pop('status', 'published'),
        'required_level': overrides.pop('required_level', 10),
        'source_repo': 'AI-Shipping-Labs/content',
        'source_path': f'interview-companies/{slug}.yaml',
    }
    fields.update(overrides)
    return InterviewCompany(slug=slug, **fields)


class CompanyInterviewListViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.published = _company('nearform', company='Nearform')
        cls.published.save()
        cls.stale = _company('tombstone', company='Tombstone')
        cls.stale.status = 'coming-soon'
        cls.stale.save()

    def test_list_returns_200_anonymous_and_shows_published_cards(self):
        response = self.client.get('/interview/companies')
        # assertContains anchors a 200 response carrying the card content.
        self.assertContains(response, 'Nearform')
        self.assertTemplateUsed(
            response, 'content/company_interviews_list.html',
        )
        content = response.content.decode()
        self.assertIn('Company interviews', content)
        self.assertIn('Three interviews over two weeks, no take-home.', content)
        self.assertIn('2 steps', content)
        self.assertIn('Senior AI Engineer', content)
        # coming-soon rows are excluded from the list.
        self.assertNotIn('Tombstone', content)

    def test_list_orders_cards_by_company_name(self):
        later = _company('acme', company='Acme')
        later.save()
        response = self.client.get('/interview/companies')
        content = response.content.decode()
        self.assertLess(
            content.index('Acme'), content.index('Nearform'),
        )

    def test_list_shows_empty_state_without_companies(self):
        InterviewCompany.objects.all().delete()
        response = self.client.get('/interview/companies')
        self.assertContains(response, 'data-testid="member-empty-state"')
        self.assertContains(response, 'No company interviews yet')


class CompanyInterviewDetailViewTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.published = _company('nearform', company='Nearform')
        cls.published.save()
        cls.stale = _company('tombstone', company='Tombstone')
        cls.stale.status = 'coming-soon'
        cls.stale.save()
        cls.basic_user = User.objects.create_user(email='basic@example.com')
        set_membership(cls.basic_user, tier=cls.basic_tier)
        cls.basic_user.save()
        cls.free_user = User.objects.create_user(email='free@example.com')
        set_membership(cls.free_user, tier=cls.free_tier)
        cls.free_user.save()

    def test_unknown_slug_returns_404(self):
        response = self.client.get('/interview/companies/nope')
        self.assertEqual(response.status_code, 404)

    def test_coming_soon_returns_404(self):
        response = self.client.get('/interview/companies/tombstone')
        self.assertEqual(response.status_code, 404)

    def test_anonymous_sees_teaser_and_upgrade_cta_only(self):
        response = self.client.get('/interview/companies/nearform')
        self.assertTemplateUsed(
            response, 'content/company_interview_detail.html',
        )
        # The gated card anchor doubles as the 200-response contract.
        self.assertContains(response, 'data-testid="gated-access-card"')
        content = response.content.decode()
        # Teaser: company, role, step count, process summary.
        self.assertIn('Nearform', content)
        self.assertIn('Senior AI Engineer', content)
        self.assertIn('2 steps', content)
        self.assertIn(
            'Three interviews over two weeks, no take-home.', content,
        )
        # Standard gated-access card with the Basic upgrade CTA.
        self.assertIn('Basic', content)
        self.assertIn('data-testid="gated-pricing-link"', content)
        self.assertIn('href="/membership"', content)
        # Full steps, notable notes, and provenance stay behind the gate.
        self.assertNotIn('Recruiter screen', content)
        self.assertNotIn('Build a working solution', content)
        self.assertNotIn('Explicitly states no live coding.', content)
        self.assertNotIn('data-testid="company-provenance"', content)

    def test_basic_user_sees_full_steps_notable_and_provenance(self):
        self.client.force_login(self.basic_user)
        response = self.client.get('/interview/companies/nearform')
        self.assertContains(response, 'data-testid="company-steps"')
        content = response.content.decode()
        self.assertNotIn('data-testid="gated-access-card"', content)
        self.assertIn('Recruiter screen', content)
        self.assertIn('30 min', content)
        self.assertIn('Build a working solution via screen sharing.', content)
        self.assertIn('Explicitly states no live coding.', content)
        self.assertIn('data-testid="company-provenance"', content)
        self.assertIn(
            'https://github.com/alexeygrigorev/ai-engineering-field-guide',
            content,
        )

    def test_free_user_sees_gated_card_with_current_access_line(self):
        self.client.force_login(self.free_user)
        response = self.client.get('/interview/companies/nearform')
        self.assertContains(response, 'data-testid="gated-access-card"')
        content = response.content.decode()
        self.assertIn('Current access: Free member', content)
        self.assertNotIn('Recruiter screen', content)

    def test_open_company_renders_steps_for_anonymous(self):
        open_company = _company('opencomp', required_level=0)
        open_company.save()
        response = self.client.get('/interview/companies/opencomp')
        self.assertContains(response, 'Recruiter screen')
        self.assertNotIn(
            'data-testid="gated-access-card"', response.content.decode(),
        )


class InterviewHubCompaniesLinkTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        category = InterviewCategory(
            slug='theory', title='Theory Interview Questions',
        )
        category.save()
        company = _company('nearform', company='Nearform')
        company.save()

    def test_hub_links_to_companies_list(self):
        response = self.client.get('/interview')
        self.assertContains(response, 'href="/interview/companies"')
        content = response.content.decode()
        self.assertIn('Company interviews', content)
        self.assertIn('data-testid="companies-card"', content)

    def test_hub_shows_company_count(self):
        response = self.client.get('/interview')
        self.assertContains(response, 'data-testid="companies-hub-metadata"')
        self.assertContains(response, '1 company')


@tag('core')
class CompanyInterviewsSitemapTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        company = _company('nearform', company='Nearform')
        company.save()

    def _locations(self):
        response = self.client.get('/sitemap.xml')
        # assertContains anchors the 200 response carrying the urlset.
        self.assertContains(response, '<urlset')
        root = ET.fromstring(response.content)
        return [
            url.find('{*}loc').text for url in root.findall('.//{*}url')
        ]

    def test_sitemap_includes_list_and_excludes_gated_details(self):
        locations = self._locations()
        self.assertTrue(
            any(loc.endswith('/interview/companies') for loc in locations),
            f'/interview/companies missing from sitemap: {locations}',
        )
        self.assertFalse(
            any('/interview/companies/' in loc for loc in locations),
            'gated company detail URLs must not appear in the sitemap',
        )
