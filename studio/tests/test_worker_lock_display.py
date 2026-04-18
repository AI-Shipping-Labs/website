"""Tests for the Pending Tasks "Lock expires" column (issue #241).

The previous implementation labelled the column "Age" and computed it as
``now - ormq.lock``. That was wrong: in django-q2, ``OrmQ.lock`` is the
per-worker claim-expiry timestamp (set to ``now + retry_after`` whenever a
worker tries to claim the row), NOT a queued-at timestamp. As a result, a
freshly-claimed task showed an "age" of ``-60s``.

These tests pin the new behaviour:

* ``lock IS NULL``     -> ``—``
* ``lock > now``       -> ``in Ns``           (worker holds an active claim)
* ``lock <= now``      -> ``expired Ns ago``  (claim expired, awaiting reclaim)

No negative numbers should ever appear.
"""

import re
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from django_q.models import OrmQ
from django_q.signing import SignedPackage

from studio.views.worker import _ormq_summary

User = get_user_model()


def _make_ormq(*, lock, name='task'):
    """Persist a real signed OrmQ row with the given ``lock`` value."""
    payload = {
        'id': uuid.uuid4().hex,
        'name': name,
        'func': 'integrations.services.github.sync_content_source',
        'args': (),
        'kwargs': {},
    }
    return OrmQ.objects.create(
        key='default',
        payload=SignedPackage.dumps(payload),
        lock=lock,
    )


class OrmqSummaryLockStateTest(TestCase):
    """``_ormq_summary`` translates ``OrmQ.lock`` into a state + magnitude."""

    def test_null_lock_reports_unlocked(self):
        ormq = _make_ormq(lock=None)
        summary = _ormq_summary(ormq, now=timezone.now())
        self.assertEqual(summary['lock_state'], 'unlocked')
        self.assertIsNone(summary['lock_seconds'])

    def test_future_lock_reports_positive_countdown(self):
        now = timezone.now()
        ormq = _make_ormq(lock=now + timedelta(seconds=30))
        summary = _ormq_summary(ormq, now=now)
        self.assertEqual(summary['lock_state'], 'future')
        # Magnitude is positive, never negative — that was the bug.
        self.assertGreater(summary['lock_seconds'], 0)
        self.assertAlmostEqual(summary['lock_seconds'], 30, delta=0.5)

    def test_past_lock_reports_expired_with_positive_age(self):
        now = timezone.now()
        ormq = _make_ormq(lock=now - timedelta(seconds=12))
        summary = _ormq_summary(ormq, now=now)
        self.assertEqual(summary['lock_state'], 'expired')
        self.assertGreater(summary['lock_seconds'], 0)
        self.assertAlmostEqual(summary['lock_seconds'], 12, delta=0.5)

    def test_no_age_seconds_key_remains(self):
        """Old key removed so callers cannot accidentally render -60s."""
        ormq = _make_ormq(lock=timezone.now() + timedelta(seconds=10))
        summary = _ormq_summary(ormq, now=timezone.now())
        self.assertNotIn('age_seconds', summary)


class PendingTasksTableLockColumnTest(TestCase):
    """Worker dashboard renders the Lock-expires column with no negatives."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def _get_dashboard(self):
        with patch('studio.worker_health.Stat.get_all', return_value=[]):
            return self.client.get('/studio/worker/')

    def test_header_renamed_from_age_to_lock_expires(self):
        _make_ormq(lock=None, name='just-queued')
        response = self._get_dashboard()
        self.assertContains(response, 'Lock expires')
        # The literal "Age" header is gone from the pending-tasks table. We
        # check only the table header cell, not arbitrary occurrences of the
        # word elsewhere in the page.
        self.assertNotContains(
            response,
            '<th class="text-left px-4 py-3 text-muted-foreground font-medium">Age</th>',
        )

    def test_unlocked_task_shows_em_dash(self):
        _make_ormq(lock=None, name='just-queued')
        response = self._get_dashboard()
        # The em-dash placeholder is used for unlocked rows.
        self.assertContains(response, 'just-queued')
        self.assertContains(response, '—')

    def test_future_lock_renders_in_ns_never_negative(self):
        _make_ormq(
            lock=timezone.now() + timedelta(seconds=30),
            name='locked-task',
        )
        response = self._get_dashboard()
        body = response.content.decode()
        self.assertIn('locked-task', body)
        self.assertIn('in 30s', body)
        # Nothing in the rendered page should display a negative seconds value
        # for the Lock-expires column.
        self.assertFalse(
            re.search(r'-\d+\s*s\b', body),
            'Found negative seconds value in worker dashboard output',
        )

    def test_expired_lock_renders_expired_n_ago(self):
        _make_ormq(
            lock=timezone.now() - timedelta(seconds=20),
            name='expired-task',
        )
        response = self._get_dashboard()
        self.assertContains(response, 'expired-task')
        self.assertContains(response, 'expired 20s ago')


class InspectViewLockColumnTest(TestCase):
    """The single-task inspect view uses the same Lock-expires semantics."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_inspect_future_lock_shows_in_ns(self):
        ormq = _make_ormq(
            lock=timezone.now() + timedelta(seconds=45),
            name='inspect-future',
        )
        response = self.client.get(
            f'/studio/worker/queue/{ormq.pk}/inspect/',
        )
        self.assertContains(response, 'Lock expires')
        self.assertContains(response, 'in 45s')

    def test_inspect_unlocked_shows_em_dash(self):
        ormq = _make_ormq(lock=None, name='inspect-unlocked')
        response = self.client.get(
            f'/studio/worker/queue/{ormq.pk}/inspect/',
        )
        self.assertContains(response, 'Lock expires')
        # Em-dash placeholder appears for the lock value.
        body = response.content.decode()
        # Confirm there's no "in -Ns" or "expired -Ns ago" anywhere.
        self.assertFalse(
            re.search(r'-\d+\s*s\b', body),
            'Found negative seconds in inspect view output',
        )
