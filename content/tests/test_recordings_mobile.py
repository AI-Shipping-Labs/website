"""Tests for recordings mobile responsive fixes - issue #178.

Covers:
- Recording list: arrow icon hidden on mobile (has `hidden sm:block` classes)
- Recording list: cards use min-w-0 to prevent overflow
- Pagination controls have min-h-[44px] for tap targets and flex-wrap

Issue #426 retired the inline event-detail recording UI, so the
materials-list mobile coverage that lived here moved to the workshop
video page (see ``content/tests/test_workshops_public.py``).
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from events.models import Event


def _create_recording(slug, **kwargs):
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


class RecordingListArrowHiddenOnMobileTest(TestCase):
    """Arrow icons on recording list cards should be hidden on mobile."""

    @classmethod
    def setUpTestData(cls):
        cls.recording = _create_recording('arrow-rec-test')

    def test_arrow_icon_hidden_on_mobile(self):
        response = self.client.get('/events?filter=past')
        content = response.content.decode()
        self.assertIn('data-lucide="arrow-right"', content)
        self.assertIn('hidden sm:block', content)

    def test_arrow_icon_has_flex_shrink_0(self):
        response = self.client.get('/events?filter=past')
        content = response.content.decode()
        self.assertIn('flex-shrink-0', content)


class RecordingListMinWidth0Test(TestCase):
    """Recording list card content div should have min-w-0 to prevent flex overflow."""

    @classmethod
    def setUpTestData(cls):
        cls.recording = _create_recording(
            'long-title-rec',
            title='A' * 100,
            description='B' * 200,
        )

    def test_card_content_has_min_w_0(self):
        response = self.client.get('/events?filter=past')
        content = response.content.decode()
        self.assertIn('min-w-0', content)

    def test_page_renders_with_long_content(self):
        response = self.client.get('/events?filter=past')
        self.assertEqual(response.status_code, 200)


class RecordingDetailMaterialTapTargetTest(TestCase):
    """Issue #1037: event detail renders structured material resource links.

    The legacy inline recording materials list remains retired, but
    standalone past events can show explicit materials as external resource
    links with stable tap targets.
    """

    @classmethod
    def setUpTestData(cls):
        cls.recording = _create_recording(
            'material-tap-test',
            materials=[
                {'title': 'GitHub Repo', 'url': 'https://github.com/test', 'type': 'code'},
                {'title': 'Slides', 'url': 'https://example.com/slides', 'type': 'slides'},
            ],
        )

    def test_event_detail_renders_structured_material_links(self):
        response = self.client.get(self.recording.get_absolute_url())
        content = response.content.decode()
        self.assertIn('data-testid="event-post-resources"', content)
        self.assertIn('data-testid="event-material-resource"', content)
        self.assertIn('min-h-[52px]', content)
        self.assertIn('https://github.com/test', content)
        self.assertIn('https://example.com/slides', content)
        # The old recording materials partial remains absent.
        self.assertNotIn('data-testid="recording-materials"', content)
        self.assertNotIn('Materials</h2>', content)


class RecordingPaginationMobileTest(TestCase):
    """Pagination controls should be tappable on mobile."""

    @classmethod
    def setUpTestData(cls):
        # Create 25 recordings to trigger pagination (20 per page)
        for i in range(25):
            _create_recording(
                f'pagination-rec-{i:02d}',
                start_datetime=timezone.now() - timedelta(days=i + 1),
            )

    def test_pagination_has_flex_wrap(self):
        response = self.client.get('/events?filter=past')
        content = response.content.decode()
        # Pagination nav should use flex-wrap for narrow screens
        self.assertIn('flex-wrap', content)

    def test_pagination_links_have_min_height(self):
        response = self.client.get('/events?filter=past')
        content = response.content.decode()
        # Pagination links should have min-h-[44px] for tap targets
        self.assertIn('min-h-[44px]', content)
