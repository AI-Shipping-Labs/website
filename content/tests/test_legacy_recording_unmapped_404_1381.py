"""Relocated unmapped legacy-recording 404 owner (#1481)."""

from django.test import TestCase

from content.models import Workshop
from events.models import Event


class UnmappedLegacyRecording404Test(TestCase):
    """Owns genuine 404 for unmapped `/event-recordings/<slug>` paths.

    Relocated from Playwright
    ``test_unmapped_legacy_recording_is_a_genuine_404``.
    """

    def test_unmapped_legacy_recording_is_a_genuine_404(self):
        Workshop.objects.all().delete()
        Event.objects.all().delete()

        response = self.client.get('/event-recordings/does-not-exist')

        self.assertEqual(response.status_code, 404)
        self.assertNotIn('Location', response)
