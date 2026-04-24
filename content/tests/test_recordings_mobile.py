"""Tests for recordings mobile responsive fixes - issue #178.

Covers:
- Recording list: arrow icon hidden on mobile (has `hidden sm:block` classes)
- Recording list: cards use min-w-0 to prevent overflow
- Recording detail: material links have adequate tap targets (min-h-[44px])
- Recording detail: material trailing icon hidden on mobile
- Pagination controls have min-h-[44px] for tap targets and flex-wrap
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
    """Material links should have adequate tap targets on mobile."""

    @classmethod
    def setUpTestData(cls):
        cls.recording = _create_recording(
            'material-tap-test',
            materials=[
                {'title': 'GitHub Repo', 'url': 'https://github.com/test', 'type': 'code'},
                {'title': 'Slides', 'url': 'https://example.com/slides', 'type': 'slides'},
            ],
        )

    def test_material_links_have_min_height(self):
        response = self.client.get('/events/material-tap-test')
        content = response.content.decode()
        # Material links should have min-h-[44px]
        self.assertIn('min-h-[44px]', content)

    def test_material_trailing_icon_hidden_on_mobile(self):
        response = self.client.get('/events/material-tap-test')
        content = response.content.decode()
        # The trailing external-link icon in materials should be hidden on mobile
        # Find material section
        materials_start = content.index('Materials')
        materials_section = content[materials_start:materials_start + 2000]
        self.assertIn('hidden sm:block', materials_section)

    def test_material_title_has_truncate(self):
        response = self.client.get('/events/material-tap-test')
        content = response.content.decode()
        # Material title should have truncate class to prevent overflow
        self.assertIn('truncate', content)


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
