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
from api.utils import parse_json_body, require_methods
from bookclub.models import (
    BOOK_STATUS_DRAFT,
    READER_VISIBILITY_CHOICES,
    READER_VISIBILITY_PUBLIC,
    Book,
    ChapterRead,
    Note,
    ReaderProfile,
)
from bookclub.reading import viewer_reading_progress
from content.access import get_user_level
from member_api.serializers.books import serialize_book_note, serialize_book_reading

_VALID_VISIBILITIES = {value for value, _ in READER_VISIBILITY_CHOICES}


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


def _require_scope(request, scope):
    """Return a 401 response if the authenticated key lacks ``scope``."""
    if scope in getattr(request, "member_api_scopes", set()):
        return None
    return error_response(
        "Member API key is missing the required scope",
        "insufficient_scope",
        status=401,
        details={"required_scope": scope},
    )


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


_CHAPTER_NOTE_OPENAPI = {
    "GET": {
        "summary": "Get the caller's own note for a chapter",
        "description": (
            "Returns the authenticated key owner's own note for the chapter, "
            "or 404 if they have not written one. Scoped to the key owner only "
            "— never another member's note, and never the group feed of other "
            "members' notes (that is a rendered web surface). Requires the "
            "``books:read`` scope."
        ),
        "responses": {
            200: {
                "description": "The caller's own note.",
                "example": {
                    "book": {"slug": "inference-engineering", "title": "Inference Engineering"},
                    "chapter": {"number": 0, "title": "Inference"},
                    "body": "The KV cache is the whole game.",
                    "body_html": "<p>The KV cache is the whole game.</p>",
                    "comment_content_id": "0b3d…",
                    "created_at": "2026-08-06T10:00:00+00:00",
                    "updated_at": "2026-08-06T10:00:00+00:00",
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
                "description": "Unknown/draft book, unknown chapter, or no note yet.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
        },
    },
    "PUT": {
        "summary": "Upsert the caller's own note",
        "description": (
            "Creates or replaces the key owner's single note for the chapter "
            "(``update_or_create`` on owner + chapter). ``body`` is required "
            "and is markdown — a fenced ```mermaid block renders as a diagram "
            "on the web, other fenced blocks render as code. The rendered, "
            "sanitised HTML is returned as ``body_html``. The first save flips "
            "the account's activation flag. Posting comments on a note and "
            "reading the group feed are out of scope for the member API — "
            "comments use the shared web endpoints. Requires the "
            "``books:write_notes`` scope."
        ),
        "request_body": {
            "required": ["body"],
            "properties": {
                "body": {"type": "string"},
            },
            "example": {"body": "The KV cache is the whole game."},
        },
        "responses": {
            200: {"description": "Upserted note."},
            401: {
                "description": "Missing key or missing ``books:write_notes`` scope.",
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
            422: {
                "description": "Missing or empty ``body``.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
        },
    },
    "DELETE": {
        "summary": "Clear the caller's own note",
        "description": (
            "Idempotently deletes the key owner's note for the chapter. "
            "Destructive deletes of other members' notes stay Studio/admin "
            "scoped. Requires the ``books:write_notes`` scope."
        ),
        "responses": {
            200: {"description": "Note cleared (idempotent)."},
            401: {
                "description": "Missing key or missing ``books:write_notes`` scope.",
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
@member_api_key_required()
@require_methods("GET", "PUT", "DELETE")
@openapi_spec(tag="Books", methods=_CHAPTER_NOTE_OPENAPI)
def book_chapter_note(request, slug, number):
    """Owner-scoped CRUD for the caller's own per-chapter note (issue #1365).

    Scopes are enforced per method (the decorator takes no scope so the read
    and write scopes can diverge): GET requires ``books:read`` while PUT /
    DELETE require ``books:write_notes`` (a progress-tracking automation with
    only ``books:write_progress`` must not be able to publish public notes).
    Access mirrors the server gate: draft / unknown book -> 404, below tier ->
    403, unknown chapter -> 404.
    """
    required_scope = "books:read" if request.method == "GET" else "books:write_notes"
    denied = _require_scope(request, required_scope)
    if denied is not None:
        return denied

    book, book_error = _book_or_error(slug)
    if book_error is not None:
        return book_error
    access_error = _access_error(request, book)
    if access_error is not None:
        return access_error

    chapter = book.chapters.filter(number=number).first()
    if chapter is None:
        return error_response("Chapter not found", "chapter_not_found", status=404)

    if request.method == "GET":
        note = Note.objects.filter(user=request.user, chapter=chapter).first()
        if note is None:
            return error_response("Note not found", "note_not_found", status=404)
        return JsonResponse(serialize_book_note(note, book, chapter))

    if request.method == "DELETE":
        Note.objects.filter(user=request.user, chapter=chapter).delete()
        return JsonResponse({"deleted": True, "chapter": {"number": number}})

    # PUT — upsert the caller's own note.
    payload, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(payload, dict):
        return error_response(
            "Body must be a JSON object", "invalid_body", status=422,
        )

    body = (payload.get("body") or "").strip()
    if not body:
        return error_response("body is required", "body_required", status=422)

    note, created = Note.objects.update_or_create(
        user=request.user,
        chapter=chapter,
        defaults={"body": body},
    )
    if created:
        # Writing a note is a real platform action (issue #768).
        mark_activated(request.user)
    return JsonResponse(serialize_book_note(note, book, chapter))


_READER_PROFILE_OPENAPI = {
    "GET": {
        "summary": "Get the caller's reading-profile visibility",
        "description": (
            "Returns the authenticated key owner's Book Club reading-profile "
            "notes visibility (``public`` or ``private``). Book-agnostic — "
            "visibility is a single per-user flag. Public shares notes in "
            "chapter group feeds and on the member's reader profile; private "
            "keeps note bodies visible only to the member and staff. A member "
            "with no profile row is reported as ``public`` (the default). "
            "Scoped to the key owner "
            "only — never another member's profile. Requires the ``books:read`` "
            "scope."
        ),
        "responses": {
            200: {
                "description": "The caller's current visibility.",
                "example": {"visibility": "public"},
            },
            401: {
                "description": "Missing key or missing ``books:read`` scope.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
        },
    },
    "PUT": {
        "summary": "Set the caller's reading-profile visibility",
        "description": (
            "Sets the authenticated key owner's reading-profile visibility "
            "from the JSON body ``{\"visibility\": \"public\"|\"private\"}`` "
            "(``get_or_create`` on the owner). Public shares notes in chapter "
            "group feeds and on the member's reader profile; private keeps "
            "note bodies visible only to the member and staff. The default is "
            "public. Book-agnostic and scoped to the "
            "key owner only — there is no user parameter. Any value other than "
            "``public`` / ``private`` returns a 400. Reading another member's "
            "public profile / notes feed (a rendered web surface) and posting "
            "comments (the shared web comment endpoints) are out of scope for "
            "the member API; destructive deletes stay Studio/admin-scoped. "
            "Requires the ``books:write_profile`` scope."
        ),
        "request_body": {
            "required": ["visibility"],
            "properties": {
                "visibility": {"type": "string", "enum": ["public", "private"]},
            },
            "example": {"visibility": "public"},
        },
        "responses": {
            200: {
                "description": "Updated visibility.",
                "example": {"visibility": "public"},
            },
            400: {
                "description": "Missing or invalid ``visibility`` value.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            401: {
                "description": (
                    "Missing key or missing ``books:write_profile`` scope."
                ),
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
        },
    },
}


@csrf_exempt
@member_api_key_required()
@require_methods("GET", "PUT")
@openapi_spec(tag="Books", methods=_READER_PROFILE_OPENAPI)
def reader_profile(request):
    """Owner-scoped read/set of the caller's Book Club notes visibility (#1366).

    Book-agnostic — notes visibility is a single per-user flag. Scopes are
    enforced per method (the decorator takes no scope so read and write
    diverge): GET requires ``books:read`` while PUT requires
    ``books:write_profile`` (a progress or notes automation must not be able to
    flip a member's notes-sharing posture). No user parameter — the endpoint
    only ever acts on the key owner.
    """
    required_scope = (
        "books:read" if request.method == "GET" else "books:write_profile"
    )
    denied = _require_scope(request, required_scope)
    if denied is not None:
        return denied

    if request.method == "GET":
        profile = ReaderProfile.objects.filter(user=request.user).first()
        visibility = (
            profile.visibility if profile is not None
            else READER_VISIBILITY_PUBLIC
        )
        return JsonResponse({"visibility": visibility})

    # PUT — set the caller's visibility.
    payload, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(payload, dict):
        return error_response(
            "Body must be a JSON object", "invalid_body", status=400,
        )

    visibility = payload.get("visibility")
    if visibility not in _VALID_VISIBILITIES:
        return error_response(
            "visibility must be 'public' or 'private'",
            "invalid_visibility",
            status=400,
        )

    profile, _ = ReaderProfile.objects.update_or_create(
        user=request.user,
        defaults={"visibility": visibility},
    )
    return JsonResponse({"visibility": profile.visibility})
