"""Footer information-architecture coverage (issue #1356).

The sitewide footer renders a brand block plus four honestly-labelled
columns — Community, Resources, About, Legal — a conditional
Downloadables item, the ``marketing_nav`` loops, and a social row whose
icons render only when their URL is configured. ``Manage Subscription``
was removed. These tests render the footer through the homepage view and
assert on the corrected structure.
"""

import re

from django.test import TestCase

from content.nav_availability import (
    set_marketing_pages_nav,
    set_published_downloads_nav_available,
)
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting


def _reset_shared_nav_state():
    """Reset the process-global nav/config caches to their defaults.

    ``set_published_downloads_nav_available`` / ``set_marketing_pages_nav``
    mutate module globals and a shared cache; restoring them prevents a
    footer test from leaking state into unrelated tests.
    """
    set_published_downloads_nav_available(False)
    set_marketing_pages_nav({'about': [], 'community': [], 'resources': []})
    IntegrationSetting.objects.filter(key__startswith='SOCIAL_').delete()
    clear_config_cache()


class _StubMarketingPage:
    """Module-level (picklable) stand-in for a MarketingPage nav entry.

    The marketing-nav cache pickles the value, so the stub cannot be a
    local class defined inside a test method.
    """

    def __init__(self, nav_text, url):
        self.nav_text = nav_text
        self._url = url

    def get_absolute_url(self):
        return self._url


def _footer_html(html):
    """Return only the ``<footer>...</footer>`` slice of a rendered page.

    Scopes assertions to the footer so header-nav links (which still
    surface Project Ideas / Curated Links via ``primary_nav``) do not
    leak into footer assertions.
    """
    match = re.search(r'<footer.*?</footer>', html, re.DOTALL)
    return match.group(0) if match else ''


def _column_ul(html, heading):
    """Return the ``<ul>`` markup for the footer column with ``heading``.

    Scopes assertions to a single column so "link X is in the Community
    column" is distinguishable from "link X appears somewhere in the
    footer".
    """
    match = re.search(
        rf'{heading}</h3>\s*(<ul.*?</ul>)',
        html,
        re.DOTALL,
    )
    return match.group(1) if match else ''


class FooterInformationArchitectureTest(TestCase):
    """Anonymous render of the sitewide footer via the homepage."""

    def setUp(self):
        # The homepage is anonymous by default; reset the two cached nav
        # flags the footer consumes so each test controls them explicitly.
        set_published_downloads_nav_available(False)
        set_marketing_pages_nav(
            {'about': [], 'community': [], 'resources': []}
        )
        IntegrationSetting.objects.filter(
            key__startswith='SOCIAL_'
        ).delete()
        clear_config_cache()
        # Restore the shared nav/config globals after each test so a
        # mutation here (e.g. Downloads flag flipped True, or a stub
        # marketing page) cannot leak into other tests in the worker.
        self.addCleanup(_reset_shared_nav_state)

    def _get_footer(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        return _footer_html(response.content.decode())

    def test_four_columns_render_in_order(self):
        html = self._get_footer()
        headings = re.findall(
            r'<h3 class="text-sm font-semibold text-foreground">'
            r'(Community|Resources|About|Legal)</h3>',
            html,
        )
        self.assertEqual(
            headings, ['Community', 'Resources', 'About', 'Legal']
        )

    def test_community_column_contents(self):
        col = _column_ul(self._get_footer(), 'Community')
        self.assertIn('href="/membership#activities"', col)
        self.assertIn('href="/sprints"', col)
        self.assertIn('href="/events"', col)
        # Past Recordings was dropped from the nav + footer (Aug 2026 redesign).
        self.assertNotIn('href="/events?filter=past"', col)
        self.assertIn('href="/books"', col)
        self.assertIn('href="/community/slack"', col)
        # The old catch-all links must not live in the Community column.
        self.assertNotIn('Membership Tiers', col)
        self.assertNotIn('>FAQ<', col)
        self.assertNotIn('>Team<', col)
        self.assertNotIn('Manage Subscription', col)

    def test_resources_column_contents(self):
        col = _column_ul(self._get_footer(), 'Resources')
        self.assertIn('href="/blog"', col)
        self.assertIn('href="/courses"', col)
        self.assertIn('href="/workshops"', col)
        self.assertIn('href="/learning-path/ai-engineer"', col)
        self.assertIn('href="/interview"', col)

    def test_downloadables_conditional_on_published_downloads(self):
        set_published_downloads_nav_available(False)
        col = _column_ul(self._get_footer(), 'Resources')
        self.assertNotIn('href="/downloads"', col)

        set_published_downloads_nav_available(True)
        col = _column_ul(self._get_footer(), 'Resources')
        self.assertIn('href="/downloads"', col)
        self.assertIn('Downloadables', col)

    def test_about_column_contents(self):
        col = _column_ul(self._get_footer(), 'About')
        self.assertIn('href="/about"', col)
        self.assertIn('>Team<', col)
        self.assertIn('href="/faq"', col)
        self.assertIn('Membership Tiers', col)
        self.assertIn('href="/membership"', col)

    def test_legal_column_unchanged(self):
        col = _column_ul(self._get_footer(), 'Legal')
        self.assertIn('href="/terms/"', col)
        self.assertIn('href="/privacy/"', col)
        self.assertIn('data-testid="analytics-preferences-open"', col)
        self.assertIn('href="/impressum/"', col)

    def test_manage_subscription_removed(self):
        html = self._get_footer()
        self.assertNotIn('Manage Subscription', html)

    def test_project_ideas_and_curated_links_absent(self):
        html = self._get_footer()
        # Footer must not surface the still-hidden #1355 destinations.
        self.assertNotIn('Project Ideas', html)
        self.assertNotIn('Curated Links', html)

    def test_slack_join_link_present_in_social_row(self):
        html = self._get_footer()
        self.assertIn('data-testid="footer-social-slack"', html)
        self.assertIn('href="/community/slack"', html)

    def test_marketing_resources_page_appears_after_fixed_links(self):
        set_marketing_pages_nav(
            {
                'about': [],
                'community': [],
                'resources': [
                    _StubMarketingPage('Playbooks', '/pages/playbooks')
                ],
            }
        )
        col = _column_ul(self._get_footer(), 'Resources')
        self.assertIn('href="/pages/playbooks"', col)
        self.assertIn('Playbooks', col)
        # The marketing page follows the fixed content-library links.
        self.assertLess(col.index('/interview'), col.index('/pages/playbooks'))


class FooterSocialIconsTest(TestCase):
    """Conditional, graceful rendering of the brand-block social icons."""

    def setUp(self):
        set_published_downloads_nav_available(False)
        set_marketing_pages_nav(
            {'about': [], 'community': [], 'resources': []}
        )
        IntegrationSetting.objects.filter(
            key__startswith='SOCIAL_'
        ).delete()
        clear_config_cache()
        # Restore the shared nav/config globals after each test so a
        # mutation here (e.g. Downloads flag flipped True, or a stub
        # marketing page) cannot leak into other tests in the worker.
        self.addCleanup(_reset_shared_nav_state)

    def _get_footer(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        return _footer_html(response.content.decode())

    def test_no_social_urls_renders_only_slack(self):
        html = self._get_footer()
        # Slack is always present; no configured social icons render.
        self.assertIn('data-testid="footer-social-slack"', html)
        self.assertNotIn('data-testid="footer-social-youtube"', html)
        self.assertNotIn('data-testid="footer-social-linkedin"', html)
        self.assertNotIn('data-testid="footer-social-github"', html)
        self.assertNotIn('data-testid="footer-social-x"', html)

    def test_configured_social_urls_render_their_icons(self):
        IntegrationSetting.objects.create(
            key='SOCIAL_YOUTUBE_URL',
            value='https://youtube.com/@aisl',
        )
        IntegrationSetting.objects.create(
            key='SOCIAL_LINKEDIN_URL',
            value='https://linkedin.com/company/aisl',
        )
        clear_config_cache()

        html = self._get_footer()
        self.assertIn('data-testid="footer-social-youtube"', html)
        self.assertIn('href="https://youtube.com/@aisl"', html)
        self.assertIn('data-testid="footer-social-linkedin"', html)
        self.assertIn('href="https://linkedin.com/company/aisl"', html)
        # Unset URLs still do not render.
        self.assertNotIn('data-testid="footer-social-github"', html)
        self.assertNotIn('data-testid="footer-social-x"', html)

    def test_github_icon_uses_indexed_owner_partial(self):
        IntegrationSetting.objects.create(
            key='SOCIAL_GITHUB_URL',
            value='https://github.com/AI-Shipping-Labs',
        )
        clear_config_cache()
        html = self._get_footer()
        self.assertIn('data-testid="footer-social-github"', html)
        # The GitHub icon renders through the indexed owner partial,
        # which stamps ``data-icon="github"`` on its single SVG.
        self.assertEqual(html.count('data-icon="github"'), 1)
