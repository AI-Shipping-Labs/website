"""URL routes for the JSON API (issue #431).

All routes are mounted under ``/api/`` from ``website/urls.py``. This module
handles ``/api/contacts/...``; existing ``/api/checkout/``, ``/api/notifications/``,
etc. routes keep working because Django evaluates ``include()``s in order and
none of them collide with the contacts prefix.
"""

from django.urls import path

from api.views.contacts import (
    contacts_export,
    contacts_import,
    contacts_set_tags,
)

urlpatterns = [
    path(
        "contacts/import",
        contacts_import,
        name="api_contacts_import",
    ),
    path(
        "contacts/export",
        contacts_export,
        name="api_contacts_export",
    ),
    # Email contains '@' and '.' which the slug converter doesn't match;
    # use the path converter so the address is captured intact.
    path(
        "contacts/<path:email>/tags",
        contacts_set_tags,
        name="api_contacts_set_tags",
    ),
]
