"""Tests for Downloadable Resources - issue #77.

Covers:
- Download model fields, defaults, properties (file_type_label, file_type_color,
  human_file_size, increment_download_count)
- GET /downloads listing page: grid with file type badge, human-readable file size
- Tag filtering on /downloads via ?tag=X
- Authorized user download: GET /api/downloads/{slug}/file redirects, increments count
- Unauthorized user with required_level > 0 sees upgrade CTA
- Anonymous user on level-0 download sees email signup form (lead magnet)
- Download endpoint returns 403 for unauthorized users
- Download endpoint returns 401 for anonymous on lead magnet
- Admin can create/edit/delete downloads
- {{download:slug}} shortcode renders inline card
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from content.access import LEVEL_BASIC, LEVEL_OPEN, LEVEL_PREMIUM
from content.models import Download
from tests.fixtures import TierSetupMixin

User = get_user_model()


# --- Model tests ---


class DownloadModelFieldsTest(TestCase):
    """Test Download model fields exist and have correct defaults."""

    def test_create_download_with_all_fields(self):
        dl = Download.objects.create(
            title='AI Cheat Sheet',
            slug='ai-cheat-sheet',
            description='A comprehensive AI cheat sheet',
            file_url='https://example.com/files/ai-cheat-sheet.pdf',
            file_type='pdf',
            file_size_bytes=2_500_000,
            cover_image_url='https://example.com/images/cover.png',
            required_level=LEVEL_OPEN,
            tags=['ai', 'cheat-sheet'],
            download_count=0,
            published=True,
        )
        self.assertEqual(dl.title, 'AI Cheat Sheet')
        self.assertEqual(dl.slug, 'ai-cheat-sheet')
        self.assertEqual(dl.description, 'A comprehensive AI cheat sheet')
        self.assertEqual(dl.file_url, 'https://example.com/files/ai-cheat-sheet.pdf')
        self.assertEqual(dl.file_type, 'pdf')
        self.assertEqual(dl.file_size_bytes, 2_500_000)
        self.assertEqual(dl.cover_image_url, 'https://example.com/images/cover.png')
        self.assertEqual(dl.required_level, 0)
        self.assertEqual(dl.tags, ['ai', 'cheat-sheet'])
        self.assertEqual(dl.download_count, 0)
        self.assertTrue(dl.published)
        self.assertIsNotNone(dl.created_at)

    def test_get_absolute_url(self):
        # Kept: our ``get_absolute_url`` is wired to the URLConf, so a
        # rename of the route would break this assertion. The companion
        # ``test_ordering_by_created_at_desc`` test that exercised
        # ``Meta.ordering`` was removed per
        # ``_docs/testing-guidelines.md`` Rule 3.
        dl = Download(slug='my-download')
        self.assertEqual(dl.get_absolute_url(), '/downloads/my-download')


class DownloadFileTypeLabelTest(TestCase):
    """Test file_type_label property."""

    def test_pdf_label(self):
        dl = Download(file_type='pdf')
        self.assertEqual(dl.file_type_label, 'PDF')

    def test_zip_label(self):
        dl = Download(file_type='zip')
        self.assertEqual(dl.file_type_label, 'ZIP')

    def test_slides_label(self):
        dl = Download(file_type='slides')
        self.assertEqual(dl.file_type_label, 'Slides')

    def test_notebook_label(self):
        dl = Download(file_type='notebook')
        self.assertEqual(dl.file_type_label, 'Notebook')

    def test_csv_label(self):
        dl = Download(file_type='csv')
        self.assertEqual(dl.file_type_label, 'CSV')

    def test_other_label(self):
        dl = Download(file_type='other')
        self.assertEqual(dl.file_type_label, 'Other')


class DownloadFileTypeColorTest(TestCase):
    """Test file_type_color property."""

    def test_pdf_color(self):
        dl = Download(file_type='pdf')
        self.assertIn('red', dl.file_type_color)

    def test_zip_color(self):
        dl = Download(file_type='zip')
        self.assertIn('blue', dl.file_type_color)

    def test_slides_color(self):
        dl = Download(file_type='slides')
        self.assertIn('purple', dl.file_type_color)

    def test_unknown_type_default_color(self):
        dl = Download(file_type='unknown')
        self.assertIn('secondary', dl.file_type_color)


class DownloadHumanFileSizeTest(TestCase):
    """Test human_file_size property."""

    def test_zero_bytes(self):
        dl = Download(file_size_bytes=0)
        self.assertEqual(dl.human_file_size, '')

    def test_bytes(self):
        dl = Download(file_size_bytes=500)
        self.assertEqual(dl.human_file_size, '500 B')

    def test_kilobytes(self):
        dl = Download(file_size_bytes=2048)
        self.assertEqual(dl.human_file_size, '2.0 KB')

    def test_megabytes(self):
        dl = Download(file_size_bytes=2_500_000)
        self.assertEqual(dl.human_file_size, '2.4 MB')

    def test_gigabytes(self):
        dl = Download(file_size_bytes=1_500_000_000)
        self.assertEqual(dl.human_file_size, '1.4 GB')


class DownloadIncrementCountTest(TestCase):
    """Test increment_download_count method."""

    def test_increment_from_zero(self):
        dl = Download.objects.create(
            title='Test', slug='test-count',
            file_url='https://example.com/file.pdf',
        )
        self.assertEqual(dl.download_count, 0)
        dl.increment_download_count()
        self.assertEqual(dl.download_count, 1)

    def test_increment_multiple_times(self):
        dl = Download.objects.create(
            title='Test', slug='test-multi',
            file_url='https://example.com/file.pdf',
        )
        dl.increment_download_count()
        dl.increment_download_count()
        dl.increment_download_count()
        self.assertEqual(dl.download_count, 3)

    def test_increment_is_atomic(self):
        dl = Download.objects.create(
            title='Test', slug='test-atomic',
            file_url='https://example.com/file.pdf',
            download_count=10,
        )
        dl.increment_download_count()
        dl.refresh_from_db()
        self.assertEqual(dl.download_count, 11)


# --- Downloads listing page tests ---


class DownloadsListViewTest(TestCase):
    """Test GET /downloads listing page."""

    def setUp(self):
        self.client = Client()
        self.dl_pdf = Download.objects.create(
            title='AI Cheat Sheet',
            slug='ai-cheat-sheet',
            description='A comprehensive cheat sheet',
            file_url='https://example.com/ai-cheat-sheet.pdf',
            file_type='pdf',
            file_size_bytes=2_500_000,
            tags=['ai', 'reference'],
            published=True,
        )
        self.dl_zip = Download.objects.create(
            title='Starter Kit',
            slug='starter-kit',
            description='Get started quickly',
            file_url='https://example.com/starter-kit.zip',
            file_type='zip',
            file_size_bytes=10_000_000,
            tags=['python', 'starter'],
            published=True,
        )
        self.dl_unpublished = Download.objects.create(
            title='Draft Download',
            slug='draft-download',
            file_url='https://example.com/draft.pdf',
            published=False,
        )

    def test_downloads_page_returns_200(self):
        response = self.client.get('/downloads')
        self.assertEqual(response.status_code, 200)

    def test_downloads_page_uses_correct_template(self):
        response = self.client.get('/downloads')
        self.assertTemplateUsed(response, 'content/downloads_list.html')

    def test_published_downloads_shown(self):
        response = self.client.get('/downloads')
        self.assertContains(response, 'AI Cheat Sheet')
        self.assertContains(response, 'Starter Kit')

    def test_unpublished_downloads_hidden(self):
        response = self.client.get('/downloads')
        self.assertNotContains(response, 'Draft Download')

    def test_file_type_badge_shown(self):
        response = self.client.get('/downloads')
        self.assertContains(response, 'PDF')
        self.assertContains(response, 'ZIP')

    def test_human_file_size_shown(self):
        response = self.client.get('/downloads')
        self.assertContains(response, '2.4 MB')
        self.assertContains(response, '9.5 MB')

    def test_description_shown(self):
        response = self.client.get('/downloads')
        self.assertContains(response, 'A comprehensive cheat sheet')
        self.assertContains(response, 'Get started quickly')

    def test_title_tag(self):
        response = self.client.get('/downloads')
        self.assertContains(response, '<title>Downloads | AI Shipping Labs</title>')


class DownloadsListTagFilterTest(TestCase):
    """Test tag filtering on /downloads via ?tag=X."""

    def setUp(self):
        self.client = Client()
        self.dl_ai = Download.objects.create(
            title='AI Guide',
            slug='ai-guide',
            file_url='https://example.com/ai-guide.pdf',
            tags=['ai', 'python'],
            published=True,
        )
        self.dl_django = Download.objects.create(
            title='Django Templates',
            slug='django-templates',
            file_url='https://example.com/django.zip',
            tags=['django', 'python'],
            published=True,
        )

    def test_no_filter_shows_all(self):
        response = self.client.get('/downloads')
        self.assertContains(response, 'AI Guide')
        self.assertContains(response, 'Django Templates')

    def test_filter_by_ai_tag(self):
        response = self.client.get('/downloads?tag=ai')
        self.assertContains(response, 'AI Guide')
        self.assertNotContains(response, 'Django Templates')

    def test_filter_by_python_tag(self):
        response = self.client.get('/downloads?tag=python')
        self.assertContains(response, 'AI Guide')
        self.assertContains(response, 'Django Templates')

    def test_filter_by_nonexistent_tag(self):
        response = self.client.get('/downloads?tag=nonexistent')
        self.assertNotContains(response, 'AI Guide')
        self.assertNotContains(response, 'Django Templates')

    def test_tag_links_in_listing(self):
        response = self.client.get('/downloads')
        content = response.content.decode()
        self.assertIn('?tag=ai', content)
        self.assertIn('?tag=python', content)
        self.assertIn('?tag=django', content)

    def test_current_tag_in_context(self):
        response = self.client.get('/downloads?tag=ai')
        self.assertEqual(response.context['current_tag'], 'ai')

    def test_empty_tag_ignored(self):
        response = self.client.get('/downloads?tag=')
        self.assertContains(response, 'AI Guide')
        self.assertContains(response, 'Django Templates')


# --- Download access control on listing page ---


class DownloadsListAccessControlTest(TierSetupMixin, TestCase):
    """Test access control display on /downloads listing."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.free_download = Download.objects.create(
            title='Free Resource',
            slug='free-resource',
            file_url='https://example.com/free.pdf',
            required_level=LEVEL_OPEN,
            published=True,
        )
        cls.basic_download = Download.objects.create(
            title='Basic Resource',
            slug='basic-resource',
            file_url='https://example.com/basic.pdf',
            required_level=LEVEL_BASIC,
            published=True,
        )

    def test_anonymous_sees_signup_and_upgrade_ctas(self):
        """Anonymous users see 'Sign Up to Download' for free items and
        'Upgrade to Basic to download' for gated items.

        Per-tier matrix and lock-icon rendering covered by
        `DownloadsGatingTest` and `DownloadsFileEndpointTest` below
        (added in #262 as the Django authoritative coverage after
        playwright_tests/test_downloadable_resources.py was deleted).
        """
        response = self.client.get('/downloads')
        self.assertContains(response, 'Sign Up to Download')
        self.assertContains(response, 'Upgrade to Basic to download')

    def test_basic_user_sees_download_for_basic_resource(self):
        User.objects.create_user(
            email='basic@test.com', password='testpass',
            tier=self.basic_tier,
        )
        self.client.login(email='basic@test.com', password='testpass')
        response = self.client.get('/downloads')
        # Should be able to download the basic resource (URL points at the
        # download API endpoint, not at the public file URL)
        content = response.content.decode()
        self.assertIn('/api/downloads/basic-resource/file', content)


# --- File download endpoint tests ---


class DownloadFileEndpointTest(TierSetupMixin, TestCase):
    """Test GET /api/downloads/{slug}/file endpoint."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.free_download = Download.objects.create(
            title='Free PDF',
            slug='free-pdf',
            file_url='https://example.com/files/free.pdf',
            file_type='pdf',
            required_level=LEVEL_OPEN,
            published=True,
        )
        cls.basic_download = Download.objects.create(
            title='Basic PDF',
            slug='basic-pdf',
            file_url='https://example.com/files/basic.pdf',
            file_type='pdf',
            required_level=LEVEL_BASIC,
            published=True,
        )
        cls.premium_download = Download.objects.create(
            title='Premium PDF',
            slug='premium-pdf',
            file_url='https://example.com/files/premium.pdf',
            file_type='pdf',
            required_level=LEVEL_PREMIUM,
            published=True,
        )

    def test_authenticated_user_can_download_free_resource(self):
        User.objects.create_user(
            email='dl_auth@test.com', password='testpass',
            email_verified=True,
        )
        self.client.login(email='dl_auth@test.com', password='testpass')
        response = self.client.get('/api/downloads/free-pdf/file')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'https://example.com/files/free.pdf')

    def test_download_increments_count(self):
        User.objects.create_user(
            email='dl_count@test.com', password='testpass',
            email_verified=True,
        )
        self.client.login(email='dl_count@test.com', password='testpass')
        self.client.get('/api/downloads/free-pdf/file')
        self.free_download.refresh_from_db()
        self.assertEqual(self.free_download.download_count, 1)

    def test_multiple_downloads_increment_count(self):
        User.objects.create_user(
            email='dl_multi@test.com', password='testpass',
            email_verified=True,
        )
        self.client.login(email='dl_multi@test.com', password='testpass')
        self.client.get('/api/downloads/free-pdf/file')
        self.client.get('/api/downloads/free-pdf/file')
        self.free_download.refresh_from_db()
        self.assertEqual(self.free_download.download_count, 2)

    def test_anonymous_lead_magnet_returns_401(self):
        """Anonymous user on a level-0 download gets 401 with requires_email."""
        response = self.client.get('/api/downloads/free-pdf/file')
        self.assertEqual(response.status_code, 401)
        data = response.json()
        self.assertTrue(data['requires_email'])
        self.assertEqual(data['download_slug'], 'free-pdf')

    def test_unauthorized_user_gets_403(self):
        """User without sufficient tier gets 403."""
        User.objects.create_user(
            email='dl_noauth@test.com', password='testpass',
            tier=self.free_tier,
        )
        self.client.login(email='dl_noauth@test.com', password='testpass')
        response = self.client.get('/api/downloads/basic-pdf/file')
        self.assertEqual(response.status_code, 403)

    def test_basic_user_can_download_basic_resource(self):
        User.objects.create_user(
            email='dl_basic@test.com', password='testpass',
            tier=self.basic_tier,
        )
        self.client.login(email='dl_basic@test.com', password='testpass')
        response = self.client.get('/api/downloads/basic-pdf/file')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'https://example.com/files/basic.pdf')

    def test_basic_user_cannot_download_premium_resource(self):
        User.objects.create_user(
            email='dl_basic2@test.com', password='testpass',
            tier=self.basic_tier,
        )
        self.client.login(email='dl_basic2@test.com', password='testpass')
        response = self.client.get('/api/downloads/premium-pdf/file')
        self.assertEqual(response.status_code, 403)

    def test_premium_user_can_download_premium_resource(self):
        User.objects.create_user(
            email='dl_premium@test.com', password='testpass',
            tier=self.premium_tier,
        )
        self.client.login(email='dl_premium@test.com', password='testpass')
        response = self.client.get('/api/downloads/premium-pdf/file')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'https://example.com/files/premium.pdf')

    def test_nonexistent_download_returns_404(self):
        User.objects.create_user(
            email='dl_404@test.com', password='testpass',
        )
        self.client.login(email='dl_404@test.com', password='testpass')
        response = self.client.get('/api/downloads/nonexistent/file')
        self.assertEqual(response.status_code, 404)

    def test_unpublished_download_returns_404(self):
        Download.objects.create(
            title='Unpublished',
            slug='unpublished-dl',
            file_url='https://example.com/unpublished.pdf',
            published=False,
        )
        User.objects.create_user(
            email='dl_unpub@test.com', password='testpass',
        )
        self.client.login(email='dl_unpub@test.com', password='testpass')
        response = self.client.get('/api/downloads/unpublished-dl/file')
        self.assertEqual(response.status_code, 404)

    def test_post_method_not_allowed(self):
        """Download endpoint only accepts GET."""
        response = self.client.post('/api/downloads/free-pdf/file')
        self.assertEqual(response.status_code, 405)

    def test_anonymous_on_gated_download_gets_403(self):
        """Anonymous user on a gated download (level > 0) gets 403, not 401."""
        response = self.client.get('/api/downloads/basic-pdf/file')
        self.assertEqual(response.status_code, 403)

    def test_403_never_exposes_file_url(self):
        """Verify the file URL is not in the 403 response body."""
        User.objects.create_user(
            email='dl_nourl@test.com', password='testpass',
            tier=self.free_tier,
        )
        self.client.login(email='dl_nourl@test.com', password='testpass')
        response = self.client.get('/api/downloads/premium-pdf/file')
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(
            'https://example.com/files/premium.pdf',
            response.content.decode(),
        )


# --- Admin tests ---


class DownloadAdminTest(TestCase):
    """Test admin CRUD for downloads."""

    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            email='admin@test.com', password='adminpass',
        )
        self.client.login(email='admin@test.com', password='adminpass')

    def test_download_admin_list(self):
        Download.objects.create(
            title='Admin Test',
            slug='admin-test',
            file_url='https://example.com/file.pdf',
        )
        response = self.client.get('/admin/content/download/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Admin Test')

    def test_download_admin_add_page(self):
        response = self.client.get('/admin/content/download/add/')
        self.assertEqual(response.status_code, 200)

    def test_download_admin_create(self):
        response = self.client.post('/admin/content/download/add/', {
            'title': 'New Download',
            'slug': 'new-download',
            'description': 'A new download',
            'file_url': 'https://example.com/new.pdf',
            'file_type': 'pdf',
            'file_size_bytes': 1000,
            'cover_image_url': '',
            'required_level': 0,
            'tags': '[]',
            'published': True,
        })
        # Should redirect to changelist on success
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Download.objects.filter(slug='new-download').exists())

    def test_download_admin_edit(self):
        dl = Download.objects.create(
            title='Edit Test',
            slug='edit-test',
            file_url='https://example.com/edit.pdf',
        )
        response = self.client.get(f'/admin/content/download/{dl.pk}/change/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Edit Test')

    def test_download_admin_delete(self):
        dl = Download.objects.create(
            title='Delete Test',
            slug='delete-test',
            file_url='https://example.com/delete.pdf',
        )
        response = self.client.post(
            f'/admin/content/download/{dl.pk}/delete/',
            {'post': 'yes'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Download.objects.filter(slug='delete-test').exists())


# --- Download shortcode template tag tests ---


class DownloadShortcodeTest(TierSetupMixin, TestCase):
    """Test {{download:slug}} shortcode rendering via template tag."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.dl = Download.objects.create(
            title='Shortcode PDF',
            slug='shortcode-pdf',
            description='A shortcode test PDF',
            file_url='https://example.com/shortcode.pdf',
            file_type='pdf',
            file_size_bytes=500_000,
            required_level=LEVEL_OPEN,
            published=True,
        )
        cls.gated_dl = Download.objects.create(
            title='Gated Shortcode',
            slug='gated-shortcode',
            description='A gated download',
            file_url='https://example.com/gated.pdf',
            file_type='pdf',
            required_level=LEVEL_BASIC,
            published=True,
        )

    def _render_shortcode(self, shortcode_text, user=None):
        """Helper: render shortcode using the template tag filter."""
        from django.test import RequestFactory

        from content.templatetags.download_tags import render_download_shortcodes

        factory = RequestFactory()
        request = factory.get('/')
        if user:
            request.user = user
        else:
            from django.contrib.auth.models import AnonymousUser
            request.user = AnonymousUser()

        return render_download_shortcodes(shortcode_text, request)

    def test_shortcode_renders_title(self):
        html = self._render_shortcode('{{download:shortcode-pdf}}')
        self.assertIn('Shortcode PDF', html)

    def test_shortcode_renders_description(self):
        html = self._render_shortcode('{{download:shortcode-pdf}}')
        self.assertIn('A shortcode test PDF', html)

    def test_shortcode_renders_file_type_badge(self):
        html = self._render_shortcode('{{download:shortcode-pdf}}')
        self.assertIn('PDF', html)

    def test_shortcode_anonymous_shows_signup_for_free(self):
        html = self._render_shortcode('{{download:shortcode-pdf}}')
        self.assertIn('Sign Up to Download', html)

    def test_shortcode_authenticated_shows_download_button(self):
        user = User.objects.create_user(
            email='sc_auth@test.com', password='testpass',
            email_verified=True,
        )
        html = self._render_shortcode('{{download:shortcode-pdf}}', user=user)
        self.assertIn('/api/downloads/shortcode-pdf/file', html)

    def test_shortcode_gated_shows_upgrade_cta(self):
        user = User.objects.create_user(
            email='sc_free@test.com', password='testpass',
            tier=self.free_tier,
        )
        html = self._render_shortcode('{{download:gated-shortcode}}', user=user)
        self.assertIn('Upgrade to Basic to download', html)

    def test_shortcode_authorized_user_for_gated(self):
        user = User.objects.create_user(
            email='sc_basic@test.com', password='testpass',
            tier=self.basic_tier,
        )
        html = self._render_shortcode('{{download:gated-shortcode}}', user=user)
        self.assertIn('/api/downloads/gated-shortcode/file', html)

    def test_shortcode_nonexistent_slug_left_as_is(self):
        result = self._render_shortcode('{{download:does-not-exist}}')
        self.assertEqual(result, '{{download:does-not-exist}}')

    def test_shortcode_with_whitespace(self):
        html = self._render_shortcode('{{ download : shortcode-pdf }}')
        self.assertIn('Shortcode PDF', html)

    def test_shortcode_embedded_in_html(self):
        html_content = '<p>Check out this resource:</p>{{download:shortcode-pdf}}<p>More content</p>'
        result = self._render_shortcode(html_content)
        self.assertIn('Check out this resource', result)
        self.assertIn('Shortcode PDF', result)
        self.assertIn('More content', result)

    def test_empty_content_returns_empty(self):
        result = self._render_shortcode('')
        self.assertEqual(result, '')

    def test_none_content_returns_none(self):
        result = self._render_shortcode(None)
        self.assertIsNone(result)


# --- URL routing tests ---


class DownloadURLTest(TestCase):
    """Test download URL patterns exist and resolve."""

    def test_downloads_list_url(self):
        response = self.client.get('/downloads')
        self.assertEqual(response.status_code, 200)

    def test_download_file_url_pattern(self):
        Download.objects.create(
            title='URL Test',
            slug='url-test',
            file_url='https://example.com/file.pdf',
            published=True,
        )
        # Anonymous user on free download gets 401 (lead magnet)
        response = self.client.get('/api/downloads/url-test/file')
        self.assertEqual(response.status_code, 401)


# --- Conversions from playwright_tests/test_seo_tags.py (issue #256) ---


class DownloadsTagFilterTest(TestCase):
    """Behaviour previously covered by Playwright Scenario 6 on
    /downloads. Filtering happens via ?tag= and resolves server-side.
    """

    def test_tag_filter_on_downloads(self):
        # Replaces playwright_tests/test_seo_tags.py::TestScenario6TagFiltersAcrossPages::test_tag_filter_on_downloads
        Download.objects.create(
            title='Python Cheatsheet', slug='python-cheatsheet',
            file_url='https://example.com/python.pdf',
            tags=['python'], published=True,
        )
        Download.objects.create(
            title='Go Cheatsheet', slug='go-cheatsheet',
            file_url='https://example.com/go.pdf',
            tags=['go'], published=True,
        )

        # Listing exposes the python chip whose href triggers the filter.
        listing = self.client.get('/downloads')
        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, '?tag=python')

        # Following ?tag=python: only the python download remains.
        filtered = self.client.get('/downloads?tag=python')
        self.assertEqual(filtered.status_code, 200)
        self.assertContains(filtered, 'Python Cheatsheet')
        self.assertNotContains(filtered, 'Go Cheatsheet')


# ---------------------------------------------------------------
# Conversions from playwright_tests/test_downloadable_resources.py
# (issue #262 — workstream 3 sub-issue of #170)
# ---------------------------------------------------------------


class DownloadsListDisplayTest(TestCase):
    """Anonymous visitor browses the catalog and evaluates resources by
    type, size, description.

    Replaces the Playwright scenario where two downloads of different
    file types are listed on /downloads with badges, sizes, and copy.
    """

    def test_downloads_catalog_shows_type_badges_sizes_descriptions(self):
        # Replaces playwright_tests/test_downloadable_resources.py::TestScenario1VisitorBrowsesCatalog::test_downloads_catalog_shows_type_badges_sizes_descriptions
        Download.objects.create(
            title='AI Cheat Sheet',
            slug='ai-cheat-sheet',
            description='A comprehensive cheat sheet for AI concepts.',
            file_url='https://example.com/cheatsheet.pdf',
            file_type='pdf',
            file_size_bytes=2_500_000,  # -> "2.4 MB"
            tags=['ai', 'reference'],
            published=True,
        )
        Download.objects.create(
            title='Starter Kit',
            slug='starter-kit',
            description='Everything you need to get started.',
            file_url='https://example.com/starter.zip',
            file_type='zip',
            file_size_bytes=9_961_472,  # -> "9.5 MB"
            tags=['starter'],
            published=True,
        )

        response = self.client.get('/downloads')
        self.assertEqual(response.status_code, 200)

        # Both card titles render.
        self.assertContains(response, 'AI Cheat Sheet')
        self.assertContains(response, 'Starter Kit')

        # File-type badges (PDF and ZIP).
        self.assertContains(response, 'PDF')
        self.assertContains(response, 'ZIP')

        # Human-readable sizes from human_file_size.
        self.assertContains(response, '2.4 MB')
        self.assertContains(response, '9.5 MB')

        # Descriptions render.
        self.assertContains(response, 'comprehensive cheat sheet for AI concepts')
        self.assertContains(response, 'Everything you need to get started')


class DownloadsGatingTest(TierSetupMixin, TestCase):
    """Anonymous-visitor gating CTAs on the /downloads listing.

    Replaces the Playwright scenarios for lead-magnet signup and
    upgrade CTA on a gated download.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.lead_magnet = Download.objects.create(
            title='Free PDF Guide',
            slug='free-pdf-guide',
            description='A free guide for everyone.',
            file_url='https://example.com/guide.pdf',
            file_type='pdf',
            required_level=LEVEL_OPEN,
            published=True,
        )
        cls.gated = Download.objects.create(
            title='Basic Toolkit',
            slug='basic-toolkit',
            description='A toolkit for Basic members.',
            file_url='https://example.com/toolkit.zip',
            file_type='zip',
            required_level=LEVEL_BASIC,
            published=True,
        )

    def test_anonymous_sees_signup_for_lead_magnet(self):
        # Replaces playwright_tests/test_downloadable_resources.py::TestScenario2AnonymousLeadMagnetSignup::test_anonymous_sees_signup_button_for_free_download
        response = self.client.get('/downloads')
        self.assertEqual(response.status_code, 200)

        # Card is present and the signup CTA links the visitor to
        # /accounts/signup with a `next` parameter pointing at the
        # gated file endpoint.
        self.assertContains(response, 'Free PDF Guide')
        self.assertContains(
            response,
            'href="/accounts/signup?next=/api/downloads/free-pdf-guide/file"',
        )
        self.assertContains(response, 'Sign Up to Download')

    def test_anonymous_sees_upgrade_cta_for_gated(self):
        # Replaces playwright_tests/test_downloadable_resources.py::TestScenario3AnonymousGatedDownloadUpgradeCTA::test_anonymous_sees_upgrade_cta_for_gated_download
        response = self.client.get('/downloads')
        self.assertEqual(response.status_code, 200)

        # Upgrade CTA is shown with a /pricing link.
        self.assertContains(response, 'Upgrade to Basic to download')
        self.assertContains(response, 'href="/pricing"')

        # The gated file endpoint must not be exposed as a download link.
        body = response.content.decode()
        self.assertNotIn(
            'href="/api/downloads/basic-toolkit/file"',
            body,
        )


class DownloadsFileEndpointTest(TierSetupMixin, TestCase):
    """File-endpoint behaviour previously verified end-to-end via
    Playwright by intercepting redirects.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.basic_resource = Download.objects.create(
            title='Member Resource',
            slug='member-resource',
            description='A resource for members.',
            file_url='https://example.com/member.pdf',
            file_type='pdf',
            required_level=LEVEL_BASIC,
            published=True,
        )
        cls.premium_resource = Download.objects.create(
            title='Premium Report',
            slug='premium-report',
            description='An exclusive premium report.',
            file_url='https://example.com/secret.pdf',
            file_type='pdf',
            required_level=LEVEL_PREMIUM,
            published=True,
        )

    def test_basic_member_gets_file_and_count_increments(self):
        # Replaces playwright_tests/test_downloadable_resources.py::TestScenario4AuthorizedMemberDownloads::test_basic_member_downloads_file_and_count_increments
        User.objects.create_user(
            email='basic_dl@test.com', password='testpass',
            tier=self.basic_tier,
        )
        self.client.login(email='basic_dl@test.com', password='testpass')

        # Listing exposes the direct download link (no upgrade CTA).
        listing = self.client.get('/downloads')
        self.assertContains(
            listing,
            'href="/api/downloads/member-resource/file"',
        )

        # Capture before/after to verify the side effect, not just ">0".
        initial_count = Download.objects.get(slug='member-resource').download_count

        response = self.client.get(
            '/api/downloads/member-resource/file', follow=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'https://example.com/member.pdf')

        final_count = Download.objects.get(slug='member-resource').download_count
        self.assertEqual(final_count, initial_count + 1)

    def test_basic_member_403_on_premium_and_count_unchanged(self):
        # Replaces playwright_tests/test_downloadable_resources.py::TestScenario5InsufficientTierUpgradeCTA::test_basic_member_cannot_access_premium_download
        User.objects.create_user(
            email='basic_p@test.com', password='testpass',
            tier=self.basic_tier,
        )
        self.client.login(email='basic_p@test.com', password='testpass')

        # Listing shows the upgrade CTA, never the file URL.
        listing = self.client.get('/downloads')
        self.assertContains(listing, 'Upgrade to Premium to download')
        self.assertContains(listing, 'href="/pricing"')
        body = listing.content.decode()
        self.assertNotIn('https://example.com/secret.pdf', body)
        self.assertNotIn(
            'href="/api/downloads/premium-report/file"',
            body,
        )

        # Endpoint refuses with 403 and download_count is not bumped
        # (Rule 12: assert side-effect is unchanged, not just status).
        initial_count = Download.objects.get(slug='premium-report').download_count
        response = self.client.get(
            '/api/downloads/premium-report/file', follow=False,
        )
        self.assertEqual(response.status_code, 403)
        # The forbidden response must not leak the source file URL.
        self.assertNotIn(
            'https://example.com/secret.pdf',
            response.content.decode(),
        )
        final_count = Download.objects.get(slug='premium-report').download_count
        self.assertEqual(final_count, initial_count)


class DownloadsListFilteringTest(TestCase):
    """Tag chip filtering on /downloads + empty-state recovery link.

    Replaces the Playwright scenarios that drove these flows through
    the browser even though they are pure server-rendered behaviour.
    """

    def test_tag_filter_narrows_results(self):
        # Replaces playwright_tests/test_downloadable_resources.py::TestScenario6VisitorFiltersByTag::test_tag_filter_narrows_to_matching_downloads
        Download.objects.create(
            title='Doc A', slug='doc-a',
            description='A document about Python and AI.',
            file_url='https://example.com/doc-a.pdf',
            file_type='pdf',
            tags=['python', 'ai'],
            published=True,
        )
        Download.objects.create(
            title='Doc B', slug='doc-b',
            description='A document about Django.',
            file_url='https://example.com/doc-b.pdf',
            file_type='pdf',
            tags=['django'],
            published=True,
        )

        # Without filter, both cards are visible.
        unfiltered = self.client.get('/downloads')
        self.assertContains(unfiltered, 'Doc A')
        self.assertContains(unfiltered, 'Doc B')

        # Filtering by ?tag=python keeps Doc A and drops Doc B.
        filtered = self.client.get('/downloads?tag=python')
        self.assertEqual(filtered.status_code, 200)
        self.assertContains(filtered, 'Doc A')
        self.assertNotContains(filtered, 'Doc B')
        # current_tag context is what the chip UI uses to highlight.
        self.assertEqual(filtered.context['current_tag'], 'python')

    def test_empty_state_with_clear_filter_link(self):
        # Replaces playwright_tests/test_downloadable_resources.py::TestScenario7EmptyTagFilter::test_nonexistent_tag_shows_empty_message_and_recovery_link
        # No downloads tagged "nonexistent" exist.
        response = self.client.get('/downloads?tag=nonexistent')
        self.assertEqual(response.status_code, 200)

        # Empty-state copy + recovery link to /downloads (no <article>
        # cards are rendered when the queryset is empty).
        self.assertContains(response, 'No downloads found with the selected tags.')
        self.assertContains(response, 'href="/downloads"')
        self.assertContains(response, 'View all downloads')
        # No download cards in the response (cards use <article>).
        self.assertNotContains(response, '<article')


def _create_article_with_shortcode(slug, shortcode_html):
    """Helper: create a published Article whose content_html embeds a
    download shortcode.

    The Article ``save()`` only re-renders ``content_html`` when
    ``content_markdown`` is non-empty, so we can pass HTML directly here
    and trust it to survive the round-trip into the blog detail page.
    """
    import datetime

    from content.models import Article

    return Article.objects.create(
        title='Article With Download',
        slug=slug,
        description='An article containing a download shortcode.',
        content_markdown='',
        content_html=shortcode_html,
        date=datetime.date.today(),
        published=True,
    )


class DownloadShortcodeRenderingTest(TierSetupMixin, TestCase):
    """End-to-end shortcode rendering through the blog detail view.

    The shortcode is rewritten to the inline download card by the
    ``render_download_shortcodes`` filter in the blog_detail template,
    so going through ``/blog/<slug>`` exercises both the template tag
    and the includes/download_card.html template — same surface the
    Playwright tests covered, with no JavaScript involved.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.lead_magnet = Download.objects.create(
            title='Inline Resource',
            slug='inline-pdf',
            description='Get it here',
            file_url='https://example.com/inline.pdf',
            file_type='pdf',
            required_level=LEVEL_OPEN,
            published=True,
        )
        cls.gated = Download.objects.create(
            title='Gated Slides',
            slug='gated-slides',
            description='Slides for Basic members.',
            file_url='https://example.com/slides.pdf',
            file_type='slides',
            required_level=LEVEL_BASIC,
            published=True,
        )

    def test_anonymous_inline_card(self):
        # Replaces playwright_tests/test_downloadable_resources.py::TestScenario8ShortcodeAnonymousReader::test_anonymous_sees_inline_card_with_signup_button
        _create_article_with_shortcode(
            'article-with-download',
            '<p>Here is some intro text.</p>'
            '{{download:inline-pdf}}'
            '<p>More content after.</p>',
        )

        response = self.client.get('/blog/article-with-download')
        self.assertEqual(response.status_code, 200)

        # Inline card renders title, description, file-type badge.
        self.assertContains(response, 'Inline Resource')
        self.assertContains(response, 'Get it here')
        self.assertContains(response, 'PDF')

        # Lead-magnet signup button with `next` pointing at the file
        # endpoint — this is the only allowed CTA for anonymous users.
        self.assertContains(
            response,
            'href="/accounts/signup?next=/api/downloads/inline-pdf/file"',
        )
        self.assertContains(response, 'Sign Up to Download Free')

        # The direct download link must NOT be exposed to anonymous.
        body = response.content.decode()
        self.assertNotIn(
            'href="/api/downloads/inline-pdf/file"',
            body,
        )

    def test_authenticated_inline_card(self):
        # Replaces playwright_tests/test_downloadable_resources.py::TestScenario9AuthenticatedShortcodeDownload::test_authenticated_user_sees_direct_download_link
        User.objects.create_user(
            email='free_sc@test.com', password='testpass',
            tier=self.free_tier,
            email_verified=True,
        )
        self.client.login(email='free_sc@test.com', password='testpass')

        _create_article_with_shortcode(
            'article-with-download',
            '<p>Intro text.</p>'
            '{{download:inline-pdf}}'
            '<p>More content.</p>',
        )

        response = self.client.get('/blog/article-with-download')
        self.assertEqual(response.status_code, 200)

        # Direct download link is exposed to the logged-in reader.
        self.assertContains(
            response,
            'href="/api/downloads/inline-pdf/file"',
        )
        self.assertContains(response, 'Download PDF')

        # The lead-magnet signup CTA is gone for authenticated users.
        self.assertNotContains(response, 'Sign Up to Download Free')

    def test_free_user_sees_upgrade_cta(self):
        # Replaces playwright_tests/test_downloadable_resources.py::TestScenario10FreeUserGatedShortcode::test_free_user_sees_upgrade_cta_in_shortcode_card
        User.objects.create_user(
            email='free_gated@test.com', password='testpass',
            tier=self.free_tier,
            email_verified=True,
        )
        self.client.login(email='free_gated@test.com', password='testpass')

        _create_article_with_shortcode(
            'article-gated-download',
            '<p>Check out these slides.</p>'
            '{{download:gated-slides}}',
        )

        response = self.client.get('/blog/article-gated-download')
        self.assertEqual(response.status_code, 200)

        # The card shows the upgrade CTA and a /pricing link, never the
        # file endpoint.
        self.assertContains(response, 'Gated Slides')
        self.assertContains(response, 'Upgrade to Basic to download')
        self.assertContains(response, 'href="/pricing"')

        body = response.content.decode()
        self.assertNotIn(
            'href="/api/downloads/gated-slides/file"',
            body,
        )


class DownloadsPubliclyVisibleAfterCreateTest(TestCase):
    """The Studio create form was removed (#152) — but downloads created
    via other paths (sync, ORM, admin) must still surface on /downloads.

    The "studio create URL is gone" half of the original Playwright
    scenario lives in studio.tests.test_downloads.StudioDownloadCreateRemovedTest;
    here we cover the public-listing half so the original behaviour is
    not lost when the file is deleted.
    """

    def test_download_created_via_orm_appears_publicly(self):
        # Replaces playwright_tests/test_downloadable_resources.py::TestScenario11StaffCreatesDownloadViaStudio::test_download_create_url_removed_and_download_visible_publicly
        Download.objects.create(
            title='Test Resource',
            slug='test-resource',
            file_url='https://example.com/test.pdf',
            file_type='pdf',
            required_level=LEVEL_OPEN,
            published=True,
        )

        response = self.client.get('/downloads')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Test Resource')
