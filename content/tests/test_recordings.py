"""Tests for Event Recordings - unified under /events?filter=past (issue #294).

Covers:
- Event recording fields (published_at, video_url property, has_recording, etc.)
- Published_at sync with published flag
- Tag filtering on /events?filter=past via ?tag=X
- Pagination (20 recordings per page)
- Listing display fields (title, description, date, tags)
- Title tag format on detail page

Issue #426 retired the inline recording playback UI on the event detail page.
Recording playback (video player, materials, transcript, chapters, gating CTA)
lives on the linked Workshop's landing/video pages and is covered by
``content/tests/test_workshops_public.py``. Detail-page tests here assert that
the legacy inline UI is no longer rendered.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.access import LEVEL_MAIN, LEVEL_OPEN
from events.models import Event
from tests.fixtures import TierSetupMixin

User = get_user_model()


def _create_recording_event(slug, **kwargs):
    """Helper to create an Event that acts as a recording."""
    defaults = {
        'title': slug.replace('-', ' ').title(),
        'slug': slug,
        'start_datetime': timezone.now() - timedelta(days=7),
        'status': 'completed',
        'recording_url': 'https://youtube.com/watch?v=test',
        'published': True,
    }
    defaults.update(kwargs)
    return Event.objects.create(**defaults)


# --- Model field tests ---


class EventRecordingPublishedAtTest(TestCase):
    """Test published_at field and sync with published flag on Event."""

    def test_published_at_set_when_published_true(self):
        event = _create_recording_event('pub-at-test', published=True)
        self.assertIsNotNone(event.published_at)

    def test_published_at_null_when_published_false(self):
        event = _create_recording_event('pub-at-false', published=False)
        self.assertIsNone(event.published_at)

    def test_published_at_cleared_on_unpublish(self):
        event = _create_recording_event('clear-pub-at', published=True)
        self.assertIsNotNone(event.published_at)
        event.published = False
        event.save()
        event.refresh_from_db()
        self.assertIsNone(event.published_at)

    def test_published_at_not_overwritten_on_re_save(self):
        event = _create_recording_event('no-overwrite', published=True)
        original_published_at = event.published_at
        event.title = 'Updated Title'
        event.save()
        event.refresh_from_db()
        self.assertEqual(event.published_at, original_published_at)


class EventVideoUrlPropertyTest(TestCase):
    """Test video_url property on Event."""

    def test_video_url_returns_recording_url(self):
        event = Event(recording_url='https://youtube.com/watch?v=test')
        self.assertEqual(event.video_url, 'https://youtube.com/watch?v=test')

    def test_video_url_returns_embed_if_no_recording_url(self):
        event = Event(
            recording_url='',
            recording_embed_url='https://docs.google.com/presentation/embed/123',
        )
        self.assertEqual(event.video_url, 'https://docs.google.com/presentation/embed/123')

    def test_video_url_empty_when_both_empty(self):
        event = Event(recording_url='', recording_embed_url='')
        self.assertEqual(event.video_url, '')

    def test_video_url_prefers_s3_over_recording_url(self):
        event = Event(
            recording_s3_url='https://s3.example.com/vid.mp4',
            recording_url='https://youtube.com/watch?v=test',
        )
        self.assertEqual(event.video_url, 'https://s3.example.com/vid.mp4')

    def test_has_recording_true_with_url(self):
        event = Event(recording_url='https://youtube.com/watch?v=test')
        self.assertTrue(event.has_recording)

    def test_has_recording_false_without_url(self):
        event = Event(recording_url='', recording_s3_url='', recording_embed_url='')
        self.assertFalse(event.has_recording)


class EventRecordingFieldsTest(TestCase):
    """Test recording-related fields on Event model."""

    def test_all_recording_fields_exist(self):
        event = _create_recording_event(
            'full-recording',
            description='A full recording description',
            tags=['agents', 'python'],
            recording_url='https://youtube.com/watch?v=abc',
            timestamps=[{'time_seconds': 0, 'label': 'Intro'}],
            materials=[{'title': 'Slides', 'url': 'https://example.com/slides'}],
            core_tools=['Python', 'Django'],
            learning_objectives=['Learn Django'],
            outcome='Build an app',
            required_level=LEVEL_MAIN,
        )
        self.assertEqual(event.title, 'Full Recording')
        self.assertEqual(event.slug, 'full-recording')
        self.assertEqual(event.description, 'A full recording description')
        self.assertEqual(event.tags, ['agents', 'python'])
        self.assertEqual(len(event.timestamps), 1)
        self.assertEqual(len(event.materials), 1)
        self.assertEqual(event.required_level, LEVEL_MAIN)
        self.assertTrue(event.published)
        self.assertIsNotNone(event.published_at)
        self.assertIsNotNone(event.created_at)

    def test_get_recording_url(self):
        event = Event(slug='my-recording')
        self.assertEqual(event.get_recording_url(), '/events/my-recording')

    def test_formatted_date(self):
        event = Event(start_datetime=timezone.make_aware(
            timezone.datetime(2025, 7, 20, 12, 0),
        ))
        self.assertEqual(event.formatted_date(), 'July 20, 2025')

# --- Tag filtering tests ---


class RecordingsListTagFilteringTest(TestCase):
    """Test tag filtering on /events?filter=past via ?tag=X query param."""

    @classmethod
    def setUpTestData(cls):
        cls.agents_recording = _create_recording_event(
            'agent-workshop',
            title='Agent Workshop',
            description='Learn agents',
            tags=['agents', 'python'],
        )
        cls.django_recording = _create_recording_event(
            'django-workshop',
            title='Django Workshop',
            description='Learn Django',
            tags=['django', 'python'],
        )
        cls.mcp_recording = _create_recording_event(
            'mcp-workshop',
            title='MCP Workshop',
            description='Learn MCP',
            tags=['mcp', 'agents'],
        )

    def test_no_filter_shows_all(self):
        response = self.client.get('/events?filter=past')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Agent Workshop')
        self.assertContains(response, 'Django Workshop')
        self.assertContains(response, 'MCP Workshop')

    def test_filter_by_python_tag(self):
        response = self.client.get('/events?filter=past&tag=python')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Agent Workshop')
        self.assertContains(response, 'Django Workshop')
        self.assertNotContains(response, 'MCP Workshop')

    def test_filter_by_agents_tag(self):
        response = self.client.get('/events?filter=past&tag=agents')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Agent Workshop')
        self.assertContains(response, 'MCP Workshop')
        self.assertNotContains(response, 'Django Workshop')

    def test_filter_by_nonexistent_tag(self):
        response = self.client.get('/events?filter=past&tag=nonexistent')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Agent Workshop')
        self.assertNotContains(response, 'Django Workshop')
        self.assertNotContains(response, 'MCP Workshop')

    def test_tag_links_in_listing(self):
        response = self.client.get('/events?filter=past')
        content = response.content.decode()
        self.assertIn('tag=python', content)
        self.assertIn('tag=agents', content)
        self.assertIn('tag=django', content)
        self.assertIn('tag=mcp', content)

    def test_current_tag_in_context(self):
        response = self.client.get('/events?filter=past&tag=python')
        self.assertEqual(response.context['current_tag'], 'python')

    def test_empty_tag_ignored(self):
        response = self.client.get('/events?filter=past&tag=')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Agent Workshop')
        self.assertContains(response, 'Django Workshop')
        self.assertContains(response, 'MCP Workshop')


# --- Pagination tests ---


class RecordingsListPaginationTest(TestCase):
    """Test pagination on /events?filter=past (20 per page)."""

    @classmethod
    def setUpTestData(cls):
        # Create 25 recordings to test pagination
        for i in range(25):
            _create_recording_event(
                f'recording-{i:02d}',
                title=f'Recording {i:02d}',
                description=f'Description {i}',
            )

    def test_first_page_has_20_items(self):
        response = self.client.get('/events?filter=past')
        self.assertEqual(response.status_code, 200)
        page_obj = response.context['page_obj']
        self.assertEqual(len(page_obj), 20)

    def test_second_page_has_remaining_items(self):
        response = self.client.get('/events?filter=past&page=2')
        self.assertEqual(response.status_code, 200)
        page_obj = response.context['page_obj']
        self.assertEqual(len(page_obj), 5)

    def test_pagination_controls_shown(self):
        response = self.client.get('/events?filter=past')
        content = response.content.decode()
        self.assertIn('Page 1 of 2', content)
        self.assertIn('Next', content)

    def test_previous_link_on_page_2(self):
        response = self.client.get('/events?filter=past&page=2')
        content = response.content.decode()
        self.assertIn('Previous', content)

    def test_no_pagination_when_under_20(self):
        Event.objects.all().delete()
        for i in range(5):
            _create_recording_event(f'small-recording-{i}')
        response = self.client.get('/events?filter=past')
        self.assertFalse(response.context['is_paginated'])

    def test_pagination_preserves_tag_filter(self):
        Event.objects.all().delete()
        for i in range(25):
            _create_recording_event(
                f'tagged-rec-{i:02d}',
                tags=['python'],
            )
        response = self.client.get('/events?filter=past&tag=python')
        content = response.content.decode()
        self.assertIn('tag=python', content)

    def test_invalid_page_number_shows_last(self):
        response = self.client.get('/events?filter=past&page=999')
        self.assertEqual(response.status_code, 200)
        page_obj = response.context['page_obj']
        self.assertEqual(page_obj.number, 2)


# --- Recordings list display tests ---


class RecordingsListDisplayTest(TestCase):
    """Test that recordings listing shows required fields."""

    @classmethod
    def setUpTestData(cls):
        cls.recording = _create_recording_event(
            'workshop-display',
            title='Workshop Display Test',
            description='Workshop description here',
            tags=['agents', 'python'],
            start_datetime=timezone.make_aware(timezone.datetime(2025, 7, 20, 12, 0)),
        )

    def test_shows_title(self):
        response = self.client.get('/events?filter=past')
        self.assertContains(response, 'Workshop Display Test')

    def test_shows_description(self):
        response = self.client.get('/events?filter=past')
        self.assertContains(response, 'Workshop description here')

    def test_shows_date(self):
        response = self.client.get('/events?filter=past')
        self.assertContains(response, 'July 20, 2025')

    def test_shows_tags(self):
        response = self.client.get('/events?filter=past')
        self.assertContains(response, 'agents')
        self.assertContains(response, 'python')

    def test_tags_are_clickable_links(self):
        response = self.client.get('/events?filter=past')
        content = response.content.decode()
        self.assertIn('href="/events?filter=past&amp;tag=agents"', content)
        self.assertIn('href="/events?filter=past&amp;tag=python"', content)

    # Listing-page lock-icon tests removed in #261 (covered by
    # `playwright_tests/test_event_recordings.py` and Rule 4).

    def test_empty_list_message(self):
        Event.objects.all().delete()
        response = self.client.get('/events?filter=past')
        self.assertContains(response, 'No recordings yet')

    def test_unpublished_not_shown(self):
        _create_recording_event('draft-recording', published=False)
        response = self.client.get('/events?filter=past')
        self.assertNotContains(response, 'Draft Recording')

    def test_event_without_recording_not_shown(self):
        Event.objects.create(
            title='No Recording Event', slug='no-rec-event',
            start_datetime=timezone.now(), status='completed',
            recording_url='', published=True,
        )
        response = self.client.get('/events?filter=past')
        self.assertNotContains(response, 'No Recording Event')


# --- Recording detail display tests ---


class RecordingDetailDisplayTest(TestCase):
    """Test the announcement-style event detail page for completed events.

    Issue #426 removed the inline recording UI. The page renders the title,
    description, date, and clickable tags; recording-specific fields (video
    player, materials, transcript, chapters, core tools, learning
    objectives, expected outcome) live on the linked Workshop's
    landing/video pages instead.
    """

    @classmethod
    def setUpTestData(cls):
        cls.recording = _create_recording_event(
            'detail-workshop',
            title='Detail Workshop',
            description='Workshop for detail testing',
            tags=['python', 'agents'],
            recording_url='https://youtube.com/watch?v=test123',
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
            start_datetime=timezone.make_aware(timezone.datetime(2025, 7, 20, 12, 0)),
        )

    def test_status_code_200(self):
        response = self.client.get('/events/detail-workshop')
        self.assertEqual(response.status_code, 200)

    def test_template_used(self):
        response = self.client.get('/events/detail-workshop')
        self.assertTemplateUsed(response, 'events/event_detail.html')

    def test_shows_title(self):
        response = self.client.get('/events/detail-workshop')
        self.assertContains(response, 'Detail Workshop')

    def test_shows_description(self):
        response = self.client.get('/events/detail-workshop')
        self.assertContains(response, 'Workshop for detail testing')

    def test_shows_date(self):
        response = self.client.get('/events/detail-workshop')
        self.assertContains(response, 'July 20, 2025')

    def test_shows_tags(self):
        response = self.client.get('/events/detail-workshop')
        self.assertContains(response, 'python')
        self.assertContains(response, 'agents')

    def test_tags_are_clickable_links(self):
        response = self.client.get('/events/detail-workshop')
        content = response.content.decode()
        self.assertIn('href="/events?filter=past&amp;tag=python"', content)
        self.assertIn('href="/events?filter=past&amp;tag=agents"', content)

    def test_omits_inline_recording_block(self):
        response = self.client.get('/events/detail-workshop')
        # No inline recording UI: no embed wrapper, no chapters, no
        # materials, no transcript, no core tools / learning objectives /
        # outcome sections.
        self.assertNotContains(
            response, 'data-testid="event-recording-block"',
        )
        self.assertNotContains(response, 'data-testid="video-chapters"')
        self.assertNotContains(response, 'class="video-timestamp')
        self.assertNotContains(
            response, 'data-testid="recording-materials"',
        )
        self.assertNotContains(response, 'Materials</h2>')
        self.assertNotContains(response, 'https://example.com/slides.pdf')
        self.assertNotContains(response, 'GitHub Repo')
        self.assertNotContains(response, 'Core Tools')
        self.assertNotContains(response, "What You'll Learn")
        self.assertNotContains(response, 'Build an API')
        self.assertNotContains(response, 'Expected Outcome')
        self.assertNotContains(response, 'A working API deployment')

    def test_404_for_nonexistent_slug(self):
        response = self.client.get('/events/nonexistent')
        self.assertEqual(response.status_code, 404)

    def test_draft_status_404(self):
        _create_recording_event('drafted-detail', status='draft')
        response = self.client.get('/events/drafted-detail')
        self.assertEqual(response.status_code, 404)

    def test_title_tag_format(self):
        response = self.client.get('/events/detail-workshop')
        content = response.content.decode()
        self.assertIn('<title>Detail Workshop | AI Shipping Labs</title>', content)


# --- Access control tests ---


class RecordingDetailAccessControlTest(TierSetupMixin, TestCase):
    """Detail page no longer enforces a recording paywall (issue #426).

    Recording playback gating (the per-tier matrix anonymous/free/basic/main
    /premium x open/gated recording) lives on the linked Workshop's video
    page. See ``content/tests/test_workshops.py::WorkshopSplitGatingTest``
    and ``playwright_tests/test_access_control.py`` for the canonical
    coverage. The event detail page is now an announcement page that does
    not embed the recording or its paywall, regardless of tier.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.open_recording = _create_recording_event(
            'open-recording',
            title='Open Recording',
            description='Open description',
            recording_url='https://youtube.com/watch?v=open',
            required_level=LEVEL_OPEN,
        )
        cls.gated_recording = _create_recording_event(
            'gated-recording',
            title='Gated Recording',
            description='Gated description',
            recording_url='https://youtube.com/watch?v=gated',
            materials=[{'title': 'Secret Slides', 'url': 'https://example.com/secret'}],
            required_level=LEVEL_MAIN,
        )

    def test_anonymous_open_recording_announcement_only(self):
        response = self.client.get('/events/open-recording')
        self.assertEqual(response.status_code, 200)
        # Announcement copy is visible.
        self.assertContains(response, 'Open description')
        # No inline player or recording paywall.
        self.assertNotContains(response, 'data-source="youtube"')
        self.assertNotContains(response, 'youtube.com/embed')
        self.assertNotContains(
            response, 'data-testid="event-recording-block"',
        )

    def test_anonymous_gated_recording_no_paywall_on_event_page(self):
        response = self.client.get('/events/gated-recording')
        self.assertEqual(response.status_code, 200)
        # No recording, no materials, no recording-specific paywall copy.
        self.assertNotContains(response, 'youtube.com/embed')
        self.assertNotContains(response, 'Secret Slides')
        self.assertNotContains(
            response, 'Upgrade to Main to watch this recording',
        )

    def test_main_user_gated_recording_no_inline_player(self):
        user = User.objects.create_user(email='main@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main@test.com', password='testpass')
        response = self.client.get('/events/gated-recording')
        # Even an authorized member sees no inline recording on the event
        # detail page; they reach the recording via the workshop CTA.
        self.assertNotContains(response, 'youtube.com/embed')
        self.assertNotContains(response, 'Secret Slides')
        self.assertNotContains(
            response, 'data-testid="event-recording-block"',
        )


# --- Conversions from playwright_tests/test_seo_tags.py (issue #256) ---


class RecordingTagFilterTest(TestCase):
    """Behaviour previously covered by Playwright Scenario 6 on
    /events?filter=past. Filtering happens via ?tag= and resolves
    server-side; no JS required.
    """

    def test_tag_filter_on_recordings(self):
        # Replaces playwright_tests/test_seo_tags.py::TestScenario6TagFiltersAcrossPages::test_tag_filter_on_recordings
        _create_recording_event(
            'python-recording', title='Python Recording',
            tags=['python'],
        )
        _create_recording_event(
            'go-recording', title='Go Recording',
            tags=['go'],
        )

        # The unfiltered listing surfaces a chip whose href triggers
        # the python filter.
        listing = self.client.get('/events?filter=past')
        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, 'tag=python')

        # Following ?tag=python: only the python recording remains.
        filtered = self.client.get('/events?filter=past&tag=python')
        self.assertEqual(filtered.status_code, 200)
        self.assertContains(filtered, 'Python Recording')
        self.assertNotContains(filtered, 'Go Recording')
