"""Book Club public URLs (issue #1362).

Mounted at the site root so the canonical path is ``/books/<slug>``. No
trailing slash — the site normalizes to slash-less canonical URLs via
``integrations.middleware.RemoveTrailingSlashMiddleware``.

Foundation scope registers only the detail route. The hub, progress board,
chapter, summary, and reader-profile routes arrive in #1363+.
"""

from django.urls import path

from bookclub import views

urlpatterns = [
    path(
        'books/<slug:slug>',
        views.book_detail,
        name='bookclub_book_detail',
    ),
]
