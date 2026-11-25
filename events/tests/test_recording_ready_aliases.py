"""Recording-ready recipient identity deduplicates aliases and historical sends."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from email_app.models import EmailLog
from events.models import EventHost, Host
from events.services.recording_ready_notification import (
    EMAIL_TYPE,
    notify_recording_ready,
    resolve_recording_ready_recipients,
)
from events.tests.test_recording_ready_notification import make_event


class RecordingReadyAliasIdentityTest(TestCase):
    def setUp(self):
        from accounts.models import EmailAlias

        self.user = get_user_model().objects.create_user(email='person@example.com')
        EmailAlias.objects.create(user=self.user, email='forward@example.org')
        self.event = make_event(host_email=self.user.email)
        host = Host.objects.create(name='Same host', slug='alias-host', email='forward@example.org')
        EventHost.objects.create(event=self.event, host=host)

    def test_delivery_aliases_receive_one_copy_for_the_canonical_person(self):
        recipients = resolve_recording_ready_recipients(self.event)
        self.assertEqual(len(recipients), 1)
        self.assertEqual(recipients[0].email, 'person@example.com')
        self.assertEqual(recipients[0].user, self.user)

    @patch('events.services.recording_ready_notification.submit_event_notice_campaign')
    def test_alias_only_historical_log_prevents_new_primary_copy(self, submit):
        EmailLog.objects.create(
            event=self.event, recipient_email='forward@example.org',
            email_type=EMAIL_TYPE, subject='Earlier notice', user=None,
        )
        result = notify_recording_ready(self.event)
        self.assertEqual(result['skipped_reason'], 'already_sent')
        submit.assert_not_called()
