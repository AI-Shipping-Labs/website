"""Tests for synced content read-only behavior in Studio.

Verifies:
- Synced items (source_repo set) are read-only: POST returns 403
- Synced items show the GitHub banner and edit URL
- Synced items show sync metadata
- Non-synced items remain fully editable
- Operational actions (notify, announce) still work for synced content
- List pages show synced badges and View/Edit links
- Create URLs are removed for synced content types
- Events remain fully editable (not synced)
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from content.models import Article, Course, Download, Module, Project, Unit
from events.models import Event
from integrations.models import ContentSource
from studio.utils import get_github_edit_url, is_synced

User = get_user_model()


class SyncedUtilsTest(TestCase):
    """Test is_synced and get_github_edit_url helpers."""

    @classmethod
    def setUpTestData(cls):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
        )
        cls.synced_article = Article.objects.create(
            title='Synced', slug='synced', date=timezone.now().date(),
            source_repo='AI-Shipping-Labs/content',
            # Issue #310: source_path is now repo-relative, including
            # the historical content_path prefix.
            source_path='blog/synced.md',
            source_commit='abc1234def5678901234567890123456789abcde',
        )
        cls.manual_article = Article.objects.create(
            title='Manual', slug='manual', date=timezone.now().date(),
        )

    def test_is_synced_true_when_source_repo_set(self):
        self.assertTrue(is_synced(self.synced_article))

    def test_is_synced_false_when_source_repo_null(self):
        self.assertFalse(is_synced(self.manual_article))

    def test_github_edit_url_includes_content_path_prefix(self):
        url = get_github_edit_url(self.synced_article)
        self.assertEqual(
            url,
            'https://github.com/AI-Shipping-Labs/content/blob/main/blog/synced.md',
        )

    def test_github_edit_url_none_for_manual_item(self):
        self.assertIsNone(get_github_edit_url(self.manual_article))


class SyncedArticleReadOnlyTest(TestCase):
    """Test that synced articles are read-only in Studio."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        ContentSource.objects.get_or_create(
            repo_name='AI-Shipping-Labs/content',
        )
        self.synced = Article.objects.create(
            title='Synced Article', slug='synced-art',
            date=timezone.now().date(), published=True,
            source_repo='AI-Shipping-Labs/content',
            # Issue #310: source_path is repo-relative including the
            # historical content_path prefix.
            source_path='blog/synced-art.md',
            source_commit='abc1234def5678901234567890123456789abcde',
        )
        self.manual = Article.objects.create(
            title='Manual Article', slug='manual-art',
            date=timezone.now().date(), published=False,
        )

    def test_synced_article_edit_get_returns_200(self):
        response = self.client.get(f'/studio/articles/{self.synced.pk}/edit')
        self.assertEqual(response.status_code, 200)

    def test_synced_article_shows_banner(self):
        response = self.client.get(f'/studio/articles/{self.synced.pk}/edit')
        self.assertContains(
            response,
            'This content is synced from GitHub',
        )

    def test_synced_article_shows_github_link(self):
        response = self.client.get(f'/studio/articles/{self.synced.pk}/edit')
        self.assertContains(
            response,
            'https://github.com/AI-Shipping-Labs/content/blob/main/blog/synced-art.md',
        )
        self.assertContains(response, 'Edit on GitHub')

    def test_synced_article_shows_sync_metadata(self):
        response = self.client.get(f'/studio/articles/{self.synced.pk}/edit')
        self.assertContains(response, 'AI-Shipping-Labs/content')
        self.assertContains(response, 'synced-art.md')
        # truncatechars:10 gives first 7 chars + ellipsis
        self.assertContains(response, 'abc1234')

    def test_synced_article_has_disabled_fields(self):
        response = self.client.get(f'/studio/articles/{self.synced.pk}/edit')
        # Fields should have disabled attribute
        content = response.content.decode()
        self.assertIn('disabled', content)

    def test_synced_article_hides_save_button(self):
        response = self.client.get(f'/studio/articles/{self.synced.pk}/edit')
        self.assertNotContains(response, 'Save Changes')

    def test_synced_article_post_returns_403(self):
        original_title = self.synced.title
        response = self.client.post(f'/studio/articles/{self.synced.pk}/edit', {
            'title': 'Hacked Title',
            'slug': 'synced-art',
            'date': '2024-01-01',
            'status': 'draft',
            'required_level': '0',
        })
        self.assertEqual(response.status_code, 403)
        self.synced.refresh_from_db()
        self.assertEqual(self.synced.title, original_title)

    def test_synced_article_still_shows_notification_actions(self):
        response = self.client.get(f'/studio/articles/{self.synced.pk}/edit')
        self.assertContains(response, 'Notify subscribers')
        self.assertContains(response, 'Post to Slack')

    def test_manual_article_is_editable(self):
        response = self.client.get(f'/studio/articles/{self.manual.pk}/edit')
        self.assertNotContains(response, 'This content is synced from GitHub')
        self.assertContains(response, 'Save Changes')

    def test_manual_article_post_succeeds(self):
        response = self.client.post(f'/studio/articles/{self.manual.pk}/edit', {
            'title': 'Updated Manual',
            'slug': 'manual-art',
            'date': '2024-06-01',
            'status': 'draft',
            'required_level': '0',
        })
        self.assertEqual(response.status_code, 302)
        self.manual.refresh_from_db()
        self.assertEqual(self.manual.title, 'Updated Manual')

    def test_context_has_is_synced_true(self):
        response = self.client.get(f'/studio/articles/{self.synced.pk}/edit')
        self.assertTrue(response.context['is_synced'])

    def test_context_has_is_synced_false_for_manual(self):
        response = self.client.get(f'/studio/articles/{self.manual.pk}/edit')
        self.assertFalse(response.context['is_synced'])


class SyncedArticleListTest(TestCase):
    """Test article list shows synced badges and View/Edit links."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.synced = Article.objects.create(
            title='SyncedListArticle', slug='synced-list',
            date=timezone.now().date(),
            source_repo='AI-Shipping-Labs/content',
        )
        self.manual = Article.objects.create(
            title='ManualListArticle', slug='manual-list',
            date=timezone.now().date(),
        )

    def test_synced_article_shows_origin_badge(self):
        response = self.client.get('/studio/articles/')
        self.assertContains(response, 'data-testid="origin-badge"')
        self.assertContains(response, 'data-origin="synced"')
        self.assertNotContains(response, 'data-testid="synced-badge"')

    def test_synced_article_shows_view_link(self):
        response = self.client.get('/studio/articles/')
        content = response.content.decode()
        # The synced article row should have "View" and the manual one "Edit"
        self.assertIn('>View<', content)
        self.assertIn('>Edit<', content)

    def test_no_new_article_button(self):
        response = self.client.get('/studio/articles/')
        self.assertNotContains(response, 'New Article')


class SyncedRecordingReadOnlyTest(TestCase):
    """Test that synced recordings (events with recordings) are read-only in Studio."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        ContentSource.objects.get_or_create(
            repo_name='AI-Shipping-Labs/content',
        )
        self.synced = Event.objects.create(
            title='Synced Recording', slug='synced-rec',
            start_datetime=timezone.now(), status='completed',
            recording_url='https://youtube.com/watch?v=test',
            published=True,
            source_repo='AI-Shipping-Labs/content',
            source_path='synced-rec.md',
            source_commit='def4567890abcdef1234567890abcdef12345678',
        )

    def test_synced_recording_post_returns_403(self):
        response = self.client.post(f'/studio/recordings/{self.synced.pk}/edit', {
            'title': 'Hacked', 'slug': 'synced-rec',
            'required_level': '0',
        })
        self.assertEqual(response.status_code, 403)
        self.synced.refresh_from_db()
        self.assertEqual(self.synced.title, 'Synced Recording')

    def test_synced_recording_shows_banner(self):
        response = self.client.get(f'/studio/recordings/{self.synced.pk}/edit')
        self.assertContains(response, 'This content is synced from GitHub')

    def test_synced_recording_shows_metadata(self):
        response = self.client.get(f'/studio/recordings/{self.synced.pk}/edit')
        self.assertContains(response, 'synced-rec.md')

    def test_synced_recording_hides_save_button(self):
        response = self.client.get(f'/studio/recordings/{self.synced.pk}/edit')
        self.assertNotContains(response, 'Save Changes')

    def test_recording_list_no_new_button(self):
        response = self.client.get('/studio/recordings/')
        self.assertNotContains(response, 'New Recording')


class SyncedCourseReadOnlyTest(TestCase):
    """Test that synced courses are read-only in Studio."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        ContentSource.objects.get_or_create(
            repo_name='AI-Shipping-Labs/content',
        )
        self.synced = Course.objects.create(
            title='Synced Course', slug='synced-course', status='published',
            source_repo='AI-Shipping-Labs/content',
            source_path='synced-course/meta.yaml',
            source_commit='abc1234def5678901234567890123456789abcde',
        )
        self.module = Module.objects.create(
            course=self.synced, title='Module 1', sort_order=0,
            source_repo='AI-Shipping-Labs/content',
        )
        self.unit = Unit.objects.create(
            module=self.module, title='Unit 1', sort_order=0,
            source_repo='AI-Shipping-Labs/content',
        )

    def test_synced_course_post_returns_403(self):
        response = self.client.post(f'/studio/courses/{self.synced.pk}/edit', {
            'title': 'Hacked', 'slug': 'synced-course',
            'status': 'draft', 'required_level': '0',
        })
        self.assertEqual(response.status_code, 403)
        self.synced.refresh_from_db()
        self.assertEqual(self.synced.title, 'Synced Course')

    def test_synced_course_shows_banner(self):
        response = self.client.get(f'/studio/courses/{self.synced.pk}/edit')
        self.assertContains(response, 'This content is synced from GitHub')

    def test_synced_course_hides_save_button(self):
        response = self.client.get(f'/studio/courses/{self.synced.pk}/edit')
        self.assertNotContains(response, 'Save Changes')

    def test_synced_course_hides_add_module_button(self):
        response = self.client.get(f'/studio/courses/{self.synced.pk}/edit')
        self.assertNotContains(response, 'Add Module')

    def test_synced_course_hides_add_unit_button(self):
        response = self.client.get(f'/studio/courses/{self.synced.pk}/edit')
        self.assertNotContains(response, 'Add Unit')

    def test_module_create_blocked_for_synced_course(self):
        module_count = Module.objects.filter(course=self.synced).count()
        response = self.client.post(
            f'/studio/courses/{self.synced.pk}/modules/add',
            {'title': 'New Module'},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            Module.objects.filter(course=self.synced).count(), module_count,
        )

    def test_unit_create_blocked_for_synced_course(self):
        unit_count = Unit.objects.filter(module=self.module).count()
        response = self.client.post(
            f'/studio/modules/{self.module.pk}/units/add',
            {'title': 'New Unit'},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            Unit.objects.filter(module=self.module).count(), unit_count,
        )

    def test_unit_edit_blocked_for_synced_course(self):
        response = self.client.post(f'/studio/units/{self.unit.pk}/edit', {
            'title': 'Hacked Unit',
        })
        self.assertEqual(response.status_code, 403)
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.title, 'Unit 1')

    def test_synced_course_still_shows_manage_access(self):
        response = self.client.get(f'/studio/courses/{self.synced.pk}/edit')
        self.assertContains(response, 'Manage Access')

    def test_synced_course_still_shows_manage_peer_reviews(self):
        response = self.client.get(f'/studio/courses/{self.synced.pk}/edit')
        self.assertContains(response, 'Manage Peer Reviews')

    def test_course_list_no_new_button(self):
        response = self.client.get('/studio/courses/')
        self.assertNotContains(response, 'New Course')


class SyncedDownloadReadOnlyTest(TestCase):
    """Test that synced downloads are read-only in Studio."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        ContentSource.objects.get_or_create(
            repo_name='AI-Shipping-Labs/content',
        )
        self.synced = Download.objects.create(
            title='Synced Download', slug='synced-dl',
            file_url='https://example.com/file.pdf',
            source_repo='AI-Shipping-Labs/content',
            source_path='downloads/synced-dl.md',
        )

    def test_synced_download_post_returns_403(self):
        response = self.client.post(f'/studio/downloads/{self.synced.pk}/edit', {
            'title': 'Hacked', 'slug': 'synced-dl',
            'file_url': 'https://example.com/hacked.pdf',
            'file_type': 'pdf', 'file_size_bytes': '0',
            'required_level': '0',
        })
        self.assertEqual(response.status_code, 403)
        self.synced.refresh_from_db()
        self.assertEqual(self.synced.title, 'Synced Download')

    def test_synced_download_shows_banner(self):
        response = self.client.get(f'/studio/downloads/{self.synced.pk}/edit')
        self.assertContains(response, 'This content is synced from GitHub')

    def test_download_list_no_new_button(self):
        response = self.client.get('/studio/downloads/')
        self.assertNotContains(response, 'New Download')


class SyncedProjectReadOnlyTest(TestCase):
    """Test that synced projects are read-only in Studio."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        ContentSource.objects.get_or_create(
            repo_name='AI-Shipping-Labs/content',
        )
        self.synced = Project.objects.create(
            title='Synced Project', slug='synced-proj',
            date=timezone.now().date(), status='published',
            source_repo='AI-Shipping-Labs/content',
            source_path='synced-proj.md',
        )

    def test_synced_project_post_returns_403(self):
        response = self.client.post(
            f'/studio/projects/{self.synced.pk}/review',
            {'action': 'approve'},
        )
        self.assertEqual(response.status_code, 403)

    def test_synced_project_shows_banner(self):
        response = self.client.get(f'/studio/projects/{self.synced.pk}/review')
        self.assertContains(response, 'This content is synced from GitHub')

    def test_synced_project_hides_moderation_buttons(self):
        # Set to pending_review to check buttons are hidden for synced
        self.synced.status = 'pending_review'
        self.synced.save()
        response = self.client.get(f'/studio/projects/{self.synced.pk}/review')
        self.assertNotContains(response, 'Approve')
        self.assertNotContains(response, 'Reject')


class EventNoCreateTest(TestCase):
    """Test that events no longer have a create button or URL (managed via content repo)."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_event_list_has_no_new_button(self):
        response = self.client.get('/studio/events/')
        self.assertNotContains(response, 'New Event')

    def test_event_create_url_returns_404(self):
        response = self.client.get('/studio/events/new')
        self.assertEqual(response.status_code, 404)


class DashboardSyncSectionTest(TestCase):
    """Test that dashboard includes Content Sync section."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_dashboard_has_sync_section(self):
        response = self.client.get('/studio/')
        self.assertContains(response, 'Content Sync')
        self.assertContains(response, 'Sync Dashboard')
