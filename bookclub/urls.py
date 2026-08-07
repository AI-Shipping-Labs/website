"""Book Club public URLs (issues #1362, #1363).

Mounted at the site root so the canonical path is ``/books`` /
``/books/<slug>``. No trailing slash — the site normalizes to slash-less
canonical URLs via ``integrations.middleware.RemoveTrailingSlashMiddleware``.

Foundation scope (#1362) registered the detail route. #1363 adds the public
hub. The progress board, chapter, summary, and reader-profile routes arrive in
#1364+.
"""

from django.urls import path

from bookclub import views

urlpatterns = [
    path(
        'books',
        views.index,
        name='bookclub_index',
    ),
    path(
        'books/<slug:slug>',
        views.book_detail,
        name='bookclub_book_detail',
    ),
    path(
        'books/<slug:slug>/progress',
        views.book_progress,
        name='bookclub_book_progress',
    ),
    path(
        'books/<slug:slug>/chapters/<int:number>/read',
        views.chapter_read,
        name='bookclub_chapter_read',
    ),
    path(
        'books/<slug:slug>/chapters/<int:number>/note',
        views.chapter_note,
        name='bookclub_chapter_note',
    ),
    path(
        'books/<slug:slug>/chapters/<int:number>',
        views.chapter_detail,
        name='bookclub_chapter_detail',
    ),
    path(
        'books/<slug:slug>/readers/<int:user_id>/visibility',
        views.reader_visibility,
        name='bookclub_reader_visibility',
    ),
    path(
        'books/<slug:slug>/readers/<int:user_id>',
        views.reader_profile,
        name='bookclub_reader_profile',
    ),
]
