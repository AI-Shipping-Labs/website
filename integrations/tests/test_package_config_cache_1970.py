"""Package config stamp bridge regressions for issue #1970."""

from unittest.mock import patch

from community_base.config.service import STAMP_KEY
from community_base.config.service import set as package_set
from django.core.cache import caches
from django.core.management import call_command
from django.test import TestCase, override_settings

from integrations import shared_cache
from integrations.config import get_config, reset_local_config_cache
from integrations.shared_cache import (
    LOCAL_TTL_SECONDS,
    SHARED_CACHE_ALIAS,
    reset_local_shared_cache_memo,
    restore_local_shared_cache_memo,
    snapshot_local_shared_cache_memo,
)

CACHE_TABLE = 'test_package_config_cache_1970'
CACHE_OVERRIDE = {
    'CACHES': {
        'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'},
        'django_q': {
            'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
            'LOCATION': CACHE_TABLE,
        },
    },
}


def _publish_in_other_process(write):
    memo = snapshot_local_shared_cache_memo()
    write()
    restore_local_shared_cache_memo(memo)


@override_settings(**CACHE_OVERRIDE)
class PackageConfigCacheTest(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call_command('createcachetable', CACHE_TABLE, verbosity=0)

    def setUp(self):
        caches.close_all()
        reset_local_shared_cache_memo()
        reset_local_config_cache()
        self.addCleanup(reset_local_shared_cache_memo)
        self.addCleanup(reset_local_config_cache)

    def test_repeated_reads_query_each_stamp_at_most_once(self):
        get_config('PODS_COURSE_SLUGS', '')
        reset_local_shared_cache_memo()
        backend = caches[SHARED_CACHE_ALIAS]
        original_get = backend.get
        reads = []

        def counting_get(key, default=None, version=None):
            if key in {STAMP_KEY, 'integration_settings_stamp'}:
                reads.append(key)
            return original_get(key, default, version=version)

        with patch.object(backend, 'get', side_effect=counting_get):
            for _ in range(10):
                get_config('PODS_COURSE_SLUGS', '')
        self.assertEqual(len(reads), len(set(reads)), reads)
        self.assertEqual(set(reads), {STAMP_KEY, 'integration_settings_stamp'})

    def test_package_write_reaches_reader_after_local_ttl(self):
        clock = {'now': 1_000.0}

        with patch.object(shared_cache, 'monotonic_time', lambda: clock['now']):
            with self.captureOnCommitCallbacks(execute=True):
                package_set('PODS_COURSE_SLUGS', 'old-course', actor_ref='test:1970')
            self.assertEqual(get_config('PODS_COURSE_SLUGS'), 'old-course')

            def publish():
                with self.captureOnCommitCallbacks(execute=True):
                    package_set(
                        'PODS_COURSE_SLUGS', 'ai-buildcamp', actor_ref='test:1970',
                    )

            _publish_in_other_process(publish)
            self.assertEqual(get_config('PODS_COURSE_SLUGS'), 'old-course')
            clock['now'] += LOCAL_TTL_SECONDS + 0.01
            self.assertEqual(get_config('PODS_COURSE_SLUGS'), 'ai-buildcamp')
