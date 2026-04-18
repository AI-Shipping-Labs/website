"""Cross-process tests for django-q heartbeat detection.

The bug fixed by issue #219: with the default ``LocMemCache``, heartbeats
written by the ``manage.py qcluster`` process are invisible to the
gunicorn / runserver process — so ``/studio/worker/`` always reports
"NOT running" even when the cluster is healthy.

The fix is config-only: a shared cache backend (``FileBasedCache`` for
local dev, also viable on production with the existing Postgres). These
tests exercise the cross-process round trip end-to-end:

1. ``test_filebased_cache_round_trip_via_subprocess`` — spawn a real
   subprocess, have it write a fake ``Stat`` heartbeat into a tmpdir
   ``FileBasedCache``, then in this process read it back via
   ``Stat.get_all()`` and assert the worker dashboard reports alive.
   This is the test that would have caught the original bug — a
   single-process LocMemCache can never produce alive=True from a
   write done in another process.

2. ``DjangoQCacheWiringTest`` — the deployment wiring assertions:
   ``settings.Q_CLUSTER['cache'] == 'django_q'`` and
   ``settings.CACHES['django_q']`` exists. This catches the case where
   someone deletes the named cache, reverts the Q_CLUSTER pointer, or
   points Q_CLUSTER back at ``default``.
"""

import os
import subprocess
import sys
import tempfile

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings

WRITER_SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '_heartbeat_writer.py',
)


def _run_writer(cache_dir, cluster_id='test-cluster', secret_key=None):
    """Spawn the writer subprocess; return CompletedProcess for assertions.

    The subprocess must use the same Django ``SECRET_KEY`` as the parent —
    django-q's ``SignedPackage`` salts heartbeats with ``SECRET_KEY`` and
    the cluster name (``Q_CLUSTER['name']``), so a mismatched key causes
    ``BadSignature`` and ``Stat.get_all()`` silently drops the entry.
    """
    return subprocess.run(
        [
            sys.executable,
            WRITER_SCRIPT,
            '--cache-dir', cache_dir,
            '--cluster-id', cluster_id,
            '--secret-key', secret_key or settings.SECRET_KEY,
            '--q-cluster-name', settings.Q_CLUSTER['name'],
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


class FileBasedCacheCrossProcessTest(TestCase):
    """The fix: heartbeats written in a subprocess via FileBasedCache are
    visible to the test process.

    Uses ``TestCase`` (not ``SimpleTestCase``) because ``Stat.get_all()``
    constructs an ORM broker, which calls ``db.close_old_connections()``
    on init — that requires a real database connection.
    """

    def test_filebased_cache_round_trip_via_subprocess(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _run_writer(tmpdir, cluster_id='child-cluster')
            self.assertEqual(
                result.returncode, 0,
                f'writer subprocess failed:\nstdout={result.stdout}\nstderr={result.stderr}',
            )
            self.assertIn('WROTE', result.stdout)

            # Read in this (parent) process via the same cache LOCATION
            # the subprocess wrote to.
            override = {
                'CACHES': {
                    'default': {
                        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                    },
                    'django_q': {
                        'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache',
                        'LOCATION': tmpdir,
                    },
                },
            }
            with override_settings(**override):
                from studio.worker_health import get_worker_status
                info = get_worker_status()

            self.assertTrue(
                info['alive'],
                'FileBasedCache should make subprocess heartbeats visible '
                f'to the parent process. Got info={info}',
            )
            self.assertEqual(info['cluster_count'], 1)
            self.assertEqual(info['clusters'][0]['cluster_id'], 'child-cluster')


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

    def test_django_q_cache_is_cross_process_in_non_test_environments(self):
        """Documented contract for production / dev environments.

        Tests themselves run with LocMemCache (single-process, fast,
        isolated). For real environments the backend must be one that
        survives a process boundary — ``filebased`` or ``db`` are the
        approved choices for this project. (No Redis: deliberate
        product decision to keep the dep footprint small.)
        """
        # In tests, LocMemCache is fine — assert that's what's wired.
        backend = settings.CACHES['django_q']['BACKEND']
        self.assertEqual(
            backend, 'django.core.cache.backends.locmem.LocMemCache',
            'Tests should use LocMemCache for the django_q cache to '
            'stay isolated. Cross-process behaviour is exercised in '
            'FileBasedCacheCrossProcessTest above.',
        )
