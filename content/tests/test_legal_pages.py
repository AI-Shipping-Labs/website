"""Tests for legal pages: Terms of Service, Privacy Policy, Impressum (issue #368).

The site uses no-trailing-slash URLs (RemoveTrailingSlashMiddleware redirects
``/foo/`` -> ``/foo``). The product spec links use ``/terms/`` etc., so we
verify both the canonical URLs return 200 directly AND that ``/terms/`` etc.
resolve via redirect to the same 200 response.
"""

from django.test import TestCase


class LegalPagesAccessTests(TestCase):
    """All three pages are public (no auth required) and return 200."""

    def test_terms_canonical_url_returns_200(self):
        response = self.client.get('/terms')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'legal/terms.html')

    def test_privacy_canonical_url_returns_200(self):
        response = self.client.get('/privacy')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'legal/privacy.html')

    def test_impressum_canonical_url_returns_200(self):
        response = self.client.get('/impressum')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'legal/impressum.html')

    def test_trailing_slash_urls_resolve_to_200(self):
        """The spec writes /terms/, /privacy/, /impressum/ as link targets.

        Site middleware 301-redirects to the no-slash canonical, which 200s.
        """
        for path in ('/terms/', '/privacy/', '/impressum/'):
            response = self.client.get(path, follow=True)
            self.assertEqual(response.status_code, 200, msg=f'{path} did not 200')

    def test_legal_pages_do_not_require_login(self):
        """No redirect to /accounts/login/ for anonymous visitors."""
        for path in ('/terms', '/privacy', '/impressum'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('/accounts/login', response.get('Location', '') or '')


class TermsContentTests(TestCase):
    """Terms of Service page contains the locked operator info and headline."""

    def setUp(self):
        self.response = self.client.get('/terms')
        self.body = self.response.content.decode('utf-8')

    def test_contains_page_heading(self):
        self.assertIn('Terms of Service', self.body)

    def test_contains_operator_legal_name(self):
        self.assertIn('DataTalks.Club', self.body)

    def test_contains_operator_address(self):
        self.assertIn('Schönensche Str. 13', self.body)

    def test_contains_vat_number(self):
        self.assertIn('DE343190995', self.body)

    def test_contains_contact_email(self):
        self.assertIn('contact@aishippinglabs.com', self.body)

    def test_contains_last_updated_stamp(self):
        self.assertIn('Last updated', self.body)
        self.assertIn('April 27, 2026', self.body)

    def test_no_draft_or_pending_review_banner(self):
        for forbidden in ('draft', 'Draft', 'pending review', 'Pending review'):
            self.assertNotIn(forbidden, self.body, msg=f'unexpected "{forbidden}" banner')


class PrivacyContentTests(TestCase):
    """Privacy Policy page covers controller, third parties, cookies."""

    def setUp(self):
        self.response = self.client.get('/privacy')
        self.body = self.response.content.decode('utf-8')

    def test_contains_page_heading(self):
        self.assertIn('Privacy Policy', self.body)

    def test_contains_operator_legal_name(self):
        self.assertIn('DataTalks.Club', self.body)

    def test_contains_operator_address(self):
        self.assertIn('Schönensche', self.body)

    def test_contains_vat_number(self):
        self.assertIn('DE343190995', self.body)

    def test_contains_contact_email(self):
        self.assertIn('contact@aishippinglabs.com', self.body)

    def test_mentions_session_and_csrf_cookies(self):
        self.assertIn('sessionid', self.body)
        self.assertIn('csrftoken', self.body)

    def test_mentions_third_party_processors(self):
        for processor in ('Stripe', 'Slack', 'Amazon SES'):
            self.assertIn(processor, self.body)

    def test_mentions_oauth_providers(self):
        self.assertIn('GitHub', self.body)
        self.assertIn('Google', self.body)

    def test_contains_last_updated_stamp(self):
        self.assertIn('Last updated', self.body)


class ImpressumContentTests(TestCase):
    """Impressum page (German) contains required statutory info."""

    def setUp(self):
        self.response = self.client.get('/impressum')
        self.body = self.response.content.decode('utf-8')

    def test_contains_page_heading(self):
        self.assertIn('Impressum', self.body)

    def test_contains_operator_legal_name(self):
        self.assertIn('DataTalks.Club', self.body)

    def test_contains_street_address(self):
        self.assertIn('Schönensche Str. 13', self.body)

    def test_contains_city(self):
        self.assertIn('10439 Berlin', self.body)

    def test_contains_vat_number(self):
        self.assertIn('DE343190995', self.body)

    def test_contains_authorized_representative(self):
        self.assertIn('Alexey Grigorev', self.body)

    def test_contains_contact_email(self):
        self.assertIn('contact@aishippinglabs.com', self.body)

    def test_contains_german_last_updated_stamp(self):
        self.assertIn('Stand', self.body)
        self.assertIn('27. April 2026', self.body)


class SignupLoginConsentLinksTests(TestCase):
    """register.html and login.html have real anchors to /terms/ and /privacy/."""

    def test_register_page_has_terms_link(self):
        response = self.client.get('/accounts/register/')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        self.assertIn('href="/terms/"', body)
        self.assertIn('href="/privacy/"', body)

    def test_login_page_has_terms_link(self):
        response = self.client.get('/accounts/login/')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        self.assertIn('href="/terms/"', body)
        self.assertIn('href="/privacy/"', body)

    def test_register_consent_paragraph_is_anchored(self):
        """The consent line uses real anchors, not bare text."""
        response = self.client.get('/accounts/register/')
        body = response.content.decode('utf-8')
        # Ensure the bare-text wording is gone (replaced by anchored links).
        self.assertNotIn(
            'agree to our Terms of Service and Privacy Policy.',
            body,
        )

    def test_login_consent_paragraph_is_anchored(self):
        response = self.client.get('/accounts/login/')
        body = response.content.decode('utf-8')
        self.assertNotIn(
            'agree to our Terms of Service and Privacy Policy.',
            body,
        )


class FooterLegalLinksTests(TestCase):
    """The global footer surfaces the three legal links under a Legal heading."""

    def test_footer_renders_legal_column_on_about_page(self):
        # /about renders includes/footer.html.
        response = self.client.get('/about')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        self.assertIn('Legal', body)
        self.assertIn('href="/terms/"', body)
        self.assertIn('href="/privacy/"', body)
        self.assertIn('href="/impressum/"', body)


class LegalSitemapTests(TestCase):
    """Sitemap exposes the three legal URLs."""

    def test_sitemap_lists_legal_urls(self):
        response = self.client.get('/sitemap.xml')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        self.assertIn('/terms', body)
        self.assertIn('/privacy', body)
        self.assertIn('/impressum', body)
