"""E2E coverage for the corrected footer IA (issue #1356).

The sitewide footer renders a brand block plus four honestly-labelled
columns — Community, Resources, About, Legal — a conditional
Downloadables item, the ``marketing_nav`` loops, and a social row whose
icons render only when their URL is configured. ``Manage Subscription``
was removed. Each scenario drives an anonymous or authenticated visitor
and asserts on user-visible footer behaviour.
"""

import pytest

# Local-only: these scenarios seed the DB and inject session cookies and
# integration settings, which cannot run against a deployed environment.
pytestmark = pytest.mark.local_only

from playwright_tests.conftest import (
    auth_context,
    create_user,
    ensure_tiers,
)


def _reset_state():
    from django.db import connection

    from accounts.models import User
    from content.models import Download
    from content.nav_availability import (
        refresh_published_downloads_nav_cache,
    )
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    Download.objects.filter(slug__startswith='foot-1356').delete()
    User.objects.filter(email__endswith='@foot-1356.test').delete()
    IntegrationSetting.objects.filter(
        key__in=[
            'SOCIAL_YOUTUBE_URL',
            'SOCIAL_LINKEDIN_URL',
            'SOCIAL_GITHUB_URL',
            'SOCIAL_X_URL',
            'SLACK_INVITE_URL',
        ]
    ).delete()
    refresh_published_downloads_nav_cache()
    clear_config_cache()
    connection.close()


def _set_config(key, value):
    from django.db import connection

    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    IntegrationSetting.objects.update_or_create(
        key=key, defaults={'value': value}
    )
    clear_config_cache()
    connection.close()


def _seed_published_download(slug='foot-1356-guide'):
    from django.db import connection

    from content.models import Download
    from content.nav_availability import (
        refresh_published_downloads_nav_cache,
    )

    Download.objects.create(
        title='Footer Guide',
        slug=slug,
        file_url='https://example.com/guide.pdf',
        file_type='pdf',
        published=True,
    )
    refresh_published_downloads_nav_cache()
    connection.close()


def _footer(page):
    return page.locator('footer')


@pytest.mark.django_db(transaction=True)
class TestFooterInformationArchitecture:
    def test_visitor_reaches_content_library_from_resources(
        self, django_server, page, django_db_blocker,
    ):
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()

        page.goto(f'{django_server}/', wait_until='domcontentloaded')
        footer = _footer(page)
        footer.get_by_role('link', name='Courses', exact=True).click()
        page.wait_for_url(f'{django_server}/courses**')

        page.goto(f'{django_server}/', wait_until='domcontentloaded')
        _footer(page).get_by_role('link', name='Blog', exact=True).click()
        page.wait_for_url(f'{django_server}/blog**')

    def test_community_column_only_community_destinations(
        self, django_server, page, django_db_blocker,
    ):
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()

        page.goto(f'{django_server}/', wait_until='domcontentloaded')
        # Scope to the Community column via its heading's parent block.
        community = page.locator(
            "footer div:has(> h3:text-is('Community'))"
        )
        for name in [
            'Activities', 'Community Sprints', 'Events',
            'Book Club', 'Join Slack',
        ]:
            assert community.get_by_role(
                'link', name=name, exact=True,
            ).count() == 1
        # Catch-all links are NOT in the Community column.
        for name in [
            'Past Recordings', 'Membership Tiers', 'FAQ', 'Team',
            'Manage Subscription',
        ]:
            assert community.get_by_role(
                'link', name=name, exact=True,
            ).count() == 0

    def test_about_column_groups_company_links(
        self, django_server, page, django_db_blocker,
    ):
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()

        page.goto(f'{django_server}/', wait_until='domcontentloaded')
        about = page.locator("footer div:has(> h3:text-is('About'))")
        about.get_by_role('link', name='Team', exact=True).click()
        page.wait_for_url(f'{django_server}/about**')

        page.goto(f'{django_server}/', wait_until='domcontentloaded')
        page.locator(
            "footer div:has(> h3:text-is('About'))"
        ).get_by_role('link', name='FAQ', exact=True).click()
        page.wait_for_url(f'{django_server}/faq**')

    def test_downloadables_conditional_on_published_downloads(
        self, django_server, page, django_db_blocker,
    ):
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            _seed_published_download()

        page.goto(f'{django_server}/', wait_until='domcontentloaded')
        resources = page.locator(
            "footer div:has(> h3:text-is('Resources'))"
        )
        assert resources.get_by_role(
            'link', name='Downloadables', exact=True,
        ).count() == 1

        with django_db_blocker.unblock():
            _reset_state()  # deletes the download + refreshes nav cache

        page.goto(f'{django_server}/', wait_until='domcontentloaded')
        resources = page.locator(
            "footer div:has(> h3:text-is('Resources'))"
        )
        assert resources.get_by_role(
            'link', name='Downloadables', exact=True,
        ).count() == 0

    def test_manage_subscription_gone_for_paying_member(
        self, django_server, browser, django_db_blocker,
    ):
        email = 'main@foot-1356.test'
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            create_user(email=email, tier_slug='main')

        context = auth_context(browser, email)
        pg = context.new_page()
        try:
            pg.goto(f'{django_server}/', wait_until='domcontentloaded')
            assert _footer(pg).get_by_text(
                'Manage Subscription',
            ).count() == 0
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestFooterSlackJoin:
    def test_main_member_slack_redirects_to_invite(
        self, django_server, browser, django_db_blocker,
    ):
        email = 'main2@foot-1356.test'
        invite = 'https://join.slack.com/t/aisl/shared_invite/xyz'
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            create_user(email=email, tier_slug='main')
            _set_config('SLACK_INVITE_URL', invite)

        context = auth_context(browser, email)
        pg = context.new_page()
        try:
            pg.goto(f'{django_server}/', wait_until='domcontentloaded')
            # The footer Join Slack link points at the gated internal
            # route, never the raw invite URL.
            join = _footer(pg).get_by_role(
                'link', name='Join Slack', exact=True,
            )
            assert join.get_attribute('href') == '/community/slack'
            assert invite not in pg.content()
            # Following that gated route redirects the eligible Main
            # member on to the configured invite (verified via a direct
            # request so the assertion does not depend on Slack rewriting
            # the external URL after the browser leaves the site).
            response = pg.request.get(
                f'{django_server}/community/slack',
                max_redirects=0,
            )
            assert response.status == 302
            assert response.headers['location'] == invite
        finally:
            context.close()

    def test_free_member_slack_lands_on_eligibility_page(
        self, django_server, browser, django_db_blocker,
    ):
        email = 'free@foot-1356.test'
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            create_user(email=email, tier_slug='free')

        context = auth_context(browser, email)
        pg = context.new_page()
        try:
            pg.goto(f'{django_server}/', wait_until='domcontentloaded')
            _footer(pg).get_by_role(
                'link', name='Join Slack', exact=True,
            ).click()
            # Lands on the community/slack route (a real deny page, not a
            # broken link) explaining Main membership is required.
            pg.wait_for_url(f'{django_server}/community/slack**')
            assert 'Main' in pg.content()
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestFooterSocialRow:
    def test_configured_social_urls_render_icons(
        self, django_server, page, django_db_blocker,
    ):
        yt = 'https://youtube.com/@aisl'
        li = 'https://linkedin.com/company/aisl'
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            _set_config('SOCIAL_YOUTUBE_URL', yt)
            _set_config('SOCIAL_LINKEDIN_URL', li)

        page.goto(f'{django_server}/', wait_until='domcontentloaded')
        footer = _footer(page)
        assert footer.locator(
            "[data-testid='footer-social-youtube']",
        ).get_attribute('href') == yt
        assert footer.locator(
            "[data-testid='footer-social-linkedin']",
        ).get_attribute('href') == li
        # Unset URLs do not render.
        assert footer.locator(
            "[data-testid='footer-social-github']",
        ).count() == 0
        assert footer.locator(
            "[data-testid='footer-social-x']",
        ).count() == 0

    def test_social_row_degrades_gracefully_with_nothing_configured(
        self, django_server, page, django_db_blocker,
    ):
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()

        page.goto(f'{django_server}/', wait_until='domcontentloaded')
        footer = _footer(page)
        # Slack is always present.
        assert footer.locator(
            "[data-testid='footer-social-slack']",
        ).count() == 1
        # No configured social icons render, but the row container still
        # exists (not empty/broken) and holds the Slack link.
        row = footer.locator("[data-testid='footer-social-row']")
        assert row.count() == 1
        for platform in ['youtube', 'linkedin', 'github', 'x']:
            assert footer.locator(
                f"[data-testid='footer-social-{platform}']",
            ).count() == 0


@pytest.mark.django_db(transaction=True)
class TestFooterNewsletterByAuthState:
    def test_newsletter_present_anon_absent_authed(
        self, django_server, page, browser, django_db_blocker,
    ):
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()

        # Anonymous: newsletter form present, four columns + copyright.
        page.goto(f'{django_server}/', wait_until='domcontentloaded')
        assert page.locator('form.subscribe-form').count() == 1
        for heading in ['Community', 'Resources', 'About', 'Legal']:
            assert page.get_by_role(
                'heading', name=heading, exact=True,
            ).is_visible()
        assert page.get_by_text('AI Shipping Labs').first.is_visible()

        # Authenticated: newsletter suppressed, columns + copyright remain.
        email = 'authed@foot-1356.test'
        with django_db_blocker.unblock():
            create_user(email=email, tier_slug='free')
        context = auth_context(browser, email)
        pg = context.new_page()
        try:
            pg.goto(f'{django_server}/', wait_until='domcontentloaded')
            assert pg.locator('form.subscribe-form').count() == 0
            for heading in ['Community', 'Resources', 'About', 'Legal']:
                assert pg.get_by_role(
                    'heading', name=heading, exact=True,
                ).is_visible()
        finally:
            context.close()
