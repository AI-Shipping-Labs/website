"""Template helpers for the community-base settings page."""

from django import template

register = template.Library()

_DOCS_BASE = "https://github.com/AI-Shipping-Labs/website/blob/main/"
_RESTART_KEYS = frozenset(
    {"LOGFIRE_ENABLED", "LOGFIRE_TOKEN", "LOGFIRE_ENVIRONMENT"}
)


@register.filter
def community_docs_url(value):
    """Turn a registry-relative docs path into a public GitHub blob URL."""
    if not value:
        return ""
    value = str(value)
    if value.startswith(("https://", "http://")):
        return value
    return _DOCS_BASE + value.lstrip("/")


@register.filter
def setting_requires_restart(setting):
    """Use package metadata, with the transition registry as a fallback."""
    if not isinstance(setting, dict):
        return False
    return bool(setting.get("requires_restart") or setting.get("key") in _RESTART_KEYS)
