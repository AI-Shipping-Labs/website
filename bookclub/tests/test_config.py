"""BOOK_CLUB_SLACK_URL is read through the IntegrationSetting framework."""

from django.test import TestCase

from bookclub.config import (
    BOOK_CLUB_SLACK_URL_DEFAULT,
    get_book_club_slack_url,
)
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from integrations.settings_registry import get_group_by_name


class BookClubSlackUrlConfigTest(TestCase):
    def setUp(self):
        clear_config_cache()
        self.addCleanup(clear_config_cache)
    def test_key_is_registered_in_the_slack_group(self):
        slack_group = get_group_by_name('slack')
        self.assertIsNotNone(slack_group)
        definition = next(
            (k for k in slack_group['keys'] if k['key'] == 'BOOK_CLUB_SLACK_URL'),
            None,
        )
        self.assertIsNotNone(definition)
        self.assertIn('description', definition)
        self.assertIn('docs_url', definition)

    def test_defaults_to_account_when_unset(self):
        self.assertEqual(get_book_club_slack_url(), BOOK_CLUB_SLACK_URL_DEFAULT)

    def test_db_override_wins(self):
        IntegrationSetting.objects.create(
            key='BOOK_CLUB_SLACK_URL',
            value='https://acme.slack.com/archives/C0BOOKCLUB',
        )
        clear_config_cache()
        self.assertEqual(
            get_book_club_slack_url(),
            'https://acme.slack.com/archives/C0BOOKCLUB',
        )
