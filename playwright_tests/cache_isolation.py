"""Reset process-local state that survives transactional database cleanup."""

import pytest
from django.core.cache import cache

from integrations.config import reset_local_config_cache


@pytest.fixture(autouse=True)
def _reset_integration_config_cache():
    """Isolate the long-lived browser test server's in-memory caches.

    Transactional tests flush model rows, but neither the default LocMemCache
    nor IntegrationSetting's module-level cache participates in that flush.
    The server runs in this same process, so cached public tags from an
    earlier test can reject freshly seeded content for the five-minute TTL
    (#2081). Reset only the default cache, leaving DB-backed aliases to the
    transactional lifecycle.

    IntegrationSetting's shared stamp is itself flushed with the database.
    Its local reset intentionally publishes no stamp and touches no database,
    remaining safe for tests without database access (#1470).
    """
    cache.clear()
    reset_local_config_cache()
    yield
    cache.clear()
    reset_local_config_cache()
