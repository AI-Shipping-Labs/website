"""Redirect shims from the old ``/studio/subscribers/`` URLs (issue #271).

The page itself moved to ``/studio/users/``. These shims keep any existing
bookmarks / external links working by issuing a permanent (301) redirect onto
the dedicated Subscribers chip.
"""

from django.http import HttpResponsePermanentRedirect
from django.urls import reverse


def subscriber_list_redirect(request):
    """301 from ``/studio/subscribers/`` to ``/studio/users/``."""
    return HttpResponsePermanentRedirect(
        f'{reverse("studio_user_list")}?filter=subscribers'
    )


def subscriber_export_redirect(request):
    """301 from ``/studio/subscribers/export`` to ``/studio/users/export``."""
    return HttpResponsePermanentRedirect(
        f'{reverse("studio_user_export")}?filter=subscribers'
    )
