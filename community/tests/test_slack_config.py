from django.test import SimpleTestCase, override_settings

from community.slack_config import (
    get_slack_announcements_channel_id,
    get_slack_community_channel_ids,
    get_slack_environment,
    slack_api_enabled,
)
from integrations.config import clear_config_cache


class SlackConfigTest(SimpleTestCase):
    def setUp(self):
        clear_config_cache()

    @override_settings(SLACK_ENVIRONMENT="production")
    def test_production_uses_production_channels(self):
        with self.settings(
            SLACK_ANNOUNCEMENTS_CHANNEL_ID="CPROD",
            SLACK_COMMUNITY_CHANNEL_IDS=["CPROD1", "CPROD2"],
        ):
            self.assertEqual(get_slack_environment(), "production")
            self.assertEqual(get_slack_announcements_channel_id(), "CPROD")
            self.assertEqual(get_slack_community_channel_ids(), ["CPROD1", "CPROD2"])

    @override_settings(SLACK_ENVIRONMENT="development")
    def test_development_ignores_production_channels(self):
        with self.settings(
            SLACK_ANNOUNCEMENTS_CHANNEL_ID="CPROD",
            SLACK_COMMUNITY_CHANNEL_IDS=["CPROD1"],
            SLACK_DEV_ANNOUNCEMENTS_CHANNEL_ID="CDEV",
            SLACK_DEV_COMMUNITY_CHANNEL_IDS=["CDEV1"],
        ):
            self.assertEqual(get_slack_announcements_channel_id(), "CDEV")
            self.assertEqual(get_slack_community_channel_ids(), ["CDEV1"])

    @override_settings(
        SLACK_ENVIRONMENT="development",
        SLACK_ANNOUNCEMENTS_CHANNEL_ID="CPROD",
        SLACK_COMMUNITY_CHANNEL_IDS=["CPROD1"],
        SLACK_DEV_ANNOUNCEMENTS_CHANNEL_ID="",
        SLACK_DEV_COMMUNITY_CHANNEL_IDS=[],
    )
    def test_development_without_overrides_is_silent(self):
        self.assertEqual(get_slack_announcements_channel_id(), "")
        self.assertEqual(get_slack_community_channel_ids(), [])

    @override_settings(
        SLACK_ENVIRONMENT="test",
        SLACK_TEST_ANNOUNCEMENTS_CHANNEL_ID="CTEST",
        SLACK_TEST_COMMUNITY_CHANNEL_IDS="CTEST1, CTEST2",
    )
    def test_test_environment_uses_test_channels(self):
        self.assertEqual(get_slack_announcements_channel_id(), "CTEST")
        self.assertEqual(get_slack_community_channel_ids(), ["CTEST1", "CTEST2"])

    @override_settings(SLACK_ENVIRONMENT="staging")
    def test_unknown_environment_falls_back_to_development(self):
        self.assertEqual(get_slack_environment(), "development")

    @override_settings(SLACK_ENABLED=False, SLACK_BOT_TOKEN="xoxb-test")
    def test_slack_api_enabled_requires_kill_switch(self):
        self.assertFalse(slack_api_enabled())

    @override_settings(SLACK_ENABLED=True, SLACK_BOT_TOKEN="")
    def test_slack_api_enabled_requires_token(self):
        self.assertFalse(slack_api_enabled())

    @override_settings(SLACK_ENABLED=True, SLACK_BOT_TOKEN="xoxb-test")
    def test_slack_api_enabled_accepts_enabled_with_token(self):
        self.assertTrue(slack_api_enabled())
