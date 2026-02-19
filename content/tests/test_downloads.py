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
from django.test import TestCase, Client

from content.access import LEVEL_OPEN, LEVEL_BASIC, LEVEL_MAIN, LEVEL_PREMIUM
from content.models import Download
from payments.models import Tier

User = get_user_model()


class TierSetupMixin:
    """Mixin that creates the four standard tiers."""

    @classmethod
    def setUpTestData(cls):
        cls.free_tier, _ = Tier.objects.get_or_create(
            slug='free', defaults={'name': 'Free', 'level': 0},
        )
        cls.basic_tier, _ = Tier.objects.get_or_create(
            slug='basic', defaults={'name': 'Basic', 'level': 10},
        )
        cls.main_tier, _ = Tier.objects.get_or_create(
            slug='main', defaults={'name': 'Main', 'level': 20},
        )
        cls.premium_tier, _ = Tier.objects.get_or_create(
            slug='premium', defaults={'name': 'Premium', 'level': 30},
        )


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

    def test_default_values(self):
        dl = Download.objects.create(
            title='Minimal',
            slug='minimal',
            file_url='https://example.com/file.pdf',
        )
        self.assertEqual(dl.description, '')
        self.assertEqual(dl.file_type, 'pdf')
        self.assertEqual(dl.file_size_bytes, 0)
        self.assertEqual(dl.cover_image_url, '')
        self.assertEqual(dl.required_level, 0)
        self.assertEqual(dl.tags, [])
        self.assertEqual(dl.download_count, 0)
        self.assertTrue(dl.published)

    def test_slug_unique(self):
        from django.db import IntegrityError
        Download.objects.create(
            title='First', slug='unique-slug',
            file_url='https://example.com/file.pdf',
        )
        with self.assertRaises(IntegrityError):
            Download.objects.create(
                title='Second', slug='unique-slug',
                file_url='https://example.com/file2.pdf',
            )

    def test_ordering_by_created_at_desc(self):
        dl1 = Download.objects.create(
            title='Old', slug='old',
            file_url='https://example.com/old.pdf',
        )
        dl2 = Download.objects.create(
            title='New', slug='new',
            file_url='https://example.com/new.pdf',
        )
        downloads = list(Download.objects.all())
        self.assertEqual(downloads[0].slug, 'new')
        self.assertEqual(downloads[1].slug, 'old')

    def test_str(self):
        dl = Download(title='My Download')
        self.assertEqual(str(dl), 'My Download')

    def test_get_absolute_url(self):
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

    def test_clear_filter_link(self):
        response = self.client.get('/downloads?tag=ai')
        content = response.content.decode()
        self.assertIn('Clear filter', content)

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

    def test_anonymous_sees_email_signup_for_free_download(self):
        response = self.client.get('/downloads')
        self.assertContains(response, 'Sign Up to Download')

    def test_anonymous_sees_upgrade_cta_for_gated_download(self):
        response = self.client.get('/downloads')
        self.assertContains(response, 'Upgrade to Basic to download')

    def test_authenticated_sees_download_button_for_free_resource(self):
        user = User.objects.create_user(
            email='free@test.com', password='testpass',
        )
        self.client.login(email='free@test.com', password='testpass')
        response = self.client.get('/downloads')
        # Free resource with level 0 should show download for authenticated users
        self.assertContains(response, 'Download')

    def test_basic_user_sees_download_for_basic_resource(self):
        user = User.objects.create_user(
            email='basic@test.com', password='testpass',
            tier=self.basic_tier,
        )
        self.client.login(email='basic@test.com', password='testpass')
        response = self.client.get('/downloads')
        # Should be able to download the basic resource
        content = response.content.decode()
        self.assertIn('/api/downloads/basic-resource/file', content)

    def test_free_user_sees_upgrade_for_basic_resource(self):
        user = User.objects.create_user(
            email='freeuser@test.com', password='testpass',
            tier=self.free_tier,
        )
        self.client.login(email='freeuser@test.com', password='testpass')
        response = self.client.get('/downloads')
        self.assertContains(response, 'Upgrade to Basic to download')

    def test_lock_icon_on_gated_download(self):
        response = self.client.get('/downloads')
        # The basic resource should show a lock icon
        content = response.content.decode()
        # Lock icon is used for required_level > 0
        self.assertIn('data-lucide="lock"', content)


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
        user = User.objects.create_user(
            email='dl_auth@test.com', password='testpass',
        )
        self.client.login(email='dl_auth@test.com', password='testpass')
        response = self.client.get('/api/downloads/free-pdf/file')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'https://example.com/files/free.pdf')

    def test_download_increments_count(self):
        user = User.objects.create_user(
            email='dl_count@test.com', password='testpass',
        )
        self.client.login(email='dl_count@test.com', password='testpass')
        self.client.get('/api/downloads/free-pdf/file')
        self.free_download.refresh_from_db()
        self.assertEqual(self.free_download.download_count, 1)

    def test_multiple_downloads_increment_count(self):
        user = User.objects.create_user(
            email='dl_multi@test.com', password='testpass',
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
        user = User.objects.create_user(
            email='dl_noauth@test.com', password='testpass',
            tier=self.free_tier,
        )
        self.client.login(email='dl_noauth@test.com', password='testpass')
        response = self.client.get('/api/downloads/basic-pdf/file')
        self.assertEqual(response.status_code, 403)

    def test_basic_user_can_download_basic_resource(self):
        user = User.objects.create_user(
            email='dl_basic@test.com', password='testpass',
            tier=self.basic_tier,
        )
        self.client.login(email='dl_basic@test.com', password='testpass')
        response = self.client.get('/api/downloads/basic-pdf/file')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'https://example.com/files/basic.pdf')

    def test_basic_user_cannot_download_premium_resource(self):
        user = User.objects.create_user(
            email='dl_basic2@test.com', password='testpass',
            tier=self.basic_tier,
        )
        self.client.login(email='dl_basic2@test.com', password='testpass')
        response = self.client.get('/api/downloads/premium-pdf/file')
        self.assertEqual(response.status_code, 403)

    def test_premium_user_can_download_premium_resource(self):
        user = User.objects.create_user(
            email='dl_premium@test.com', password='testpass',
            tier=self.premium_tier,
        )
        self.client.login(email='dl_premium@test.com', password='testpass')
        response = self.client.get('/api/downloads/premium-pdf/file')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'https://example.com/files/premium.pdf')

    def test_nonexistent_download_returns_404(self):
        user = User.objects.create_user(
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
        user = User.objects.create_user(
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
        user = User.objects.create_user(
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
        import re
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
