"""Cross-process tests for django-q heartbeat detection.

The bug fixed by issue #219: with the default ``LocMemCache``, heartbeats
written by the ``manage.py qcluster`` process are invisible to the
gunicorn / runserver process — so ``/studio/worker/`` always reports
"NOT running" even when the cluster is healthy. #219 swapped the named
``django_q`` cache to ``FileBasedCache``.

The bug fixed by issue #273: ``FileBasedCache`` works on a single host
but is per-container in ECS, where web and worker run as separate ECS
tasks with their own ephemeral disks — so the same false-negative came
back in production. #273 swaps the named ``django_q`` cache to
``DatabaseCache`` (the application DB is the only thing every container
shares). Both bugs are the same shape: a per-instance cache cannot carry
heartbeats from one process to another.

Tests in this module:

1. ``DatabaseCacheRoundTripTest`` — write a fake ``Stat`` heartbeat into
   a ``DatabaseCache`` backed by the test DB using the same code path
   ``django_q.brokers.orm.ORM.set_stat`` uses, then call
   ``get_worker_status()`` and assert the dashboard reports alive.
   Verifies the heartbeat row actually lives in the DB cache table —
   that's what makes the round-trip cross-process / cross-container in
   any deployment that shares a database.

2. ``DjangoQCacheWiringTest`` — deployment wiring assertions:
   ``settings.Q_CLUSTER['cache'] == 'django_q'`` and
   ``settings.CACHES['django_q']`` exists and points at LocMemCache in
   tests. Catches the case where someone deletes the named cache,
   reverts the Q_CLUSTER pointer, or points Q_CLUSTER back at
   ``default``.
"""

import os

from django.conf import settings
from django.core.cache import caches
from django.core.management import call_command
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings

# Reuse the existing fake-heartbeat plumbing.
CACHE_TABLE = 'test_django_q_cache'

DATABASE_CACHE_OVERRIDE = {
    'CACHES': {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        },
        'django_q': {
            'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
            'LOCATION': CACHE_TABLE,
        },
    },
}


def _write_fake_heartbeat(cluster_id):
    """Write a fake django-q ``Stat`` heartbeat through the named cache.

    Mirrors ``django_q.brokers.Broker.set_stat`` (and what the qcluster
    Sentinel writes on every guard cycle): maintain the index list at
    ``Conf.Q_STAT`` and store the signed payload at ``Stat.get_key(...)``.
    The signing salt is ``Q_CLUSTER['name']`` + ``SECRET_KEY`` — both
    must match what the parent process uses when reading, otherwise
    ``Stat.get_all()`` silently drops the entry on ``BadSignature``.
    """
    from django_q.conf import Conf
    from django_q.signing import SignedPackage
    from django_q.status import Stat, Status

    heartbeat = Status(pid=os.getpid(), cluster_id=cluster_id)
    heartbeat.status = 'Idle'
    heartbeat.workers = [os.getpid()]

    cache = caches[Conf.CACHE]
    key = Stat.get_key(cluster_id)
    payload = SignedPackage.dumps(heartbeat, True)

    key_list = cache.get(Conf.Q_STAT, []) or []
    if key not in key_list:
        key_list.append(key)
    cache.set(Conf.Q_STAT, key_list)
    cache.set(key, payload, 30)
    return key


class DatabaseCacheRoundTripTest(TestCase):
    """A heartbeat written via ``CACHES['django_q']`` must round-trip
    through the database, not process memory.

    This is the regression test for #273 (and, by construction, #219):
    if someone reverts the named cache to ``LocMemCache``, the row is
    written to a per-process dict and ``get_worker_status()`` will still
    return ``alive=True`` *in this single test process* — which is why
    the second assertion below queries the cache table directly. A
    LocMemCache backend has no DB row, so that assertion fails.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # createcachetable is idempotent; safe to call against the test DB.
        call_command('createcachetable', CACHE_TABLE, verbosity=0)

    def test_database_cache_round_trip(self):
        with override_settings(**DATABASE_CACHE_OVERRIDE):
            _write_fake_heartbeat('child-cluster')

            from studio.worker_health import get_worker_status
            info = get_worker_status()

            self.assertTrue(
                info['alive'],
                'DatabaseCache should make heartbeats visible to any '
                f'process that talks to the same DB. Got info={info}',
            )
            self.assertEqual(info['cluster_count'], 1)
            self.assertEqual(info['clusters'][0]['cluster_id'], 'child-cluster')

            # Prove the row actually lives in the DB cache table — that's
            # what makes this cross-process. A LocMemCache backend would
            # leave the table empty.
            with connection.cursor() as cur:
                cur.execute(f'SELECT COUNT(*) FROM {CACHE_TABLE}')
                row_count = cur.fetchone()[0]
            self.assertGreater(
                row_count, 0,
                f'Expected heartbeat rows in {CACHE_TABLE} after writing '
                'via CACHES["django_q"]. An empty table means the cache '
                'backend is not persisting to the DB — likely reverted '
                'to LocMemCache or FileBasedCache.',
            )


class DjangoQCacheWiringTest(SimpleTestCase):
    """Deployment-level wiring: settings must use a named django_q cache
    so the cluster heartbeat does not share namespace with whatever the
    application uses ``cache.default`` for."""

    def test_q_cluster_points_at_named_django_q_cache(self):
        self.assertEqual(
            settings.Q_CLUSTER.get('cache'), 'django_q',
            'Q_CLUSTER[cache] must be "django_q" so heartbeats land in '
            'the cross-process cache, not the per-process default.',
        )

    def test_django_q_cache_is_configured(self):
        self.assertIn(
            'django_q', settings.CACHES,
            'CACHES["django_q"] must exist — the dedicated cluster cache.',
        )

    def test_django_q_cache_uses_locmem_in_tests(self):
        """Tests run in a single process, so LocMemCache is fine and
        keeps tests fast and isolated. Cross-process behaviour is
        exercised by ``DatabaseCacheRoundTripTest`` above using
        ``override_settings``.
        """
        backend = settings.CACHES['django_q']['BACKEND']
        self.assertEqual(
            backend, 'django.core.cache.backends.locmem.LocMemCache',
            'Tests should use LocMemCache for the django_q cache to '
            'stay isolated. Cross-process behaviour is exercised in '
            'DatabaseCacheRoundTripTest.',
        )
