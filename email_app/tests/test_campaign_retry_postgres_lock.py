"""PostgreSQL regression coverage for the operator campaign retry lock.

Found by the repo-wide ``select_for_update`` audit in issue #1919:
``retry_delivery`` locked the delivery with a bare ``select_for_update()``
while also ``select_related('campaign', 'wave')``. ``CampaignDelivery.wave``
is nullable, so PostgreSQL raised ``NotSupportedError: FOR UPDATE cannot be
applied to the nullable side of an outer join`` and the Studio / API retry
of a failed delivery returned a 500. SQLite drops the locking clause, so
only the CI ``PostgreSQL 16 Verification`` job (``--tag=postgresql``) can
catch it.
"""

from unittest.mock import patch

from django.db import connection
from django.test import TestCase, tag

from email_app.models import CampaignDelivery
from email_app.services.campaign_dispatch import retry_delivery
from email_app.tests.test_campaign_delivery_1499 import CampaignDeliveryBase
from tests.fixtures import create_user_with_membership


@tag('core', 'postgresql')
class CampaignRetryLockPostgresTest(CampaignDeliveryBase, TestCase):
    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest(
                'FOR UPDATE over a nullable outer join is a PostgreSQL-only '
                'failure; SQLite drops the locking clause entirely'
            )

    @patch('jobs.tasks.async_task', return_value='targeted-retry')
    def test_retry_of_a_failed_delivery_without_a_wave_locks_on_postgres(self, enqueue):
        _campaign, _user, delivery = self.make_campaign_delivery(state=CampaignDelivery.State.FAILED)
        self.assertIsNone(delivery.wave_id)
        actor = create_user_with_membership(email='resolver@test.com', tier=self.free_tier, is_staff=True)
        retry_delivery(delivery.pk, actor=actor)
        delivery.refresh_from_db()
        self.assertEqual(
            (delivery.state, delivery.resolution, delivery.resolved_by_id),
            (CampaignDelivery.State.PENDING, CampaignDelivery.Resolution.RETRY, actor.pk),
        )
        self.assertEqual(enqueue.call_count, 1)
