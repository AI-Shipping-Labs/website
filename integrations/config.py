"""Configuration helper for integration settings.

Provides get_config() which checks the database first, then falls back
to environment variables. Uses an in-process cache that is cleared
when settings are saved via Studio.
"""

import os

_cache = {}
_cache_populated = False


def get_config(key, default=''):
    """Get config value: DB first, then env var, then Django settings, then default.

    Args:
        key: The setting key (e.g. 'ZOOM_CLIENT_ID').
        default: Default value if not found anywhere.

    Returns:
        str: The setting value.
    """
    global _cache, _cache_populated
    if not _cache_populated:
        _populate_cache()
    if key in _cache and _cache[key]:
        return _cache[key]
    # Check Django settings first (supports @override_settings in tests)
    from django.conf import settings  # noqa: PLC0415
    settings_val = getattr(settings, key, None)
    if settings_val is not None:
        return settings_val
    # Then env var
    env_val = os.environ.get(key)
    if env_val is not None:
        return env_val
    return default


def is_enabled(key):
    """Check if a config flag is enabled (handles both bool and string values).

    Args:
        key: The setting key (e.g. 'SLACK_ENABLED').

    Returns:
        bool: True if the value is truthy ('true', True, '1').
    """
    val = get_config(key, 'false')
    if isinstance(val, bool):
        return val
    return str(val).lower() in ('true', '1', 'yes')


def _populate_cache():
    """Load all IntegrationSetting values into the in-process cache."""
    global _cache, _cache_populated
    try:
        from integrations.models import IntegrationSetting  # noqa: PLC0415
        _cache = dict(IntegrationSetting.objects.values_list('key', 'value'))
        _cache_populated = True
    except Exception:
        _cache_populated = False


def clear_config_cache():
    """Clear the in-process config cache. Call after saving settings."""
    global _cache, _cache_populated
    _cache = {}
    _cache_populated = False
