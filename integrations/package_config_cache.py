"""Bridge community-base runtime settings to the shared cache."""

from integrations.shared_cache import get_shared_cache, set_shared_cache


class SharedStampStore:
    """Minimal cache facade using the site's five-second read memo."""

    def get(self, key, default=None):
        return get_shared_cache(key, default)

    def set(self, key, value, timeout=None):
        set_shared_cache(key, value, timeout)


def configure_package_config_cache():
    """Publish package setting changes through the site's shared cache."""
    from community_base.config.service import runtime  # noqa: PLC0415

    runtime.stamp_store = SharedStampStore()
