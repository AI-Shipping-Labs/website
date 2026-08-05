"""Book Club prototype URLs.

Mounted under /book-club/ from the root URLconf. Prototype only.
"""

from django.urls import path

from . import views

# No trailing slashes — the site normalizes to slash-less canonical URLs via
# integrations.middleware.RemoveTrailingSlashMiddleware.
urlpatterns = [
    path("books", views.index, name="bookclub_index"),
    path("books/<slug:slug>", views.book_detail, name="bookclub_book_detail"),
    path(
        "books/<slug:slug>/progress",
        views.progress,
        name="bookclub_leaderboard",
    ),
    path(
        "books/<slug:slug>/chapters/<int:number>",
        views.chapter_detail,
        name="bookclub_chapter_detail",
    ),
    path(
        "books/<slug:slug>/summary",
        views.book_summary,
        name="bookclub_book_summary",
    ),
    path(
        "books/<slug:slug>/readers/<str:handle>",
        views.reader_profile,
        name="bookclub_reader_profile",
    ),
]
