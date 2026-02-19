"""Tests for Event Recordings - issue #74.

Covers:
- Recording model fields (published_at, video_url property, etc.)
- Published_at sync with published flag
- Tag filtering on /event-recordings via ?tag=X
- Pagination (20 recordings per page)
- Detail page: video player for authorized, gated CTA for unauthorized
- Detail page: materials listed as links
- Admin CRUD for recordings
- Lock icon on listing for gated recordings
- Clickable tags in listing and detail
- Title tag format on detail page
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.utils import timezone

from content.access import LEVEL_OPEN, LEVEL_BASIC, LEVEL_MAIN, LEVEL_PREMIUM
from content.models import Recording
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


# --- Model field tests ---


class RecordingPublishedAtTest(TestCase):
    """Test published_at field and sync with published flag."""

    def test_published_at_set_when_published_true(self):
        rec = Recording.objects.create(
            title='Test', slug='pub-at-test', date=date(2025, 7, 1),
            published=True,
        )
        self.assertIsNotNone(rec.published_at)

    def test_published_at_null_when_published_false(self):
        rec = Recording.objects.create(
            title='Test', slug='pub-at-false', date=date(2025, 7, 1),
            published=False,
        )
        self.assertIsNone(rec.published_at)

    def test_published_at_cleared_on_unpublish(self):
        rec = Recording.objects.create(
            title='Test', slug='clear-pub-at', date=date(2025, 7, 1),
            published=True,
        )
        self.assertIsNotNone(rec.published_at)
        rec.published = False
        rec.save()
        rec.refresh_from_db()
        self.assertIsNone(rec.published_at)

    def test_published_at_not_overwritten_on_re_save(self):
        rec = Recording.objects.create(
            title='Test', slug='no-overwrite', date=date(2025, 7, 1),
            published=True,
        )
        original_published_at = rec.published_at
        rec.title = 'Updated Title'
        rec.save()
        rec.refresh_from_db()
        self.assertEqual(rec.published_at, original_published_at)


class RecordingVideoUrlPropertyTest(TestCase):
    """Test video_url property."""

    def test_video_url_returns_youtube_url(self):
        rec = Recording(youtube_url='https://youtube.com/watch?v=test')
        self.assertEqual(rec.video_url, 'https://youtube.com/watch?v=test')

    def test_video_url_returns_google_embed_if_no_youtube(self):
        rec = Recording(
            youtube_url='',
            google_embed_url='https://docs.google.com/presentation/embed/123',
        )
        self.assertEqual(rec.video_url, 'https://docs.google.com/presentation/embed/123')

    def test_video_url_empty_when_both_empty(self):
        rec = Recording(youtube_url='', google_embed_url='')
        self.assertEqual(rec.video_url, '')

    def test_video_url_prefers_youtube_over_google(self):
        rec = Recording(
            youtube_url='https://youtube.com/watch?v=test',
            google_embed_url='https://docs.google.com/embed/123',
        )
        self.assertEqual(rec.video_url, 'https://youtube.com/watch?v=test')


class RecordingModelFieldsTest(TestCase):
    """Test all Recording model fields exist and have correct defaults."""

    def test_slug_unique(self):
        from django.db import IntegrityError
        Recording.objects.create(
            title='First', slug='unique-slug', date=date(2025, 7, 1),
        )
        with self.assertRaises(IntegrityError):
            Recording.objects.create(
                title='Second', slug='unique-slug', date=date(2025, 7, 2),
            )

    def test_all_fields_exist(self):
        rec = Recording.objects.create(
            title='Full Recording',
            slug='full-recording',
            description='A full recording description',
            date=date(2025, 7, 20),
            tags=['agents', 'python'],
            level='Intermediate',
            youtube_url='https://youtube.com/watch?v=abc',
            timestamps=[{'time_seconds': 0, 'label': 'Intro'}],
            materials=[{'title': 'Slides', 'url': 'https://example.com/slides'}],
            core_tools=['Python', 'Django'],
            learning_objectives=['Learn Django'],
            outcome='Build an app',
            required_level=LEVEL_MAIN,
            published=True,
        )
        self.assertEqual(rec.title, 'Full Recording')
        self.assertEqual(rec.slug, 'full-recording')
        self.assertEqual(rec.description, 'A full recording description')
        self.assertEqual(rec.tags, ['agents', 'python'])
        self.assertEqual(len(rec.timestamps), 1)
        self.assertEqual(len(rec.materials), 1)
        self.assertEqual(rec.required_level, LEVEL_MAIN)
        self.assertTrue(rec.published)
        self.assertIsNotNone(rec.published_at)
        self.assertIsNotNone(rec.created_at)

    def test_default_values(self):
        rec = Recording.objects.create(
            title='Minimal', slug='minimal', date=date(2025, 1, 1),
        )
        self.assertEqual(rec.description, '')
        self.assertEqual(rec.tags, [])
        self.assertEqual(rec.timestamps, [])
        self.assertEqual(rec.materials, [])
        self.assertEqual(rec.core_tools, [])
        self.assertEqual(rec.learning_objectives, [])
        self.assertEqual(rec.outcome, '')
        self.assertEqual(rec.required_level, 0)
        self.assertTrue(rec.published)

    def test_ordering_by_date_desc(self):
        rec1 = Recording.objects.create(
            title='Old', slug='old', date=date(2025, 1, 1),
        )
        rec2 = Recording.objects.create(
            title='New', slug='new', date=date(2025, 7, 1),
        )
        recordings = list(Recording.objects.all())
        self.assertEqual(recordings[0].slug, 'new')
        self.assertEqual(recordings[1].slug, 'old')

    def test_get_absolute_url(self):
        rec = Recording(slug='my-recording')
        self.assertEqual(rec.get_absolute_url(), '/event-recordings/my-recording')

    def test_formatted_date(self):
        rec = Recording(date=date(2025, 7, 20))
        self.assertEqual(rec.formatted_date(), 'July 20, 2025')

    def test_short_date(self):
        rec = Recording(date=date(2025, 7, 20))
        self.assertEqual(rec.short_date(), 'Jul 20, 2025')

    def test_str(self):
        rec = Recording(title='My Recording')
        self.assertEqual(str(rec), 'My Recording')


# --- Tag filtering tests ---


class RecordingsListTagFilteringTest(TestCase):
    """Test tag filtering on /event-recordings via ?tag=X query param."""

    def setUp(self):
        self.client = Client()
        self.agents_recording = Recording.objects.create(
            title='Agent Workshop',
            slug='agent-workshop',
            description='Learn agents',
            date=date(2025, 7, 20),
            tags=['agents', 'python'],
            published=True,
        )
        self.django_recording = Recording.objects.create(
            title='Django Workshop',
            slug='django-workshop',
            description='Learn Django',
            date=date(2025, 7, 15),
            tags=['django', 'python'],
            published=True,
        )
        self.mcp_recording = Recording.objects.create(
            title='MCP Workshop',
            slug='mcp-workshop',
            description='Learn MCP',
            date=date(2025, 7, 10),
            tags=['mcp', 'agents'],
            published=True,
        )

    def test_no_filter_shows_all(self):
        response = self.client.get('/event-recordings')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Agent Workshop')
        self.assertContains(response, 'Django Workshop')
        self.assertContains(response, 'MCP Workshop')

    def test_filter_by_python_tag(self):
        response = self.client.get('/event-recordings?tag=python')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Agent Workshop')
        self.assertContains(response, 'Django Workshop')
        self.assertNotContains(response, 'MCP Workshop')

    def test_filter_by_agents_tag(self):
        response = self.client.get('/event-recordings?tag=agents')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Agent Workshop')
        self.assertContains(response, 'MCP Workshop')
        self.assertNotContains(response, 'Django Workshop')

    def test_filter_by_nonexistent_tag(self):
        response = self.client.get('/event-recordings?tag=nonexistent')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Agent Workshop')
        self.assertNotContains(response, 'Django Workshop')
        self.assertNotContains(response, 'MCP Workshop')

    def test_tag_links_in_listing(self):
        response = self.client.get('/event-recordings')
        content = response.content.decode()
        self.assertIn('?tag=python', content)
        self.assertIn('?tag=agents', content)
        self.assertIn('?tag=django', content)
        self.assertIn('?tag=mcp', content)

    def test_all_tags_displayed_in_filter_bar(self):
        response = self.client.get('/event-recordings')
        content = response.content.decode()
        self.assertIn('Filter by tag', content)

    def test_current_tag_in_context(self):
        response = self.client.get('/event-recordings?tag=python')
        self.assertEqual(response.context['current_tag'], 'python')

    def test_clear_filter_link(self):
        response = self.client.get('/event-recordings?tag=python')
        content = response.content.decode()
        self.assertIn('Clear filter', content)

    def test_empty_tag_ignored(self):
        response = self.client.get('/event-recordings?tag=')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Agent Workshop')
        self.assertContains(response, 'Django Workshop')
        self.assertContains(response, 'MCP Workshop')


# --- Pagination tests ---


class RecordingsListPaginationTest(TestCase):
    """Test pagination on /event-recordings (20 per page)."""

    def setUp(self):
        self.client = Client()
        # Create 25 recordings to test pagination
        for i in range(25):
            Recording.objects.create(
                title=f'Recording {i:02d}',
                slug=f'recording-{i:02d}',
                description=f'Description {i}',
                date=date(2025, 7, 1),
                published=True,
            )

    def test_first_page_has_20_items(self):
        response = self.client.get('/event-recordings')
        self.assertEqual(response.status_code, 200)
        page_obj = response.context['page_obj']
        self.assertEqual(len(page_obj), 20)

    def test_second_page_has_remaining_items(self):
        response = self.client.get('/event-recordings?page=2')
        self.assertEqual(response.status_code, 200)
        page_obj = response.context['page_obj']
        self.assertEqual(len(page_obj), 5)

    def test_pagination_controls_shown(self):
        response = self.client.get('/event-recordings')
        content = response.content.decode()
        self.assertIn('Page 1 of 2', content)
        self.assertIn('Next', content)

    def test_previous_link_on_page_2(self):
        response = self.client.get('/event-recordings?page=2')
        content = response.content.decode()
        self.assertIn('Previous', content)

    def test_no_pagination_when_under_20(self):
        Recording.objects.all().delete()
        for i in range(5):
            Recording.objects.create(
                title=f'Small Recording {i}',
                slug=f'small-recording-{i}',
                date=date(2025, 7, 1),
                published=True,
            )
        response = self.client.get('/event-recordings')
        self.assertFalse(response.context['is_paginated'])

    def test_pagination_preserves_tag_filter(self):
        # Delete existing and create 25 tagged recordings
        Recording.objects.all().delete()
        for i in range(25):
            Recording.objects.create(
                title=f'Tagged Rec {i:02d}',
                slug=f'tagged-rec-{i:02d}',
                date=date(2025, 7, 1),
                tags=['python'],
                published=True,
            )
        response = self.client.get('/event-recordings?tag=python')
        content = response.content.decode()
        # The next page link should preserve the tag filter
        self.assertIn('tag=python', content)

    def test_invalid_page_number_shows_last(self):
        response = self.client.get('/event-recordings?page=999')
        self.assertEqual(response.status_code, 200)
        # Paginator.get_page returns the last page for out-of-range
        page_obj = response.context['page_obj']
        self.assertEqual(page_obj.number, 2)


# --- Recordings list display tests ---


class RecordingsListDisplayTest(TestCase):
    """Test that recordings listing shows required fields."""

    def setUp(self):
        self.client = Client()
        self.recording = Recording.objects.create(
            title='Workshop Display Test',
            slug='workshop-display',
            description='Workshop description here',
            date=date(2025, 7, 20),
            tags=['agents', 'python'],
            published=True,
        )

    def test_shows_title(self):
        response = self.client.get('/event-recordings')
        self.assertContains(response, 'Workshop Display Test')

    def test_shows_description(self):
        response = self.client.get('/event-recordings')
        self.assertContains(response, 'Workshop description here')

    def test_shows_date(self):
        response = self.client.get('/event-recordings')
        self.assertContains(response, 'July 20, 2025')

    def test_shows_tags(self):
        response = self.client.get('/event-recordings')
        self.assertContains(response, 'agents')
        self.assertContains(response, 'python')

    def test_tags_are_clickable_links(self):
        response = self.client.get('/event-recordings')
        content = response.content.decode()
        self.assertIn('href="/event-recordings?tag=agents"', content)
        self.assertIn('href="/event-recordings?tag=python"', content)

    def test_gated_recording_shows_lock_icon(self):
        Recording.objects.create(
            title='Gated Recording',
            slug='gated-display',
            date=date(2025, 7, 15),
            required_level=LEVEL_MAIN,
            published=True,
        )
        response = self.client.get('/event-recordings')
        self.assertContains(response, 'data-lucide="lock"')

    def test_open_recording_no_lock_icon(self):
        # Only open recordings, no lock
        response = self.client.get('/event-recordings')
        content = response.content.decode()
        self.assertNotIn('data-lucide="lock"', content)

    def test_empty_list_message(self):
        Recording.objects.all().delete()
        response = self.client.get('/event-recordings')
        self.assertContains(response, 'No resources yet')

    def test_unpublished_not_shown(self):
        Recording.objects.create(
            title='Draft Recording',
            slug='draft-recording',
            date=date(2025, 7, 10),
            published=False,
        )
        response = self.client.get('/event-recordings')
        self.assertNotContains(response, 'Draft Recording')

    def test_sorting_by_date_desc(self):
        Recording.objects.create(
            title='Older Workshop',
            slug='older-workshop',
            date=date(2025, 1, 1),
            published=True,
        )
        response = self.client.get('/event-recordings')
        content = response.content.decode()
        new_pos = content.index('Workshop Display Test')
        old_pos = content.index('Older Workshop')
        self.assertLess(new_pos, old_pos)


# --- Recording detail display tests ---


class RecordingDetailDisplayTest(TestCase):
    """Test recording detail page shows all required elements."""

    def setUp(self):
        self.client = Client()
        self.recording = Recording.objects.create(
            title='Detail Workshop',
            slug='detail-workshop',
            description='Workshop for detail testing',
            date=date(2025, 7, 20),
            level='Intermediate',
            tags=['python', 'agents'],
            youtube_url='https://youtube.com/watch?v=test123',
            timestamps=[
                {'time_seconds': 0, 'label': 'Introduction'},
                {'time_seconds': 125, 'label': 'Setting up'},
            ],
            materials=[
                {'title': 'Slides PDF', 'url': 'https://example.com/slides.pdf', 'type': 'slides'},
                {'title': 'GitHub Repo', 'url': 'https://github.com/example/repo'},
            ],
            core_tools=['Python', 'Django'],
            learning_objectives=['Build an API', 'Deploy to production'],
            outcome='A working API deployment',
            published=True,
        )

    def test_status_code_200(self):
        response = self.client.get('/event-recordings/detail-workshop')
        self.assertEqual(response.status_code, 200)

    def test_template_used(self):
        response = self.client.get('/event-recordings/detail-workshop')
        self.assertTemplateUsed(response, 'content/recording_detail.html')

    def test_shows_title(self):
        response = self.client.get('/event-recordings/detail-workshop')
        self.assertContains(response, 'Detail Workshop')

    def test_shows_description(self):
        response = self.client.get('/event-recordings/detail-workshop')
        self.assertContains(response, 'Workshop for detail testing')

    def test_shows_date(self):
        response = self.client.get('/event-recordings/detail-workshop')
        self.assertContains(response, 'July 20, 2025')

    def test_shows_level(self):
        response = self.client.get('/event-recordings/detail-workshop')
        self.assertContains(response, 'Intermediate')

    def test_shows_tags(self):
        response = self.client.get('/event-recordings/detail-workshop')
        self.assertContains(response, 'python')
        self.assertContains(response, 'agents')

    def test_tags_are_clickable_links(self):
        response = self.client.get('/event-recordings/detail-workshop')
        content = response.content.decode()
        self.assertIn('href="/event-recordings?tag=python"', content)
        self.assertIn('href="/event-recordings?tag=agents"', content)

    def test_shows_materials(self):
        response = self.client.get('/event-recordings/detail-workshop')
        self.assertContains(response, 'Materials')
        self.assertContains(response, 'Slides PDF')
        self.assertContains(response, 'GitHub Repo')
        self.assertContains(response, 'https://example.com/slides.pdf')
        self.assertContains(response, 'https://github.com/example/repo')

    def test_shows_core_tools(self):
        response = self.client.get('/event-recordings/detail-workshop')
        self.assertContains(response, 'Core Tools')
        self.assertContains(response, 'Python')
        self.assertContains(response, 'Django')

    def test_shows_learning_objectives(self):
        response = self.client.get('/event-recordings/detail-workshop')
        self.assertContains(response, 'Build an API')
        self.assertContains(response, 'Deploy to production')

    def test_shows_outcome(self):
        response = self.client.get('/event-recordings/detail-workshop')
        self.assertContains(response, 'A working API deployment')

    def test_shows_timestamps(self):
        response = self.client.get('/event-recordings/detail-workshop')
        content = response.content.decode()
        self.assertIn('Introduction', content)
        self.assertIn('Setting up', content)

    def test_404_for_nonexistent_slug(self):
        response = self.client.get('/event-recordings/nonexistent')
        self.assertEqual(response.status_code, 404)

    def test_404_for_unpublished(self):
        Recording.objects.create(
            title='Draft', slug='draft-detail',
            date=date(2025, 7, 1), published=False,
        )
        response = self.client.get('/event-recordings/draft-detail')
        self.assertEqual(response.status_code, 404)

    def test_title_tag_format(self):
        response = self.client.get('/event-recordings/detail-workshop')
        content = response.content.decode()
        self.assertIn('<title>Detail Workshop | AI Shipping Labs</title>', content)


# --- Access control tests ---


class RecordingDetailAccessControlTest(TierSetupMixin, TestCase):
    """Test recording detail view access control."""

    def setUp(self):
        self.client = Client()
        self.open_recording = Recording.objects.create(
            title='Open Recording',
            slug='open-recording',
            description='Open description',
            youtube_url='https://youtube.com/watch?v=open',
            date=date(2025, 7, 20),
            published=True,
            required_level=LEVEL_OPEN,
        )
        self.gated_recording = Recording.objects.create(
            title='Gated Recording',
            slug='gated-recording',
            description='Gated description',
            youtube_url='https://youtube.com/watch?v=gated',
            materials=[{'title': 'Secret Slides', 'url': 'https://example.com/secret'}],
            date=date(2025, 7, 20),
            published=True,
            required_level=LEVEL_MAIN,
        )

    def test_anonymous_sees_open_recording_video(self):
        response = self.client.get('/event-recordings/open-recording')
        self.assertEqual(response.status_code, 200)
        # Should not be gated
        self.assertFalse(response.context['is_gated'])

    def test_anonymous_sees_gated_recording_title_and_description(self):
        response = self.client.get('/event-recordings/gated-recording')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Gated Recording')
        self.assertContains(response, 'Gated description')

    def test_anonymous_does_not_see_gated_video(self):
        response = self.client.get('/event-recordings/gated-recording')
        # Video embed should not be present
        self.assertNotContains(response, 'youtube.com/embed')
        # CTA should be present
        self.assertContains(response, 'Upgrade to Main to watch this recording')

    def test_anonymous_does_not_see_gated_materials(self):
        response = self.client.get('/event-recordings/gated-recording')
        self.assertNotContains(response, 'Secret Slides')

    def test_free_user_sees_gated_cta(self):
        user = User.objects.create_user(email='free@test.com', password='testpass')
        user.tier = self.free_tier
        user.save()
        self.client.login(email='free@test.com', password='testpass')
        response = self.client.get('/event-recordings/gated-recording')
        self.assertContains(response, 'Upgrade to Main')

    def test_basic_user_cannot_see_main_recording(self):
        user = User.objects.create_user(email='basic@test.com', password='testpass')
        user.tier = self.basic_tier
        user.save()
        self.client.login(email='basic@test.com', password='testpass')
        response = self.client.get('/event-recordings/gated-recording')
        self.assertContains(response, 'Upgrade to Main')

    def test_main_user_sees_full_recording(self):
        user = User.objects.create_user(email='main@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main@test.com', password='testpass')
        response = self.client.get('/event-recordings/gated-recording')
        self.assertNotContains(response, 'Upgrade to Main')
        self.assertContains(response, 'Secret Slides')

    def test_premium_user_sees_full_recording(self):
        user = User.objects.create_user(email='premium@test.com', password='testpass')
        user.tier = self.premium_tier
        user.save()
        self.client.login(email='premium@test.com', password='testpass')
        response = self.client.get('/event-recordings/gated-recording')
        self.assertNotContains(response, 'Upgrade to Main')

    def test_gated_recording_never_returns_404(self):
        response = self.client.get('/event-recordings/gated-recording')
        self.assertEqual(response.status_code, 200)

    def test_gated_recording_shows_pricing_link(self):
        response = self.client.get('/event-recordings/gated-recording')
        self.assertContains(response, '/pricing')


# --- Admin tests ---


class RecordingAdminTest(TestCase):
    """Test admin CRUD for recordings."""

    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            email='admin@test.com', password='testpass',
        )
        self.client.login(email='admin@test.com', password='testpass')

    def test_admin_recording_list(self):
        Recording.objects.create(
            title='Admin Recording', slug='admin-rec', date=date(2025, 7, 20),
            published=True,
        )
        response = self.client.get('/admin/content/recording/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Admin Recording')

    def test_admin_create_recording(self):
        response = self.client.post('/admin/content/recording/add/', {
            'title': 'New Recording',
            'slug': 'new-recording',
            'description': 'A new recording',
            'date': '2025-07-20',
            'tags': '[]',
            'level': '',
            'google_embed_url': '',
            'youtube_url': 'https://youtube.com/watch?v=new',
            'timestamps': '[]',
            'materials': '[]',
            'core_tools': '[]',
            'learning_objectives': '[]',
            'outcome': '',
            'related_course': '',
            'required_level': 0,
            'published': True,
        })
        self.assertEqual(Recording.objects.filter(slug='new-recording').count(), 1)
        rec = Recording.objects.get(slug='new-recording')
        self.assertEqual(rec.title, 'New Recording')

    def test_admin_delete_recording(self):
        rec = Recording.objects.create(
            title='Delete Me', slug='delete-me', date=date(2025, 7, 20),
            published=True,
        )
        response = self.client.post(
            f'/admin/content/recording/{rec.pk}/delete/',
            {'post': 'yes'},
        )
        self.assertEqual(Recording.objects.filter(slug='delete-me').count(), 0)

    def test_admin_slug_auto_generated(self):
        """Verify prepopulated_fields config for slug from title."""
        from content.admin.recording import RecordingAdmin
        self.assertEqual(RecordingAdmin.prepopulated_fields, {'slug': ('title',)})

    def test_admin_search(self):
        Recording.objects.create(
            title='Searchable Recording', slug='searchable-rec',
            description='find me recording', date=date(2025, 7, 20),
            published=True,
        )
        response = self.client.get('/admin/content/recording/?q=Searchable')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Searchable Recording')

    def test_admin_published_at_in_list_display(self):
        from content.admin.recording import RecordingAdmin
        self.assertIn('published_at', RecordingAdmin.list_display)

    def test_admin_published_at_readonly(self):
        from content.admin.recording import RecordingAdmin
        self.assertIn('published_at', RecordingAdmin.readonly_fields)
