"""Tests for Slack announcement posting."""

from datetime import date
from unittest.mock import MagicMock, patch

import requests
from django.test import TestCase, override_settings

from content.models import Article, Workshop
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from notifications.services.slack_announcements import (
    _build_slack_blocks,
    post_slack_announcement,
)


class BuildSlackBlocksTest(TestCase):
    """Tests for Slack Block Kit message building."""

    def test_builds_article_blocks(self):
        article = Article.objects.create(
            title='Test Article', slug='test-article',
            date=date(2025, 1, 1), description='A great article.',
        )
        text, blocks = _build_slack_blocks('article', article)
        self.assertIn('New article', text)
        self.assertIn('Test Article', text)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]['type'], 'section')
        self.assertEqual(blocks[1]['type'], 'actions')

    def test_blocks_contain_mrkdwn(self):
        article = Article.objects.create(
            title='Test', slug='test-mrkdwn',
            date=date(2025, 1, 1), description='Desc.',
        )
        _, blocks = _build_slack_blocks('article', article)
        section = blocks[0]
        self.assertEqual(section['text']['type'], 'mrkdwn')
        self.assertIn('*New article:*', section['text']['text'])
        self.assertIn('Desc.', section['text']['text'])

    def test_blocks_contain_button(self):
        article = Article.objects.create(
            title='Test', slug='test-button',
            date=date(2025, 1, 1),
        )
        _, blocks = _build_slack_blocks('article', article)
        button = blocks[1]['elements'][0]
        self.assertEqual(button['type'], 'button')
        self.assertIn('/blog/test-button', button['url'])

    def test_truncates_long_description(self):
        article = Article.objects.create(
            title='Test', slug='test-truncate',
            date=date(2025, 1, 1),
            description='x' * 300,
        )
        _, blocks = _build_slack_blocks('article', article)
        text = blocks[0]['text']['text']
        self.assertIn('...', text)

    def test_build_slack_blocks_workshop_label(self):
        """text_fallback starts with 'New workshop:' and mrkdwn_text starts
        with '*New workshop:*' (issue #647)."""
        workshop = Workshop.objects.create(
            title='RAG Bootcamp', slug='rag-bootcamp',
            date=date(2026, 1, 1), status='published',
            description='Hands-on RAG.',
            landing_required_level=0,
            pages_required_level=10,
            recording_required_level=20,
        )
        text_fallback, blocks = _build_slack_blocks('workshop', workshop)
        self.assertEqual(text_fallback, 'New workshop: RAG Bootcamp')
        self.assertTrue(
            blocks[0]['text']['text'].startswith('*New workshop:*'),
            f'mrkdwn_text should start with *New workshop:* but was: '
            f'{blocks[0]["text"]["text"]!r}',
        )


@override_settings(SLACK_ENABLED=True)
class PostSlackAnnouncementTest(TestCase):
    """Tests for the post_slack_announcement function."""

    def setUp(self):
        clear_config_cache()

    def test_skips_when_no_bot_token(self):
        article = Article.objects.create(
            title='Test', slug='test-no-token',
            date=date(2025, 1, 1),
        )
        with self.settings(
            SLACK_ENVIRONMENT='production',
            SLACK_BOT_TOKEN='',
            SLACK_ANNOUNCEMENTS_CHANNEL_ID='C123',
        ):
            result = post_slack_announcement('article', article)
            self.assertFalse(result)

    def test_skips_when_no_channel_id(self):
        article = Article.objects.create(
            title='Test', slug='test-no-channel',
            date=date(2025, 1, 1),
        )
        with self.settings(
            SLACK_ENVIRONMENT='production',
            SLACK_BOT_TOKEN='xoxb-test',
            SLACK_ANNOUNCEMENTS_CHANNEL_ID='',
        ):
            result = post_slack_announcement('article', article)
            self.assertFalse(result)

    @patch('notifications.services.slack_announcements.requests.post')
    def test_development_without_dev_channel_skips(self, mock_post):
        article = Article.objects.create(
            title='Dev Skip Article', slug='test-dev-skip',
            date=date(2025, 1, 1),
        )
        with self.settings(
            SLACK_ENVIRONMENT='development',
            SLACK_BOT_TOKEN='xoxb-test',
            SLACK_ANNOUNCEMENTS_CHANNEL_ID='CPROD',
            SLACK_DEV_ANNOUNCEMENTS_CHANNEL_ID='',
        ):
            result = post_slack_announcement('article', article)

        self.assertFalse(result)
        mock_post.assert_not_called()

    @patch('notifications.services.slack_announcements.requests.post')
    def test_development_posts_to_dev_channel(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {'ok': True}
        mock_post.return_value = mock_response

        article = Article.objects.create(
            title='Dev Posted Article', slug='test-dev-post',
            date=date(2025, 1, 1),
        )
        with self.settings(
            SLACK_ENVIRONMENT='development',
            SLACK_BOT_TOKEN='xoxb-test',
            SLACK_ANNOUNCEMENTS_CHANNEL_ID='CPROD',
            SLACK_DEV_ANNOUNCEMENTS_CHANNEL_ID='CDEV',
        ):
            result = post_slack_announcement('article', article)

        self.assertTrue(result)
        self.assertEqual(mock_post.call_args.kwargs['json']['channel'], 'CDEV')

    @patch('notifications.services.slack_announcements.requests.post')
    def test_posts_to_slack_api(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {'ok': True}
        mock_post.return_value = mock_response

        article = Article.objects.create(
            title='Posted Article', slug='test-post',
            date=date(2025, 1, 1),
        )
        with self.settings(
            SLACK_ENVIRONMENT='production',
            SLACK_BOT_TOKEN='xoxb-test',
            SLACK_ANNOUNCEMENTS_CHANNEL_ID='C123',
        ):
            result = post_slack_announcement('article', article)

        self.assertTrue(result)
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        self.assertIn('C123', call_kwargs.kwargs['json']['channel'])

    @patch('notifications.services.slack_announcements.requests.post')
    def test_returns_false_on_slack_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {'ok': False, 'error': 'channel_not_found'}
        mock_post.return_value = mock_response

        article = Article.objects.create(
            title='Error Article', slug='test-error',
            date=date(2025, 1, 1),
        )
        with (
            self.settings(
                SLACK_ENVIRONMENT='production',
                SLACK_BOT_TOKEN='xoxb-test',
                SLACK_ANNOUNCEMENTS_CHANNEL_ID='C123',
            ),
            self.assertLogs('notifications.services.slack_announcements', level='WARNING'),
        ):
            result = post_slack_announcement('article', article)

        self.assertFalse(result)

    @patch('notifications.services.slack_announcements.requests.post')
    def test_returns_false_on_exception(self, mock_post):
        mock_post.side_effect = requests.exceptions.Timeout('Network error')
        article = Article.objects.create(
            title='Exception Article', slug='test-exception',
            date=date(2025, 1, 1),
        )
        with (
            self.settings(
                SLACK_ENVIRONMENT='production',
                SLACK_BOT_TOKEN='xoxb-test',
                SLACK_ANNOUNCEMENTS_CHANNEL_ID='C123',
            ),
            self.assertLogs('notifications.services.slack_announcements', level='ERROR'),
        ):
            result = post_slack_announcement('article', article)

        self.assertFalse(result)

    @patch('notifications.services.slack_announcements.requests.post')
    def test_returns_false_on_invalid_json(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.side_effect = ValueError('invalid json')
        mock_post.return_value = mock_response

        article = Article.objects.create(
            title='Invalid Json Article',
            slug='test-invalid-json',
            date=date(2025, 1, 1),
        )
        with (
            self.settings(
                SLACK_ENVIRONMENT='production',
                SLACK_BOT_TOKEN='xoxb-test',
                SLACK_ANNOUNCEMENTS_CHANNEL_ID='C123',
            ),
            self.assertLogs(
                'notifications.services.slack_announcements',
                level='ERROR',
            ) as logs,
        ):
            result = post_slack_announcement('article', article)

        self.assertFalse(result)
        self.assertIn(
            'Slack announcement returned invalid JSON for article',
            '\n'.join(logs.output),
        )

    @patch('notifications.services.slack_announcements.requests.post')
    def test_returns_false_on_malformed_json_shape(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_post.return_value = mock_response

        article = Article.objects.create(
            title='Malformed Json Article',
            slug='test-malformed-json',
            date=date(2025, 1, 1),
        )
        with (
            self.settings(
                SLACK_ENVIRONMENT='production',
                SLACK_BOT_TOKEN='xoxb-test',
                SLACK_ANNOUNCEMENTS_CHANNEL_ID='C123',
            ),
            self.assertLogs(
                'notifications.services.slack_announcements',
                level='WARNING',
            ) as logs,
        ):
            result = post_slack_announcement('article', article)

        self.assertFalse(result)
        self.assertIn(
            'Slack announcement returned malformed JSON for article',
            '\n'.join(logs.output),
        )


@override_settings(SITE_BASE_URL='https://env.example.com')
class SlackAnnouncementSiteUrlOverrideTest(TestCase):
    """Slack announcement URLs respect the Studio override (issue #435)."""

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def test_announcement_blocks_use_db_override_for_content_url(self):
        IntegrationSetting.objects.create(
            key='SITE_BASE_URL',
            value='https://override.example.com',
            group='site',
        )
        clear_config_cache()
        article = Article.objects.create(
            title='Override Slack', slug='override-slack',
            date=date(2025, 1, 1),
        )
        _, blocks = _build_slack_blocks('article', article)
        button = blocks[1]['elements'][0]
        self.assertEqual(
            button['url'],
            f'https://override.example.com{article.get_absolute_url()}',
        )
        self.assertNotIn(
            'env.example.com', blocks[0]['text']['text'],
        )

    def test_announcement_blocks_fall_back_to_settings(self):
        article = Article.objects.create(
            title='Env Slack', slug='env-slack',
            date=date(2025, 1, 1),
        )
        _, blocks = _build_slack_blocks('article', article)
        button = blocks[1]['elements'][0]
        self.assertEqual(
            button['url'],
            f'https://env.example.com{article.get_absolute_url()}',
        )
