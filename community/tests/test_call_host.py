"""Tests for the CallHost model (#870)."""

from django.test import TestCase, tag

from community.models import CallHost


@tag('core')
class CallHostModelTest(TestCase):
    def test_is_available_truth_table(self):
        cases = [
            # (is_active, capacity, current_load, expected)
            (True, 5, 0, True),
            (True, 1, 1, False),   # capacity reached
            (True, 0, 0, False),   # zero capacity
            (False, 5, 0, False),  # inactive but with capacity
            (True, 5, 4, True),    # spare capacity
        ]
        for is_active, capacity, current_load, expected in cases:
            with self.subTest(is_active=is_active, capacity=capacity, load=current_load):
                host = CallHost(
                    slug='x', name='X', is_active=is_active,
                    capacity=capacity, current_load=current_load,
                )
                self.assertEqual(host.is_available, expected)

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
