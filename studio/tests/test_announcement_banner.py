"""Tests for the Studio announcement banner editor and public rendering."""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from integrations.middleware import clear_announcement_banner_cache
from integrations.models import AnnouncementBanner

User = get_user_model()


class StudioAnnouncementAccessTest(TestCase):
    """Only staff users can reach the editor."""

    def setUp(self):
        self.client = Client()
        clear_announcement_banner_cache()

    def tearDown(self):
        clear_announcement_banner_cache()

    def test_anonymous_redirected_to_login(self):
        response = self.client.get('/studio/announcement/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_non_staff_gets_403(self):
        User.objects.create_user(email='user@test.com', password='testpass', is_staff=False)
        self.client.login(email='user@test.com', password='testpass')
        response = self.client.get('/studio/announcement/')
        self.assertEqual(response.status_code, 403)


class StudioAnnouncementGetTest(TestCase):
    """GET creates the singleton and renders the form pre-filled."""

    def setUp(self):
        self.client = Client()
        User.objects.create_user(email='staff@test.com', password='testpass', is_staff=True)
        self.client.login(email='staff@test.com', password='testpass')
        AnnouncementBanner.objects.all().delete()
        clear_announcement_banner_cache()

    def tearDown(self):
        clear_announcement_banner_cache()

    def test_get_returns_200_and_uses_template(self):
        response = self.client.get('/studio/announcement/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'studio/announcement/edit.html')

    def test_get_creates_singleton_row(self):
        self.assertEqual(AnnouncementBanner.objects.count(), 0)
        self.client.get('/studio/announcement/')
        self.assertEqual(AnnouncementBanner.objects.count(), 1)
        banner = AnnouncementBanner.objects.get()
        self.assertEqual(banner.pk, 1)
        self.assertFalse(banner.is_enabled)
        self.assertTrue(banner.is_dismissible)

    def test_get_prefills_existing_values(self):
        banner = AnnouncementBanner.get_singleton()
        banner.message = 'Existing message'
        banner.link_url = '/some-link'
        banner.save()
        response = self.client.get('/studio/announcement/')
        self.assertContains(response, 'Existing message')
        self.assertContains(response, '/some-link')

    def test_get_shows_version_and_updated_at(self):
        banner = AnnouncementBanner.get_singleton()
        banner.message = 'm'
        banner.version = 7
        banner.save()
        response = self.client.get('/studio/announcement/')
        self.assertContains(response, 'Current version:')
        self.assertContains(response, '7')
        self.assertContains(response, 'Last updated:')


class StudioAnnouncementPostTest(TestCase):
    """POST saves the banner, bumps version on text change, and clears cache."""

    def setUp(self):
        self.client = Client()
        User.objects.create_user(email='staff@test.com', password='testpass', is_staff=True)
        self.client.login(email='staff@test.com', password='testpass')
        AnnouncementBanner.objects.all().delete()
        clear_announcement_banner_cache()

    def tearDown(self):
        clear_announcement_banner_cache()

    def test_post_saves_and_redirects(self):
        response = self.client.post('/studio/announcement/', {
            'message': 'Spring cohort closes Friday',
            'link_url': '/courses',
            'link_label': 'Reserve your seat',
            'is_enabled': 'on',
            'is_dismissible': 'on',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/studio/announcement/')

        banner = AnnouncementBanner.objects.get(pk=1)
        self.assertEqual(banner.message, 'Spring cohort closes Friday')
        self.assertEqual(banner.link_url, '/courses')
        self.assertEqual(banner.link_label, 'Reserve your seat')
        self.assertTrue(banner.is_enabled)
        self.assertTrue(banner.is_dismissible)

    def test_post_without_message_shows_error(self):
        response = self.client.post('/studio/announcement/', {
            'message': '',
            'link_url': '/x',
            'is_enabled': 'on',
            'is_dismissible': 'on',
        })
        self.assertEqual(response.status_code, 200)
        # Banner should not have been updated.
        banner = AnnouncementBanner.objects.get(pk=1)
        self.assertEqual(banner.message, '')

    def test_post_unchecked_booleans_become_false(self):
        # Pre-create with both checks on.
        banner = AnnouncementBanner.get_singleton()
        banner.message = 'x'
        banner.is_enabled = True
        banner.is_dismissible = True
        banner.save()

        # Submit without the checkboxes.
        self.client.post('/studio/announcement/', {
            'message': 'still here',
        })
        banner.refresh_from_db()
        self.assertFalse(banner.is_enabled)
        self.assertFalse(banner.is_dismissible)

    def test_post_does_not_create_extra_rows(self):
        for _ in range(3):
            self.client.post('/studio/announcement/', {
                'message': 'one row only',
                'is_enabled': 'on',
                'is_dismissible': 'on',
            })
        self.assertEqual(AnnouncementBanner.objects.count(), 1)


class StudioAnnouncementVersionBumpTest(TestCase):
    """Version is bumped only when message or link_url changes."""

    def setUp(self):
        self.client = Client()
        User.objects.create_user(email='staff@test.com', password='testpass', is_staff=True)
        self.client.login(email='staff@test.com', password='testpass')
        AnnouncementBanner.objects.all().delete()
        clear_announcement_banner_cache()

        self.client.post('/studio/announcement/', {
            'message': 'Original',
            'link_url': '/old',
            'link_label': 'Read more',
            'is_enabled': 'on',
            'is_dismissible': 'on',
        })
        self.banner = AnnouncementBanner.objects.get(pk=1)
        self.starting_version = self.banner.version

    def tearDown(self):
        clear_announcement_banner_cache()

    def test_version_bumps_when_message_changes(self):
        self.client.post('/studio/announcement/', {
            'message': 'Changed message',
            'link_url': '/old',
            'link_label': 'Read more',
            'is_enabled': 'on',
            'is_dismissible': 'on',
        })
        self.banner.refresh_from_db()
        self.assertEqual(self.banner.version, self.starting_version + 1)

    def test_version_bumps_when_link_url_changes(self):
        self.client.post('/studio/announcement/', {
            'message': 'Original',
            'link_url': '/new-link',
            'link_label': 'Read more',
            'is_enabled': 'on',
            'is_dismissible': 'on',
        })
        self.banner.refresh_from_db()
        self.assertEqual(self.banner.version, self.starting_version + 1)

    def test_version_does_not_bump_for_label_or_flag_changes(self):
        self.client.post('/studio/announcement/', {
            'message': 'Original',
            'link_url': '/old',
            'link_label': 'Different label',
            'is_enabled': 'on',
            'is_dismissible': '',  # toggled off
        })
        self.banner.refresh_from_db()
        self.assertEqual(self.banner.version, self.starting_version)
        self.assertEqual(self.banner.link_label, 'Different label')
        self.assertFalse(self.banner.is_dismissible)


class StudioAnnouncementCacheClearedOnSaveTest(TestCase):
    """After saving via the studio view, the public cache reflects the change."""

    def setUp(self):
        self.client = Client()
        User.objects.create_user(email='staff@test.com', password='testpass', is_staff=True)
        self.client.login(email='staff@test.com', password='testpass')
        AnnouncementBanner.objects.all().delete()
        clear_announcement_banner_cache()

    def tearDown(self):
        clear_announcement_banner_cache()

    def test_save_invalidates_in_process_cache(self):
        from integrations.middleware import get_announcement_banner

        # Prime cache while no row exists.
        self.assertIsNone(get_announcement_banner())

        self.client.post('/studio/announcement/', {
            'message': 'Now visible',
            'link_url': '',
            'link_label': 'Read more',
            'is_enabled': 'on',
            'is_dismissible': 'on',
        })

        cached = get_announcement_banner()
        self.assertIsNotNone(cached)
        self.assertEqual(cached.message, 'Now visible')


class StudioAnnouncementSidebarTest(TestCase):
    """The Studio sidebar should expose the Announcement entry."""

    def setUp(self):
        self.client = Client()
        User.objects.create_user(email='staff@test.com', password='testpass', is_staff=True)
        self.client.login(email='staff@test.com', password='testpass')

    def test_sidebar_contains_announcement_link(self):
        response = self.client.get('/studio/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="/studio/announcement/"')
        self.assertContains(response, '>Announcement<')


class PublicHeaderBannerRenderingTest(TestCase):
    """Public-page rendering of the announcement banner via the header partial."""

    def setUp(self):
        self.client = Client()
        AnnouncementBanner.objects.all().delete()
        clear_announcement_banner_cache()

    def tearDown(self):
        clear_announcement_banner_cache()

    def _enable_banner(self, **kwargs):
        defaults = {
            'message': 'Default banner copy',
            'link_url': '',
            'link_label': 'Read more',
            'is_enabled': True,
            'is_dismissible': True,
        }
        defaults.update(kwargs)
        banner = AnnouncementBanner.get_singleton()
        for k, v in defaults.items():
            setattr(banner, k, v)
        banner.save()
        clear_announcement_banner_cache()
        return banner

    def test_banner_appears_on_public_homepage_when_enabled(self):
        self._enable_banner(message='Cohort opens soon', link_url='/courses')
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Cohort opens soon')
        self.assertContains(response, 'data-testid="announcement-banner"')

    def test_banner_hidden_when_disabled(self):
        self._enable_banner(message='Stealth', is_enabled=False)
        response = self.client.get('/')
        self.assertNotContains(response, 'data-testid="announcement-banner"')
        self.assertNotContains(response, 'Stealth')

    def test_banner_hidden_when_no_row(self):
        AnnouncementBanner.objects.all().delete()
        clear_announcement_banner_cache()
        response = self.client.get('/')
        self.assertNotContains(response, 'data-testid="announcement-banner"')

    def test_banner_with_link_renders_anchor_and_label(self):
        self._enable_banner(message='Click me', link_url='/events/launch', link_label='Read more')
        response = self.client.get('/')
        self.assertContains(response, 'href="/events/launch"')
        self.assertContains(response, 'Read more')

    def test_banner_without_link_omits_label(self):
        self._enable_banner(message='Plain text only', link_url='', link_label='Read more')
        response = self.client.get('/')
        # Banner is rendered.
        self.assertContains(response, 'Plain text only')
        # No anchor element wrapping the banner (rendered as a div instead).
        # We verify by checking that the data-testid block is on a div, not an a.
        self.assertContains(response, '<div class="announcement-banner')
        self.assertNotContains(response, '>Read more</span>')

    def test_dismissible_banner_includes_close_button(self):
        self._enable_banner(message='X me', is_dismissible=True)
        response = self.client.get('/')
        self.assertContains(response, 'id="announcement-banner-close"')

    def test_non_dismissible_banner_omits_close_button(self):
        self._enable_banner(message='No X', is_dismissible=False)
        response = self.client.get('/')
        self.assertContains(response, 'No X')
        self.assertNotContains(response, 'id="announcement-banner-close"')

    def test_banner_not_rendered_in_studio(self):
        self._enable_banner(message='Studio-hidden')
        # Have to be staff to hit /studio/.
        User.objects.create_user(email='s@test.com', password='testpass', is_staff=True)
        self.client.login(email='s@test.com', password='testpass')
        response = self.client.get('/studio/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="announcement-banner"')
        self.assertNotContains(response, 'Studio-hidden')

    def test_cookie_name_uses_current_version(self):
        banner = self._enable_banner(message='cookie-name', is_dismissible=True)
        response = self.client.get('/')
        self.assertContains(response, f'data-version="{banner.version}"')
