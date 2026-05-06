"""YouTube description footer must use the resolved ``SITE_BASE_URL``
(DB override > env) per issue #435.
"""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from events.models import Event
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from jobs.tasks.youtube_upload import _build_description


@override_settings(SITE_BASE_URL='https://env.example.com')
class YouTubeDescriptionFooterTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.event = Event.objects.create(
            title='Footer Workshop',
            slug='footer-workshop',
            description='An event for testing.',
            start_datetime=timezone.now() - timedelta(hours=2),
            end_datetime=timezone.now() - timedelta(hours=1),
            status='completed',
        )

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def test_description_footer_uses_db_override(self):
        IntegrationSetting.objects.create(
            key='SITE_BASE_URL',
            value='https://override.example.com',
            group='site',
        )
        clear_config_cache()
        description = _build_description(self.event)
        # Footer is the last line of the description block.
        self.assertEqual(
            description.splitlines()[-1],
            'AI Shipping Labs - https://override.example.com',
        )
        self.assertNotIn('env.example.com', description)

    def test_description_footer_falls_back_to_settings(self):
        # No DB row => env value used. Regression guard.
        description = _build_description(self.event)
        self.assertEqual(
            description.splitlines()[-1],
            'AI Shipping Labs - https://env.example.com',
        )
