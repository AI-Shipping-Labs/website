"""Tests for boolean integration setting rendering and save round-trip (issue #340).

Two keys in the integration registry are booleans stored as the literal
strings ``"true"`` / ``"false"`` — ``SES_WEBHOOK_VALIDATION_ENABLED`` and
``SLACK_ENABLED``. The studio settings form renders them as real HTML
checkboxes so the operator cannot typo ``True``, ``1`` or ``yes``. The
save view normalises the POST payload back to the same lowercase strings
so all existing parsers keep working.
"""

import re

from django.contrib.auth import get_user_model
from django.test import TestCase

from integrations.models import IntegrationSetting

User = get_user_model()


class BooleanSettingRoundTripTest(TestCase):
    """Boolean keys round-trip through the settings form as ``"true"``/``"false"``."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='admin@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='admin@test.com', password='testpass')

    def test_checkbox_checked_stores_true(self):
        self.client.post('/studio/settings/slack/save/', {
            'SLACK_ENABLED': 'true',
            'SLACK_BOT_TOKEN': 'xoxb-test',
        })
        self.assertEqual(
            IntegrationSetting.objects.get(key='SLACK_ENABLED').value,
            'true',
        )

    def test_checkbox_unchecked_stores_false_not_empty(self):
        # Pre-existing "true" row simulates the operator unticking a
        # previously enabled flag — the unchecked HTML checkbox sends no
        # SLACK_ENABLED key at all, and the save view must normalise that
        # absence to "false" rather than empty string.
        IntegrationSetting.objects.create(
            key='SLACK_ENABLED', value='true', is_secret=False, group='slack',
        )
        self.client.post('/studio/settings/slack/save/', {
            # SLACK_ENABLED deliberately absent.
            'SLACK_BOT_TOKEN': 'xoxb-test',
        })
        self.assertEqual(
            IntegrationSetting.objects.get(key='SLACK_ENABLED').value,
            'false',
        )

    def test_dashboard_renders_checkbox_for_boolean_key(self):
        response = self.client.get('/studio/settings/')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()

        # The SLACK_ENABLED input must be a checkbox carrying value="true".
        slack_enabled = re.search(
            r'<input[^>]*name="SLACK_ENABLED"[^>]*>',
            body,
        )
        self.assertIsNotNone(slack_enabled)
        self.assertIn('type="checkbox"', slack_enabled.group(0))
        self.assertIn('value="true"', slack_enabled.group(0))

        # Non-boolean SLACK_BOT_TOKEN in the same group must NOT render as a checkbox.
        slack_bot_token = re.search(
            r'<input[^>]*name="SLACK_BOT_TOKEN"[^>]*>',
            body,
        )
        self.assertIsNotNone(slack_bot_token)
        self.assertNotIn('type="checkbox"', slack_bot_token.group(0))

    def test_stored_mixed_case_true_renders_checked(self):
        IntegrationSetting.objects.create(
            key='SLACK_ENABLED', value='True', is_secret=False, group='slack',
        )
        response = self.client.get('/studio/settings/')
        # Locate the SLACK_ENABLED input element and confirm it carries the
        # ``checked`` attribute, demonstrating the case-insensitive match.
        match = re.search(
            r'<input[^>]*name="SLACK_ENABLED"[^>]*>',
            response.content.decode(),
        )
        self.assertIsNotNone(match)
        self.assertIn('checked', match.group(0))


class EmailSesSenderSettingsTest(TestCase):
    """Email (SES) exposes separate sender fields for both email classes."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='ses-admin@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='ses-admin@test.com', password='testpass')

    def test_dashboard_renders_transactional_and_promotional_sender_fields(self):
        response = self.client.get('/studio/settings/')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()

        self.assertIn('name="SES_TRANSACTIONAL_FROM_EMAIL"', body)
        self.assertIn('name="SES_PROMOTIONAL_FROM_EMAIL"', body)
        self.assertIn('required account and service email', body)
        self.assertIn('campaigns, newsletters, and marketing email', body)
        self.assertIn('Must be verified in SES', body)
