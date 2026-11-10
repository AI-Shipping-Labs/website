from django.apps import AppConfig


class WebsiteConfig(AppConfig):
    name = 'website'
    verbose_name = 'Website'

    def ready(self):
        """Give allauth rate limits the shared cache without changing page caching."""
        from allauth.core.internal import ratelimit
        from django.core.cache import caches

        # allauth 65.x has no rate-limit-specific cache setting; its rate-limit
        # module binds Django's default cache proxy. Keep unrelated website
        # cache entries in LocMemCache and share only allauth's counters.
        ratelimit.cache = caches['allauth_rate_limits']
