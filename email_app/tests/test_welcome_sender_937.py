"""Sender-resolution tests for the dedicated welcome@ sender (issue #937).

Welcome emails keep their transactional classification and delivery
semantics; only the From address is overridden to a configurable
``welcome@`` sender. These tests pin both the unchanged classification and
the new per-type sender resolution, including DB-override behaviour.
"""

from django.test import TestCase

from email_app.services.email_classification import (
    EMAIL_KIND_TRANSACTIONAL,
    TRANSACTIONAL_EMAIL_TYPES,
    WELCOME_EMAIL_TYPES,
    classify_email_type,
    get_sender_for_email_type,
)
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting

WELCOME_TYPES = [
    'welcome',
    'cofounder_welcome',
    'basic_welcome',
    'premium_welcome',
    'welcome_imported',
]


class WelcomeClassificationTest(TestCase):
    """Welcome types must stay classified transactional so unsubscribed
    paid users still receive them and they carry no unsubscribe footer."""

    def test_welcome_email_types_set_is_exact(self):
        self.assertEqual(WELCOME_EMAIL_TYPES, set(WELCOME_TYPES))

    def test_welcome_email_types_subset_of_transactional(self):
        # Invariant: dropping a welcome type from TRANSACTIONAL_EMAIL_TYPES
        # would silently strip its transactional delivery semantics.
        self.assertTrue(WELCOME_EMAIL_TYPES <= TRANSACTIONAL_EMAIL_TYPES)

    def test_welcome_types_still_classified_transactional(self):
        for email_type in WELCOME_TYPES:
            with self.subTest(email_type=email_type):
                self.assertEqual(
                    classify_email_type(email_type),
                    EMAIL_KIND_TRANSACTIONAL,
                )


class WelcomeSenderResolutionTest(TestCase):
    """``get_sender_for_email_type`` routes welcome types to welcome@ and
    leaves every other type resolving exactly as before."""

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def test_welcome_types_resolve_to_welcome_sender_by_default(self):
        for email_type in WELCOME_TYPES:
            with self.subTest(email_type=email_type):
                self.assertEqual(
                    get_sender_for_email_type(email_type),
                    'welcome@aishippinglabs.com',
                )

    def test_non_welcome_transactional_resolves_to_noreply(self):
        self.assertEqual(
            get_sender_for_email_type('password_reset'),
            'noreply@aishippinglabs.com',
        )
        self.assertEqual(
            get_sender_for_email_type('event_registration'),
            'noreply@aishippinglabs.com',
        )

    def test_promotional_resolves_to_content(self):
        self.assertEqual(
            get_sender_for_email_type('campaign'),
            'content@aishippinglabs.com',
        )

    def test_db_override_changes_welcome_sender(self):
        setting = IntegrationSetting.objects.create(
            key='SES_WELCOME_FROM_EMAIL',
            value='hello@aishippinglabs.com',
            group='ses',
        )
        clear_config_cache()
        self.assertEqual(
            get_sender_for_email_type('welcome'),
            'hello@aishippinglabs.com',
        )

        setting.delete()
        clear_config_cache()
        self.assertEqual(
            get_sender_for_email_type('welcome'),
            'welcome@aishippinglabs.com',
        )

    def test_db_override_does_not_affect_other_senders(self):
        IntegrationSetting.objects.create(
            key='SES_WELCOME_FROM_EMAIL',
            value='hello@aishippinglabs.com',
            group='ses',
        )
        clear_config_cache()

        self.assertEqual(
            get_sender_for_email_type('password_reset'),
            'noreply@aishippinglabs.com',
        )
        self.assertEqual(
            get_sender_for_email_type('campaign'),
            'content@aishippinglabs.com',
        )

    def test_transactional_and_promotional_overrides_still_work(self):
        # Regression: the existing two senders remain editable the same way.
        IntegrationSetting.objects.create(
            key='SES_TRANSACTIONAL_FROM_EMAIL',
            value='svc@aishippinglabs.com',
            group='ses',
        )
        IntegrationSetting.objects.create(
            key='SES_PROMOTIONAL_FROM_EMAIL',
            value='news@aishippinglabs.com',
            group='ses',
        )
        clear_config_cache()

        self.assertEqual(
            get_sender_for_email_type('password_reset'),
            'svc@aishippinglabs.com',
        )
        self.assertEqual(
            get_sender_for_email_type('campaign'),
            'news@aishippinglabs.com',
        )
