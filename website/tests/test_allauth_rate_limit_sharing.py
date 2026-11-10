from allauth.core.exceptions import RateLimited
from allauth.core.internal import ratelimit
from django.core.cache.backends.db import DatabaseCache
from django.test import RequestFactory, TestCase, override_settings

from accounts.adapters import AccountAdapter


class AllauthRateLimitSharingTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.original_cache = ratelimit.cache

    def tearDown(self):
        ratelimit.cache = self.original_cache

    @override_settings(ALLAUTH_TRUSTED_PROXY_COUNT=1)
    def test_client_ip_uses_the_site_one_proxy_hop_policy(self):
        request = self.factory.post(
            '/accounts/password/reset/',
            REMOTE_ADDR='10.0.0.4',
            HTTP_X_FORWARDED_FOR='198.51.100.88, 203.0.113.27',
        )

        self.assertEqual(
            AccountAdapter().get_client_ip(request),
            '203.0.113.27',
        )

    def test_allauth_uses_a_database_cache_shared_by_cache_instances(self):
        params = {
            'OPTIONS': {
                'MAX_ENTRIES': 100000,
                'CULL_FREQUENCY': 10,
            },
        }
        worker_one_cache = DatabaseCache('django_q_cache', params)
        worker_two_cache = DatabaseCache('django_q_cache', params)
        worker_one_cache.delete('allauth:rl:login:ip:203.0.113.27')
        worker_one_cache.delete('allauth:rl:login:ip:203.0.113.28')
        ratelimit.cache = worker_one_cache

        request_a = self.factory.post(
            '/accounts/login/',
            REMOTE_ADDR='10.0.0.4',
            HTTP_X_FORWARDED_FOR='203.0.113.27',
        )
        request_b = self.factory.post(
            '/accounts/login/',
            REMOTE_ADDR='10.0.0.4',
            HTTP_X_FORWARDED_FOR='203.0.113.28',
        )
        config = {'login': '1/m/ip'}

        first_usage = ratelimit.consume(
            request_a,
            app='allauth.account',
            action='login',
            config=config,
            raise_exception=True,
        )
        self.assertIsNotNone(first_usage)

        ratelimit.cache = worker_two_cache
        with self.assertRaises(RateLimited):
            ratelimit.consume(
                request_a,
                app='allauth.account',
                action='login',
                config=config,
                raise_exception=True,
            )
        self.assertIsNotNone(
            ratelimit.consume(
                request_b,
                app='allauth.account',
                action='login',
                config=config,
                raise_exception=True,
            )
        )
