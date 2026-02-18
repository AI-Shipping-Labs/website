from django.test import TestCase, RequestFactory
from website.context_processors import site_context


class SiteContextProcessorTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_site_context_keys(self):
        request = self.factory.get('/')
        context = site_context(request)
        self.assertIn('site_name', context)
        self.assertIn('site_url', context)
        self.assertIn('site_description', context)
        self.assertIn('stripe_customer_portal_url', context)
        self.assertIn('current_year', context)

    def test_site_name(self):
        request = self.factory.get('/')
        context = site_context(request)
        self.assertEqual(context['site_name'], 'AI Shipping Labs')

    def test_current_year_is_int(self):
        request = self.factory.get('/')
        context = site_context(request)
        self.assertIsInstance(context['current_year'], int)
