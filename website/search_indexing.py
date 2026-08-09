"""Deployment-wide search-indexing policy.

The development deployment must remain fully functional and crawlable while
never being eligible for indexing.  Classify the deployment from Django's
environment-backed ``SITE_BASE_URL`` setting rather than the runtime database
override: an operator edit must not be able to turn off this safety rail, and
the check must remain available before database-backed middleware runs.
"""

from urllib.parse import urlsplit

from django.conf import settings

DEV_SITE_HOST = 'dev.aishippinglabs.com'
PRODUCTION_SITE_HOST = 'aishippinglabs.com'
DEV_ROBOTS_DIRECTIVE = 'noindex, nofollow, noarchive'
DEV_ROBOTS_META_CONTENT = 'noindex,nofollow,noarchive'

SEARCH_ENVIRONMENT_PRODUCTION = 'production'
SEARCH_ENVIRONMENT_DEV = 'dev'
SEARCH_ENVIRONMENT_OTHER = 'other'


def search_indexing_environment():
    """Classify the deployment from the environment-backed site hostname."""
    try:
        hostname = urlsplit(str(settings.SITE_BASE_URL).strip()).hostname
    except ValueError:
        return SEARCH_ENVIRONMENT_OTHER

    hostname = (hostname or '').lower()
    if hostname == PRODUCTION_SITE_HOST:
        return SEARCH_ENVIRONMENT_PRODUCTION
    if hostname == DEV_SITE_HOST:
        return SEARCH_ENVIRONMENT_DEV
    return SEARCH_ENVIRONMENT_OTHER


def search_indexing_disabled():
    """Return whether this process is serving the development deployment."""
    return search_indexing_environment() == SEARCH_ENVIRONMENT_DEV


def production_search_indexing_enabled():
    """Return whether this process is the explicitly classified production."""
    return search_indexing_environment() == SEARCH_ENVIRONMENT_PRODUCTION
