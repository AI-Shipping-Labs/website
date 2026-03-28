"""Tests for curated links (collection) mobile responsive fixes - issue #178.

Covers:
- Gated link CTA has overflow-hidden and break-words
- Gated link cards use smaller padding on mobile (p-4 sm:p-6)
- Accessible link cards also use responsive padding
"""

from django.test import TestCase

from content.models import CuratedLink


class CollectionGatedCtaOverflowTest(TestCase):
    """Gated link CTA section should not overflow on 375px viewport."""

    @classmethod
    def setUpTestData(cls):
        # Create a gated link (required_level > 0)
        cls.gated_link = CuratedLink.objects.create(
            item_id='gated-mobile-test',
            title='Gated Link For Mobile Test',
            description='A test description for a gated link',
            url='https://example.com/gated',
            category='tools',
            required_level=1,
            published=True,
        )
        # Create a free link for comparison
        cls.free_link = CuratedLink.objects.create(
            item_id='free-mobile-test',
            title='Free Link For Mobile Test',
            description='A test description for a free link',
            url='https://example.com/free',
            category='tools',
            required_level=0,
            published=True,
        )

    def test_gated_card_has_overflow_hidden(self):
        response = self.client.get('/collection')
        content = response.content.decode()
        # Gated link cards should have overflow-hidden
        gated_pos = content.index('class="gated-link')
        gated_section = content[gated_pos:gated_pos + 500]
        self.assertIn('overflow-hidden', gated_section)

    def test_gated_cta_has_overflow_hidden(self):
        response = self.client.get('/collection')
        content = response.content.decode()
        # The gated-cta div (class="gated-cta ...) should have overflow-hidden
        cta_pos = content.index('class="gated-cta')
        cta_section = content[cta_pos:cta_pos + 300]
        self.assertIn('overflow-hidden', cta_section)

    def test_gated_cta_message_has_break_words(self):
        response = self.client.get('/collection')
        content = response.content.decode()
        # CTA message text should have break-words
        cta_pos = content.index('class="gated-cta')
        cta_section = content[cta_pos:cta_pos + 300]
        self.assertIn('break-words', cta_section)

    def test_cards_use_responsive_padding(self):
        response = self.client.get('/collection')
        content = response.content.decode()
        # Cards should use p-4 sm:p-6 for responsive padding
        self.assertIn('p-4 sm:p-6', content)

    def test_page_renders_200(self):
        response = self.client.get('/collection')
        self.assertEqual(response.status_code, 200)
