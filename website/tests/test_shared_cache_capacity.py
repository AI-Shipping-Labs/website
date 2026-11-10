from django.conf import settings
from django.core.cache.backends.db import DatabaseCache
from django.test import TestCase


class SharedDatabaseCacheCapacityTests(TestCase):
    def test_shared_cache_keeps_system_entries_beyond_400_rate_keys(self):
        options = settings._SHARED_DATABASE_CACHE_OPTIONS
        self.assertEqual(options['MAX_ENTRIES'], 100000)
        self.assertEqual(options['CULL_FREQUENCY'], 10)
        self.assertEqual(settings.CACHES['allauth_rate_limits']['OPTIONS'], options)

        cache = DatabaseCache('django_q_cache', {'OPTIONS': options})
        cache.set('auth-throttle:sentinel', 'retained', timeout=3600)
        cache.set('django-q:heartbeat-sentinel', 'retained', timeout=3600)
        for index in range(400):
            cache.set(f'allauth:rl:reset:ip:198.51.100.{index}', [index], timeout=3600)

        self.assertEqual(cache.get('auth-throttle:sentinel'), 'retained')
        self.assertEqual(cache.get('django-q:heartbeat-sentinel'), 'retained')
