import importlib
from datetime import timedelta

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import EmailAlias
from email_app.models import EmailCampaign, EmailLog, SesEvent
from email_app.services.email_log_history import (
    apply_email_log_filters,
    email_log_queryset,
    user_history_queryset,
)

User = get_user_model()


class EmailLogHistoryQueryTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="Primary@Example.com")
        EmailAlias.objects.create(user=self.user, email="old@example.com")

    def _log(self, **kwargs):
        defaults = {
            "recipient_email": "primary@example.com",
            "email_type": "welcome",
            "subject": "Welcome",
        }
        defaults.update(kwargs)
        return EmailLog.objects.create(**defaults)

    def test_canonical_history_unions_fk_primary_and_alias_once(self):
        owned = self._log(user=self.user, recipient_email="other-old@example.com")
        alias = self._log(recipient_email="OLD@EXAMPLE.COM")
        duplicate = self._log(user=self.user, recipient_email="old@example.com")
        unrelated = self._log(recipient_email="unrelated@example.com")

        ids = list(user_history_queryset(self.user).values_list("pk", flat=True))

        self.assertCountEqual(ids, [owned.pk, alias.pk, duplicate.pk])
        self.assertNotIn(unrelated.pk, ids)

    def test_partial_search_does_not_expand_fk_history(self):
        match = self._log(user=self.user, recipient_email="primary@example.com")
        owned_old = self._log(user=self.user, recipient_email="historic@elsewhere.test")

        rows = apply_email_log_filters(
            email_log_queryset(), search="PRIMARY@EXAMPLE",
        )

        self.assertIn(match, rows)
        self.assertNotIn(owned_old, rows)

    def test_exact_alias_search_expands_full_canonical_history(self):
        owned_old = self._log(user=self.user, recipient_email="historic@elsewhere.test")
        alias = self._log(recipient_email="old@example.com")

        rows = apply_email_log_filters(
            email_log_queryset(), search="OLD@example.com",
        )

        self.assertCountEqual(rows.values_list("pk", flat=True), [owned_old.pk, alias.pk])

    def test_exclusive_disposition_precedence_and_delivery(self):
        sent = self._log(recipient_email="sent@example.com")
        delivered = self._log(recipient_email="delivered@example.com")
        opened = self._log(recipient_email="opened@example.com", opens=1)
        clicked = self._log(
            recipient_email="clicked@example.com", opens=2, clicks=1,
        )
        bounced = self._log(
            recipient_email="bounced@example.com", clicks=1,
            bounced_at=timezone.now(),
        )
        complained = self._log(
            recipient_email="complained@example.com", bounced_at=timezone.now(),
            complained_at=timezone.now(),
        )
        SesEvent.objects.create(
            event_type=SesEvent.EVENT_TYPE_DELIVERY,
            message_id="delivery-1283",
            raw_payload={},
            recipient_email=delivered.recipient_email,
            email_log=delivered,
        )

        dispositions = dict(
            email_log_queryset().values_list("pk", "disposition")
        )

        self.assertEqual(dispositions[sent.pk], "sent")
        self.assertEqual(dispositions[delivered.pk], "delivered")
        self.assertEqual(dispositions[opened.pk], "opened")
        self.assertEqual(dispositions[clicked.pk], "clicked")
        self.assertEqual(dispositions[bounced.pk], "bounced")
        self.assertEqual(dispositions[complained.pk], "complained")
        self.assertEqual(
            list(
                apply_email_log_filters(
                    email_log_queryset(), status="opened",
                ).values_list("pk", flat=True)
            ),
            [opened.pk],
        )

    def test_migration_backfills_only_knowable_snapshots(self):
        campaign = EmailCampaign.objects.create(subject="Campaign snapshot", body="Body")
        campaign_log = self._log(
            user=self.user, recipient_email="", subject="", campaign=campaign,
            email_type="campaign",
        )
        transactional = self._log(
            user=self.user, recipient_email="", subject="", email_type="welcome",
        )
        migration = importlib.import_module(
            "email_app.migrations.0020_emaillog_recipient_subject_snapshots"
        )

        migration.backfill_snapshots(apps, None)
        campaign_log.refresh_from_db()
        transactional.refresh_from_db()

        self.assertEqual(campaign_log.recipient_email, self.user.email)
        self.assertEqual(campaign_log.subject, "Campaign snapshot")
        self.assertEqual(transactional.recipient_email, self.user.email)
        self.assertEqual(transactional.subject, "")

    def test_newest_first_is_deterministic(self):
        older = self._log(user=self.user)
        newer = self._log(user=self.user)
        EmailLog.objects.filter(pk=older.pk).update(
            sent_at=timezone.now() - timedelta(days=1)
        )
        self.assertEqual(list(user_history_queryset(self.user)[:2]), [newer, older])
