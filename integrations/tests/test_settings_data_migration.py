"""Data-migration coverage for A0.2 step 3 (issue #1584).

Checks that ``integrations`` migration ``0030_copy_integration_settings_to_cb_config``
copies every ``IntegrationSetting`` row into the package's ``cb_config.Setting``
with secrets Fernet-encrypted, donor type mapping, ``source='db'``, the donor
``updated_at`` preserved, and that re-running the forwards function is
idempotent. Plaintext secrets only ever appear as synthetic literals here.
"""

from importlib import import_module

from community_base.config.crypto import PREFIX, decrypt
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, tag
from django.utils import timezone

DONOR_ROWS = (
    # (key, value, is_secret, group)
    ('SLACK_INVITE_URL', 'https://example.com/join/slack', False, 'slack'),
    ('ZOOM_WAITING_ROOM', 'true', False, 'zoom'),
    ('EMAIL_BATCH_SIZE', '120', False, 'ses'),
    ('STRIPE_WEBHOOK_SECRET', 'sk_test_synthetic_123', True, 'stripe'),
    # Not declared in the package registry: typed from the frozen donor
    # snapshot (str for both), with the secret taken from the donor row flag.
    ('SYNTHETIC_ADHOC_TOKEN', 'adhoc_synthetic_secret_value', True, 'misc'),
    ('SYNTHETIC_ADHOC_NOTE', 'adhoc plain note', False, 'misc'),
)


@tag('slow_platform')
class SettingsDataMigrationTest(TransactionTestCase):
    migrate_from = [
        ('cb_config', '0001_initial'),
        ('integrations', '0029_webhooklog_calendly_event_uri_and_more'),
    ]
    migrate_to = [
        ('cb_config', '0001_initial'),
        ('integrations', '0030_copy_integration_settings_to_cb_config'),
    ]

    @staticmethod
    def _seed_donor_rows(IntegrationSetting):
        stamps = {}
        for key, value, is_secret, group in DONOR_ROWS:
            row = IntegrationSetting.objects.create(
                key=key,
                value=value,
                is_secret=is_secret,
                group=group,
                description=f'Synthetic row for {key}.',
            )
            # auto_now ignores the provided timestamp; pin it so the copy can be
            # checked against the donor value.
            stamp = timezone.now() - timezone.timedelta(days=3)
            IntegrationSetting.objects.filter(pk=row.pk).update(updated_at=stamp)
            stamps[key] = stamp
        return stamps

    def test_rows_are_copied_with_secrets_encrypted(self):
        executor = MigrationExecutor(connection)
        latest_targets = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate(self.migrate_from)
            old_apps = executor.loader.project_state(self.migrate_from).apps
            DonorSetting = old_apps.get_model('integrations', 'IntegrationSetting')
            self._seed_donor_rows(DonorSetting)

            executor = MigrationExecutor(connection)
            executor.migrate(self.migrate_to)
            new_apps = executor.loader.project_state(self.migrate_to).apps
            DonorSetting = new_apps.get_model('integrations', 'IntegrationSetting')
            PackageSetting = new_apps.get_model('cb_config', 'Setting')

            self.assertEqual(
                PackageSetting.objects.count(),
                DonorSetting.objects.count(),
            )
            for key, plaintext, is_secret, _group in DONOR_ROWS:
                with self.subTest(key=key):
                    donor = DonorSetting.objects.get(key=key)
                    copied = PackageSetting.objects.get(key=key)
                    self.assertEqual(copied.source, 'db')
                    self.assertEqual(copied.updated_at, donor.updated_at)
                    if is_secret:
                        # Stored encrypted, decryptable to the exact plaintext.
                        self.assertIsInstance(copied.value, str)
                        self.assertTrue(copied.value.startswith(PREFIX))
                        self.assertNotIn(plaintext, copied.value)
                        self.assertEqual(decrypt(copied.value), plaintext)
                        self.assertEqual(copied.value_type, 'str')
                    else:
                        # Plain values are stored as raw JSON string scalars.
                        self.assertEqual(copied.value, plaintext)
                    if key == 'ZOOM_WAITING_ROOM':
                        self.assertEqual(copied.value_type, 'bool')
                    elif key == 'EMAIL_BATCH_SIZE':
                        self.assertEqual(copied.value_type, 'int')
                    elif key == 'SLACK_INVITE_URL':
                        # Package registry wins over the donor 'url' type.
                        self.assertEqual(copied.value_type, 'str')
                    elif key.startswith('SYNTHETIC_ADHOC'):
                        self.assertEqual(copied.value_type, 'str')

            # Idempotency: re-running the forwards function must not duplicate
            # or alter the already-copied rows.
            migration = import_module(
                'integrations.migrations.0030_copy_integration_settings_to_cb_config'
            )
            migration.copy_integration_settings(new_apps, None)
            self.assertEqual(
                PackageSetting.objects.count(),
                DonorSetting.objects.count(),
            )
            secret_row = PackageSetting.objects.get(key='STRIPE_WEBHOOK_SECRET')
            self.assertEqual(decrypt(secret_row.value), 'sk_test_synthetic_123')
        finally:
            MigrationExecutor(connection).migrate(latest_targets)
