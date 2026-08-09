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
DEV_ROBOTS_DIRECTIVE = 'noindex, nofollow, noarchive'
DEV_ROBOTS_META_CONTENT = 'noindex,nofollow,noarchive'


def search_indexing_disabled():
    """Return whether this process is serving the development deployment."""
    try:
        hostname = urlsplit(str(settings.SITE_BASE_URL).strip()).hostname
    except ValueError:
        return False
    return (hostname or '').lower() == DEV_SITE_HOST
