"""Book Club admin API (issue #1362).

Mirrors ``api/views/sprints.py``: ``@token_required`` JSON endpoints with an
admin bearer for writes, JSON error envelopes, and OpenAPI registration.

- ``GET/POST /api/books`` — list all books; create (admin bearer).
- ``GET/PATCH /api/books/<slug>`` — retrieve; partial update (admin bearer).
  ``DELETE`` returns 405 pointing to Studio (delete stays Studio-scoped).
- ``GET/POST /api/books/<slug>/chapters`` — list ordered chapters; create.
- ``GET/PATCH /api/books/<slug>/chapters/<int:number>`` — retrieve; update.
  ``DELETE`` returns 405 pointing to Studio.

``event_series`` is accepted by pk or slug and unknown values are rejected,
mirroring the sprint precedent.
"""

from datetime import date

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.serializers.books import serialize_book, serialize_chapter
from api.utils import (
    delete_not_available_response,
    parse_json_body,
    require_methods,
)
from api.views._permissions import bearer_is_admin
from bookclub.models import BOOK_STATUS_CHOICES, Book, Chapter
from content.access import VISIBILITY_CHOICES as TIER_LEVEL_CHOICES
from events.models import Event
from studio.views.books import _parse_event_series

VALID_BOOK_STATUSES = {choice for choice, _label in BOOK_STATUS_CHOICES}
VALID_TIER_LEVELS = {value for value, _label in TIER_LEVEL_CHOICES}

_BOOK_STATUS_ENUM = sorted(VALID_BOOK_STATUSES)
_TIER_LEVEL_ENUM = sorted(VALID_TIER_LEVELS)

# Delete stays Studio-scoped (mirrors the sprint precedent, issue #864).
BOOK_DELETE_NOT_AVAILABLE_MESSAGE = (
    "Book deletion is not available through the API. "
    "Go to Studio to delete this book manually."
)
CHAPTER_DELETE_NOT_AVAILABLE_MESSAGE = (
    "Chapter deletion is not available through the API. "
    "Go to Studio to delete this chapter manually."
)

_EVENT_SERIES_PROPERTY = {
    "type": ["string", "integer", "null"],
    "nullable": True,
    "description": (
        "Link to an event series, resolved by id (integer or numeric "
        "string) or slug (non-numeric string). ``null`` or an empty string "
        "clears the link; an unknown id/slug returns 422 ``unknown_series``."
    ),
}

_BOOK_EXAMPLE = {
    "slug": "inference-engineering",
    "title": "Inference Engineering",
    "subtitle": "The technologies behind every AI product in production",
    "author": "Philip Kiely",
    "description": "",
    "cover_image_url": "",
    "cover_accent": "from-accent/30",
    "required_level": 20,
    "status": "current",
    "start_date": "2026-08-10",
    "meeting_cadence": "Weekly · 17:00 CET",
    "buy_url": "https://www.baseten.co/inference-engineering/",
    "event_series": None,
    "chapter_count": 8,
    "created_at": "2026-08-01T12:00:00+00:00",
    "updated_at": "2026-08-01T12:00:00+00:00",
}

_CHAPTER_EVENT_PROPERTY = {
    "type": ["string", "integer", "null"],
    "nullable": True,
    "description": (
        "Link to an event, resolved by id (integer or numeric string) or "
        "slug (non-numeric string). ``null`` or an empty string detaches. "
        "Series-only rule: the event must be an occurrence of the book's "
        "linked ``event_series``. If the book has no series, the event is not "
        "in the series, or the id/slug is unknown, the request returns 422 "
        "with an ``event`` field error and the chapter is left unchanged."
    ),
}

_CHAPTER_EXAMPLE = {
    "number": 0,
    "title": "Inference",
    "deadline": "2026-08-17",
    "week_label": "Week 1",
    "event": None,
    "created_at": "2026-08-01T12:00:00+00:00",
    "updated_at": "2026-08-01T12:00:00+00:00",
}


def _parse_iso_date(value):
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _resolve_book(slug):
    book = (
        Book.objects.select_related("event_series").filter(slug=slug).first()
    )
    if book is None:
        return None, error_response(
            "Book not found", "unknown_book", status=404,
        )
    return book, None


def _resolve_event_series_body(raw):
    """Resolve ``event_series`` from a JSON body. Returns ``(series, error)``.

    ``error`` is a ready ``error_response`` (or ``None``). ``series`` is
    ``None`` for an explicit unlink. Wrong types are a 422 ``validation_error``;
    unknown id/slug is a 422 ``unknown_series``.
    """
    if raw in (None, ""):
        return None, None
    if not isinstance(raw, (int, str)) or isinstance(raw, bool):
        return None, error_response(
            "Invalid event_series", "validation_error", status=422,
            details={"event_series": "Must be an event series id or slug"},
        )
    series, parse_error = _parse_event_series(raw)
    if parse_error:
        return None, error_response(
            "Unknown event series", "unknown_series", status=422,
            details={"event_series": "Unknown event series"},
        )
    return series, None


@token_required
@csrf_exempt
@require_methods("GET", "POST")
@openapi_spec(
    tag="Book Club",
    summary="List or create book club books",
    methods={
        "GET": {
            "summary": "List books",
            "description": "Returns every book. Chapters are summarized as a count.",
            "responses": {
                200: {
                    "description": "List of books.",
                    "example": {"books": [_BOOK_EXAMPLE]},
                },
                401: {"description": "Missing or invalid token."},
            },
        },
        "POST": {
            "summary": "Create a book (admin bearer)",
            "description": (
                "Admin-only. Non-admin tokens get 403. Slug uniqueness is "
                "enforced at the API layer (422) before hitting the DB."
            ),
            "request_body": {
                "required": ["title", "author", "slug"],
                "properties": {
                    "title": {"type": "string"},
                    "author": {"type": "string"},
                    "slug": {"type": "string"},
                    "subtitle": {"type": "string", "default": ""},
                    "description": {"type": "string", "default": ""},
                    "cover_image_url": {"type": "string", "default": ""},
                    "cover_accent": {"type": "string", "default": "from-accent/30"},
                    "required_level": {
                        "type": "integer", "enum": _TIER_LEVEL_ENUM, "default": 20,
                    },
                    "status": {
                        "type": "string", "enum": _BOOK_STATUS_ENUM, "default": "draft",
                    },
                    "start_date": {"type": "string", "format": "date"},
                    "meeting_cadence": {"type": "string", "default": ""},
                    "buy_url": {"type": "string", "default": ""},
                    "event_series": _EVENT_SERIES_PROPERTY,
                },
                "example": {
                    "title": "Inference Engineering",
                    "author": "Philip Kiely",
                    "slug": "inference-engineering",
                    "required_level": 20,
                    "status": "current",
                    "start_date": "2026-08-10",
                },
            },
            "responses": {
                201: {"description": "Book created.", "example": _BOOK_EXAMPLE},
                400: {"description": "Invalid JSON body."},
                403: {"description": "Non-admin token attempted to create."},
                422: {"description": "Validation error (missing field, bad "
                                     "value, duplicate slug, unknown series)."},
            },
        },
    },
)
def books_collection(request):
    """``GET /api/books`` and ``POST /api/books``."""
    if request.method == "GET":
        qs = Book.objects.select_related("event_series").all()
        return JsonResponse(
            {"books": [serialize_book(b) for b in qs]}, status=200,
        )

    if not bearer_is_admin(request.user):
        return error_response(
            "Book creation is admin-only", "forbidden", status=403,
        )

    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return error_response(
            "Body must be a JSON object", "invalid_type",
            details={"field": "body", "expected": "object"},
        )

    for required in ("title", "author", "slug"):
        if data.get(required) in (None, ""):
            return error_response(
                f"Missing required field: {required}", "missing_field",
                status=422, details={"field": required},
            )

    fields, field_error = _read_book_fields(data, partial=False)
    if field_error is not None:
        return field_error

    if Book.objects.filter(slug=data["slug"]).exists():
        return error_response(
            "Slug already exists", "validation_error", status=422,
            details={"slug": "Slug already in use"},
        )

    book = Book(slug=data["slug"], title=data["title"], author=data["author"])
    for name, value in fields.items():
        setattr(book, name, value)
    book.save()
    return JsonResponse(serialize_book(book, include_chapters=True), status=201)


def _read_book_fields(data, *, partial):
    """Read and validate optional book fields from ``data``.

    Returns ``(fields_dict, error_response|None)``. ``fields_dict`` holds only
    the keys present in ``data`` (for PATCH) or all optional keys with defaults
    applied (never for required title/author/slug, which the caller sets).
    """
    fields = {}
    string_fields = (
        "subtitle", "description", "cover_image_url", "cover_accent",
        "meeting_cadence", "buy_url",
    )
    for name in string_fields:
        if name in data:
            if not isinstance(data[name], str):
                return None, error_response(
                    f"{name} must be a string", "invalid_type",
                    details={"field": name, "expected": "string"},
                )
            fields[name] = data[name]

    # On create, allow title/author to flow through too when present so a full
    # replace works; the caller has already validated required presence.
    if not partial:
        if "title" in data:
            fields["title"] = data["title"]
        if "author" in data:
            fields["author"] = data["author"]

    if "required_level" in data:
        if data["required_level"] not in VALID_TIER_LEVELS:
            return None, error_response(
                "Invalid required_level", "validation_error", status=422,
                details={"required_level": "Must be one of 0, 10, 20, 30"},
            )
        fields["required_level"] = data["required_level"]

    if "status" in data:
        if data["status"] not in VALID_BOOK_STATUSES:
            return None, error_response(
                "Invalid status", "validation_error", status=422,
                details={"status": "Unknown status"},
            )
        fields["status"] = data["status"]

    if "start_date" in data:
        if data["start_date"] in (None, ""):
            fields["start_date"] = None
        else:
            parsed = _parse_iso_date(data["start_date"])
            if parsed is None:
                return None, error_response(
                    "start_date must be ISO 8601 (YYYY-MM-DD)",
                    "validation_error", status=422,
                    details={"start_date": "Invalid date format"},
                )
            fields["start_date"] = parsed

    if "event_series" in data:
        series, series_error = _resolve_event_series_body(data["event_series"])
        if series_error is not None:
            return None, series_error
        fields["event_series"] = series

    return fields, None


@token_required
@csrf_exempt
@require_methods("GET", "PATCH", "DELETE")
@openapi_spec(
    tag="Book Club",
    summary="Retrieve, update, or delete a book",
    methods={
        "GET": {
            "summary": "Retrieve a book",
            "description": "Returns the book with its ordered chapters.",
            "responses": {
                200: {"description": "Book detail.", "example": _BOOK_EXAMPLE},
                401: {"description": "Missing or invalid token."},
                404: {"description": "Book not found."},
            },
        },
        "PATCH": {
            "summary": "Update a book (admin bearer)",
            "description": "Admin-only partial update.",
            "request_body": {
                "properties": {
                    "title": {"type": "string"},
                    "author": {"type": "string"},
                    "subtitle": {"type": "string"},
                    "description": {"type": "string"},
                    "cover_image_url": {"type": "string"},
                    "cover_accent": {"type": "string"},
                    "required_level": {"type": "integer", "enum": _TIER_LEVEL_ENUM},
                    "status": {"type": "string", "enum": _BOOK_STATUS_ENUM},
                    "start_date": {"type": "string", "format": "date"},
                    "meeting_cadence": {"type": "string"},
                    "buy_url": {"type": "string"},
                    "event_series": _EVENT_SERIES_PROPERTY,
                },
                "example": {"status": "finished"},
            },
            "responses": {
                200: {"description": "Book updated.", "example": _BOOK_EXAMPLE},
                400: {"description": "Invalid JSON body."},
                403: {"description": "Non-admin token."},
                404: {"description": "Book not found."},
                422: {"description": "Validation error."},
            },
        },
        "DELETE": {
            "summary": "DELETE is not available on this route",
            "description": (
                "Book deletion is not available through the API; operators "
                "must use Studio. DELETE returns a structured 405."
            ),
            "responses": {
                405: {
                    "description": "Book deletion is not available.",
                    "example": {
                        "error": BOOK_DELETE_NOT_AVAILABLE_MESSAGE,
                        "code": "book_delete_not_available",
                    },
                },
            },
        },
    },
)
def book_detail(request, slug):
    """``GET / PATCH /api/books/<slug>``. DELETE -> 405 Studio pointer."""
    if request.method == "DELETE":
        return delete_not_available_response(
            BOOK_DELETE_NOT_AVAILABLE_MESSAGE, "book_delete_not_available",
        )

    book, book_error = _resolve_book(slug)
    if book_error is not None:
        return book_error

    if request.method == "GET":
        return JsonResponse(
            serialize_book(book, include_chapters=True), status=200,
        )

    if not bearer_is_admin(request.user):
        return error_response("Admin-only endpoint", "forbidden", status=403)

    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return error_response(
            "Body must be a JSON object", "invalid_type",
            details={"field": "body", "expected": "object"},
        )
    if "title" in data and data["title"] in (None, ""):
        return error_response(
            "title cannot be empty", "validation_error", status=422,
            details={"title": "Required"},
        )
    if "author" in data and data["author"] in (None, ""):
        return error_response(
            "author cannot be empty", "validation_error", status=422,
            details={"author": "Required"},
        )
    if "slug" in data:
        new_slug = data["slug"]
        if new_slug in (None, ""):
            return error_response(
                "slug cannot be empty", "validation_error", status=422,
                details={"slug": "Required"},
            )
        if Book.objects.filter(slug=new_slug).exclude(pk=book.pk).exists():
            return error_response(
                "Slug already exists", "validation_error", status=422,
                details={"slug": "Slug already in use"},
            )
        book.slug = new_slug

    fields, field_error = _read_book_fields(data, partial=True)
    if field_error is not None:
        return field_error
    if "title" in data:
        book.title = data["title"]
    if "author" in data:
        book.author = data["author"]
    for name, value in fields.items():
        setattr(book, name, value)
    book.save()
    return JsonResponse(serialize_book(book, include_chapters=True), status=200)


def _resolve_event_body(raw):
    """Resolve a chapter ``event`` from a JSON body. Returns ``(event, error)``.

    ``error`` is a ready ``error_response`` (or ``None``). ``event`` is
    ``None`` for an explicit detach (JSON ``null`` / empty string). Resolution
    is by integer pk (or numeric string pk), else by slug. Wrong types are a
    422 ``validation_error``; an unknown id/slug is a 422 with an ``event``
    field error — never a 404 on the chapter. The series-only rule is enforced
    separately via ``Chapter.clean()`` (``_validate_chapter_event``).
    """
    if raw in (None, ""):
        return None, None
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        return None, error_response(
            "Invalid event", "validation_error", status=422,
            details={"event": "Must be an event id or slug"},
        )
    if isinstance(raw, int):
        event = Event.objects.filter(pk=raw).first()
    elif raw.lstrip("-").isdigit():
        event = Event.objects.filter(pk=int(raw)).first()
    else:
        event = Event.objects.filter(slug=raw).first()
    if event is None:
        return None, error_response(
            "Unknown event", "validation_error", status=422,
            details={"event": "Unknown event"},
        )
    return event, None


def _validate_chapter_event(chapter):
    """Run the series-only ``Chapter.clean()`` rule. Returns ``error|None``.

    Translates the model ``ValidationError`` on the ``event`` field into the
    API's 422 error envelope so the constraint lives in exactly one place
    (``Chapter.clean()``) and cannot be bypassed through the API.
    """
    try:
        chapter.clean()
    except ValidationError as exc:
        messages = exc.message_dict.get("event") or ["Invalid event"]
        return error_response(
            "Invalid event", "validation_error", status=422,
            details={"event": messages[0]},
        )
    return None


def _read_chapter_fields(data, *, partial):
    """Read/validate chapter fields. Returns ``(fields, error_response|None)``.

    ``event`` (pk/slug/null) is resolved to an ``events.Event`` instance here;
    the series-only rule is applied by the caller via ``_validate_chapter_event``
    once the chapter's ``book`` is known.
    """
    fields = {}
    if "event" in data:
        event, event_error = _resolve_event_body(data["event"])
        if event_error is not None:
            return None, event_error
        fields["event"] = event
    if "title" in data:
        if not isinstance(data["title"], str) or not data["title"].strip():
            return None, error_response(
                "title must be a non-empty string", "validation_error",
                status=422, details={"title": "Required"},
            )
        fields["title"] = data["title"]
    if "week_label" in data:
        if not isinstance(data["week_label"], str):
            return None, error_response(
                "week_label must be a string", "invalid_type",
                details={"field": "week_label", "expected": "string"},
            )
        fields["week_label"] = data["week_label"]
    if "deadline" in data:
        if data["deadline"] in (None, ""):
            fields["deadline"] = None
        else:
            parsed = _parse_iso_date(data["deadline"])
            if parsed is None:
                return None, error_response(
                    "deadline must be ISO 8601 (YYYY-MM-DD)",
                    "validation_error", status=422,
                    details={"deadline": "Invalid date format"},
                )
            fields["deadline"] = parsed
    return fields, None


@token_required
@csrf_exempt
@require_methods("GET", "POST")
@openapi_spec(
    tag="Book Club",
    summary="List or create a book's chapters",
    methods={
        "GET": {
            "summary": "List chapters",
            "description": "Returns the book's chapters ordered by number.",
            "responses": {
                200: {
                    "description": "List of chapters.",
                    "example": {"chapters": [_CHAPTER_EXAMPLE]},
                },
                401: {"description": "Missing or invalid token."},
                404: {"description": "Book not found."},
            },
        },
        "POST": {
            "summary": "Create a chapter (admin bearer)",
            "description": (
                "Admin-only. ``number`` must be unique within the book "
                "(422 on collision)."
            ),
            "request_body": {
                "required": ["number", "title"],
                "properties": {
                    "number": {"type": "integer"},
                    "title": {"type": "string"},
                    "deadline": {"type": "string", "format": "date"},
                    "week_label": {"type": "string", "default": ""},
                    "event": _CHAPTER_EVENT_PROPERTY,
                },
                "example": {"number": 0, "title": "Inference", "week_label": "Week 1"},
            },
            "responses": {
                201: {"description": "Chapter created.", "example": _CHAPTER_EXAMPLE},
                400: {"description": "Invalid JSON body."},
                403: {"description": "Non-admin token."},
                404: {"description": "Book not found."},
                422: {"description": "Validation error or duplicate number."},
            },
        },
    },
)
def book_chapters_collection(request, slug):
    """``GET / POST /api/books/<slug>/chapters``."""
    book, book_error = _resolve_book(slug)
    if book_error is not None:
        return book_error

    if request.method == "GET":
        return JsonResponse(
            {"chapters": [serialize_chapter(c) for c in book.chapters.all()]},
            status=200,
        )

    if not bearer_is_admin(request.user):
        return error_response(
            "Chapter creation is admin-only", "forbidden", status=403,
        )

    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return error_response(
            "Body must be a JSON object", "invalid_type",
            details={"field": "body", "expected": "object"},
        )

    if not isinstance(data.get("number"), int) or isinstance(data.get("number"), bool):
        return error_response(
            "number must be an integer", "validation_error", status=422,
            details={"number": "Required integer"},
        )
    if data["number"] < 0:
        return error_response(
            "number must be 0 or greater", "validation_error", status=422,
            details={"number": "Must be >= 0"},
        )
    if data.get("title") in (None, ""):
        return error_response(
            "Missing required field: title", "missing_field", status=422,
            details={"field": "title"},
        )

    fields, field_error = _read_chapter_fields(data, partial=False)
    if field_error is not None:
        return field_error
    if Chapter.objects.filter(book=book, number=data["number"]).exists():
        return error_response(
            "Chapter number already exists", "validation_error", status=422,
            details={"number": "A chapter with this number already exists"},
        )

    chapter = Chapter(book=book, number=data["number"])
    for name, value in fields.items():
        setattr(chapter, name, value)
    event_error = _validate_chapter_event(chapter)
    if event_error is not None:
        return event_error
    try:
        chapter.save()
    except IntegrityError:
        return error_response(
            "Chapter number already exists", "validation_error", status=422,
            details={"number": "A chapter with this number already exists"},
        )
    return JsonResponse(serialize_chapter(chapter), status=201)


@token_required
@csrf_exempt
@require_methods("GET", "PATCH", "DELETE")
@openapi_spec(
    tag="Book Club",
    summary="Retrieve, update, or delete a chapter",
    methods={
        "GET": {
            "summary": "Retrieve a chapter",
            "responses": {
                200: {"description": "Chapter detail.", "example": _CHAPTER_EXAMPLE},
                401: {"description": "Missing or invalid token."},
                404: {"description": "Book or chapter not found."},
            },
        },
        "PATCH": {
            "summary": "Update a chapter (admin bearer)",
            "request_body": {
                "properties": {
                    "title": {"type": "string"},
                    "deadline": {"type": "string", "format": "date"},
                    "week_label": {"type": "string"},
                    "event": _CHAPTER_EVENT_PROPERTY,
                },
                "example": {"deadline": "2026-09-10"},
            },
            "responses": {
                200: {"description": "Chapter updated.", "example": _CHAPTER_EXAMPLE},
                400: {"description": "Invalid JSON body."},
                403: {"description": "Non-admin token."},
                404: {"description": "Book or chapter not found."},
                422: {"description": "Validation error."},
            },
        },
        "DELETE": {
            "summary": "DELETE is not available on this route",
            "description": (
                "Chapter deletion is not available through the API; operators "
                "must use Studio. DELETE returns a structured 405."
            ),
            "responses": {
                405: {
                    "description": "Chapter deletion is not available.",
                    "example": {
                        "error": CHAPTER_DELETE_NOT_AVAILABLE_MESSAGE,
                        "code": "chapter_delete_not_available",
                    },
                },
            },
        },
    },
)
def book_chapter_detail(request, slug, number):
    """``GET / PATCH /api/books/<slug>/chapters/<int:number>``.

    DELETE -> 405 Studio pointer.
    """
    if request.method == "DELETE":
        return delete_not_available_response(
            CHAPTER_DELETE_NOT_AVAILABLE_MESSAGE,
            "chapter_delete_not_available",
        )

    book, book_error = _resolve_book(slug)
    if book_error is not None:
        return book_error
    chapter = Chapter.objects.filter(book=book, number=number).first()
    if chapter is None:
        return error_response(
            "Chapter not found", "unknown_chapter", status=404,
        )

    if request.method == "GET":
        return JsonResponse(serialize_chapter(chapter), status=200)

    if not bearer_is_admin(request.user):
        return error_response("Admin-only endpoint", "forbidden", status=403)

    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return error_response(
            "Body must be a JSON object", "invalid_type",
            details={"field": "body", "expected": "object"},
        )

    fields, field_error = _read_chapter_fields(data, partial=True)
    if field_error is not None:
        return field_error
    for name, value in fields.items():
        setattr(chapter, name, value)
    event_error = _validate_chapter_event(chapter)
    if event_error is not None:
        # Re-read from the DB so the in-memory (rejected) mutation does not
        # leak back to the caller; the persisted chapter is unchanged.
        return event_error
    chapter.save()
    return JsonResponse(serialize_chapter(chapter), status=200)
