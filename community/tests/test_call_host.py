"""Tests for the CallHost model (#870)."""

from django.test import TestCase, tag

from community.models import CallHost


@tag('core')
class CallHostModelTest(TestCase):
    def test_is_available_truth_table(self):
        cases = [
            # (is_active, booking_url, capacity, current_load, expected)
            (True, 'https://example.com/book', 5, 0, True),
            (True, 'https://example.com/book', 1, 1, True),
            (True, 'https://example.com/book', 0, 99, True),
            (False, 'https://example.com/book', 5, 0, False),
            (True, '', 5, 0, False),
            (True, '   ', 5, 0, False),
            (True, 'javascript:alert(1)', 5, 0, False),
            (True, 'ftp://example.com/book', 5, 0, False),
            (True, 'https://example.com/has space', 5, 0, False),
        ]
        for is_active, booking_url, capacity, current_load, expected in cases:
            with self.subTest(is_active=is_active, booking_url=booking_url):
                host = CallHost(
                    slug='x', name='X', is_active=is_active,
                    booking_url=booking_url,
                    capacity=capacity,
                    current_load=current_load,
                )
                self.assertEqual(host.is_available, expected)
                self.assertEqual(
                    host.usable_booking_url,
                    booking_url if booking_url == 'https://example.com/book' else '',
                )

    def test_model_uses_call_profile_verbose_names(self):
        self.assertEqual(CallHost._meta.verbose_name, 'Call profile')
        self.assertEqual(CallHost._meta.verbose_name_plural, 'Call profiles')

    def test_display_photo_url_prefers_configured_photo(self):
        host = CallHost(slug='alexey', name='Alexey', photo_url='https://cdn.example/a.png')
        self.assertEqual(host.display_photo_url, 'https://cdn.example/a.png')

    def test_display_photo_url_falls_back_to_valeria_static_asset(self):
        # Valeria's slug is "valeria" but the static file is "valeriia.png".
        host = CallHost(slug='valeria', name='Valeriia', photo_url='')
        self.assertTrue(host.display_photo_url.endswith('valeriia.png'))

    def test_display_photo_url_falls_back_to_slug_static_asset(self):
        host = CallHost(slug='alexey', name='Alexey', photo_url='')
        self.assertTrue(host.display_photo_url.endswith('alexey.png'))


@tag('core')
class CallHostSeedTest(TestCase):
    """The migration seeds Alexey and Valeria."""

    def test_seeded_hosts_exist(self):
        slugs = set(CallHost.objects.values_list('slug', flat=True))
        self.assertIn('alexey', slugs)
        self.assertIn('valeria', slugs)

    def test_valeria_seeded_with_google_booking_link(self):
        valeria = CallHost.objects.get(slug='valeria')
        self.assertEqual(
            valeria.booking_url,
            'https://calendar.app.google/Rh5oWPU9ZAuuDLPt9',
        )
