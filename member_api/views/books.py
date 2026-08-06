"""Owner-only member Book Club reading endpoints (issue #1364).

Every endpoint authenticates with a scoped member API key. The
``member_api_key_required`` decorator sets ``request.user = key.user``, so a
member structurally cannot touch another member's reading state — no endpoint
accepts a user parameter. Ownership is enforced by always filtering
``ChapterRead`` on ``request.user``.

Access mirrors the server-rendered gate: draft / unknown book -> 404, below
``book.required_level`` -> 403, unknown chapter -> 404.
"""

from __future__ import annotations

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import member_api_key_required
from accounts.utils.activation import mark_activated
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import require_methods
from bookclub.models import BOOK_STATUS_DRAFT, Book, ChapterRead
from bookclub.reading import viewer_reading_progress
from content.access import get_user_level
from member_api.serializers.books import serialize_book_reading


def _visible_book(slug):
    """Return a non-draft book for ``slug`` or ``None`` (draft/unknown 404)."""
    return (
        Book.objects.filter(slug=slug)
        .exclude(status=BOOK_STATUS_DRAFT)
        .first()
    )


def _book_or_error(slug):
    book = _visible_book(slug)
    if book is None:
        return None, error_response("Book not found", "book_not_found", status=404)
    return book, None


def _access_error(request, book):
    """Return a 403 error response if the key owner is below the book tier."""
    if get_user_level(request.user) < book.required_level:
        return error_response(
            "Your tier does not grant access to this book",
            "book_access_denied",
            status=403,
        )
    return None


_BOOK_READING_OPENAPI = {
    "GET": {
        "summary": "Get the caller's reading state for a book",
        "description": (
            "Returns the authenticated key owner's per-chapter read state for "
            "the book plus ``total`` / ``done`` / ``pct``. Scoped to the key "
            "owner only — never another member's state. Requires the "
            "``books:read`` scope."
        ),
        "responses": {
            200: {
                "description": "The caller's reading state.",
                "example": {
                    "book": {"slug": "inference-engineering", "title": "Inference Engineering"},
                    "total": 5,
                    "done": 2,
                    "pct": 40,
                    "chapters": [
                        {"number": 0, "title": "Inference", "read": True,
                         "read_at": "2026-08-06T10:00:00+00:00"},
                        {"number": 1, "title": "Prerequisites", "read": False,
                         "read_at": None},
                    ],
                },
            },
            401: {
                "description": "Missing key or missing ``books:read`` scope.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            403: {
                "description": "Key owner's tier is below the book's required level.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            404: {
                "description": "Unknown or draft book.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
        },
    },
}


@csrf_exempt
@member_api_key_required("books:read")
@require_methods("GET")
@openapi_spec(tag="Books", methods=_BOOK_READING_OPENAPI)
def book_reading(request, slug):
    book, book_error = _book_or_error(slug)
    if book_error is not None:
        return book_error
    access_error = _access_error(request, book)
    if access_error is not None:
        return access_error
    return JsonResponse(serialize_book_reading(book, request.user))


_CHAPTER_READ_OPENAPI = {
    "PUT": {
        "summary": "Mark a chapter read",
        "description": (
            "Idempotently marks the chapter read for the authenticated key "
            "owner (``get_or_create``). A repeated call is a no-op. Returns "
            "the updated ``done`` / ``total``. Requires the "
            "``books:write_progress`` scope."
        ),
        "responses": {
            200: {
                "description": "Chapter is read.",
                "example": {
                    "read": True,
                    "read_at": "2026-08-06T10:00:00+00:00",
                    "done": 3,
                    "total": 5,
                },
            },
            401: {
                "description": "Missing key or missing ``books:write_progress`` scope.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            403: {
                "description": "Key owner's tier is below the book's required level.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            404: {
                "description": "Unknown/draft book or unknown chapter number.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
        },
    },
    "DELETE": {
        "summary": "Mark a chapter unread",
        "description": (
            "Idempotently removes the caller's read row for the chapter "
            "(delete if present). A repeated call is a no-op. Returns the "
            "updated ``done`` / ``total``. Requires the "
            "``books:write_progress`` scope."
        ),
        "responses": {
            200: {
                "description": "Chapter is unread.",
                "example": {"read": False, "done": 2, "total": 5},
            },
            401: {
                "description": "Missing key or missing ``books:write_progress`` scope.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            403: {
                "description": "Key owner's tier is below the book's required level.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            404: {
                "description": "Unknown/draft book or unknown chapter number.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
        },
    },
}


@csrf_exempt
@member_api_key_required("books:write_progress")
@require_methods("PUT", "DELETE")
@openapi_spec(tag="Books", methods=_CHAPTER_READ_OPENAPI)
def book_chapter_read(request, slug, number):
    book, book_error = _book_or_error(slug)
    if book_error is not None:
        return book_error
    access_error = _access_error(request, book)
    if access_error is not None:
        return access_error

    chapter = book.chapters.filter(number=number).first()
    if chapter is None:
        return error_response("Chapter not found", "chapter_not_found", status=404)

    if request.method == "PUT":
        row, created = ChapterRead.objects.get_or_create(
            user=request.user,
            chapter=chapter,
            defaults={"read_at": timezone.now()},
        )
        if created:
            # Marking a chapter read is a real platform action (issue #768).
            mark_activated(request.user)
        progress = viewer_reading_progress(request.user, book)
        return JsonResponse({
            "read": True,
            "read_at": row.read_at.isoformat(),
            "done": progress["done"],
            "total": progress["total"],
        })

    # DELETE — idempotent unread.
    ChapterRead.objects.filter(user=request.user, chapter=chapter).delete()
    progress = viewer_reading_progress(request.user, book)
    return JsonResponse({
        "read": False,
        "done": progress["done"],
        "total": progress["total"],
    })
