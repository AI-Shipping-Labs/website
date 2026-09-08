"""``asl comments`` -- list shared comments and post idempotent replies."""

from __future__ import annotations

from pathlib import Path

import click

from asl_cli.commands._shared import emit, format_option, get_client

API = "/api/comments"
CONTENT_TYPES = [
    "course_unit", "workshop_page", "sprint_plan", "book_club_note", "unknown",
]
TABLE_COLUMNS = [
    "id", "kind", "content_type", "author_email", "created_at",
    "reply_count", "context", "body",
]


@click.group()
def comments():
    """Fetch comments and post staff replies."""


def _fail(message):
    raise click.UsageError(message)


def _validate_list_filters(params):
    if params["limit"] < 1 or params["limit"] > 200:
        _fail("--limit must be between 1 and 200")
    if params["offset"] < 0:
        _fail("--offset must be non-negative")
    if params["module_slug"] is not None and params["course_slug"] is None:
        _fail("--module requires --course")
    if params["unit_slug"] is not None and (
        params["course_slug"] is None or params["module_slug"] is None
    ):
        _fail("--unit requires --course and --module")
    if params["page_slug"] is not None and params["workshop_key"] is None:
        _fail("--page requires --workshop")
    if params["chapter_number"] is not None and params["book_slug"] is None:
        _fail("--chapter requires --book")

    groups = {
        "course_unit": any(params[name] is not None for name in (
            "course_slug", "module_slug", "unit_slug",
        )),
        "workshop_page": any(params[name] is not None for name in (
            "workshop_key", "page_slug",
        )),
        "book_club_note": any(params[name] is not None for name in (
            "book_slug", "chapter_number",
        )),
        "sprint_plan": params["plan_id"] is not None,
    }
    selected = [name for name, active in groups.items() if active]
    if len(selected) > 1:
        _fail("Owner-specific filters from different thread types cannot be mixed")
    if selected and params["content_type"] not in (None, selected[0]):
        _fail("--content-type contradicts the owner-specific filters")
    if params["unanswered"] is not None and (
        params["kind"] == "reply" or params["parent_id"] is not None
    ):
        _fail("--unanswered/--answered cannot be combined with replies or --parent-id")
    if params["kind"] == "top_level" and params["parent_id"] is not None:
        _fail("--parent-id cannot be combined with --kind top_level")


@comments.command("list")
@click.option("--content-type", type=click.Choice(CONTENT_TYPES))
@click.option("--content-id")
@click.option("--kind", type=click.Choice(["top_level", "reply"]))
@click.option("--parent-id", type=int)
@click.option("--author-email")
@click.option("--since")
@click.option("--until")
@click.option("--unanswered/--answered", default=None)
@click.option("--course", "course_slug")
@click.option("--module", "module_slug")
@click.option("--unit", "unit_slug")
@click.option("--workshop", "workshop_key")
@click.option("--page", "page_slug")
@click.option("--book", "book_slug")
@click.option("--chapter", "chapter_number", type=int)
@click.option("--plan-id", type=int)
@click.option("--limit", type=int, default=50, show_default=True)
@click.option("--offset", type=int, default=0, show_default=True)
@format_option
def comments_list(fmt, **params):
    """List and filter first-party comment threads."""
    _validate_list_filters(params)
    query = {
        name: value
        for name, value in params.items()
        if value is not None and name != "fmt"
    }
    if "unanswered" in query:
        query["unanswered"] = "true" if query["unanswered"] else "false"
    data = get_client().get(API, params=query)
    if fmt == "table":
        rows = []
        for row in data.get("comments", []):
            rows.append({
                **row,
                "author_email": (row.get("author") or {}).get("email", ""),
            })
        emit(rows, fmt, columns=TABLE_COLUMNS)
        return
    emit(data, fmt)


def _validate_key(_ctx, _param, value):
    if value is None:
        return None
    value = value.strip()
    if not value:
        raise click.BadParameter("must not be blank")
    if len(value) > 255 or any(ord(char) < 33 or ord(char) > 126 for char in value):
        raise click.BadParameter("must be 1..255 visible ASCII characters")
    return value


@comments.command("reply")
@click.argument("comment_id", type=click.IntRange(min=1))
@click.option("--body")
@click.option("--body-file", type=click.Path(path_type=Path, dir_okay=False))
@click.option("--idempotency-key", required=True, callback=_validate_key)
@format_option
def comments_reply(comment_id, body, body_file, idempotency_key, fmt):
    """Post one direct reply; explicitly reuse the key after uncertain delivery."""
    if (body is None) == (body_file is None):
        _fail("Provide exactly one of --body or --body-file")
    if body_file is not None:
        try:
            body = body_file.read_text(encoding="utf-8")
        except OSError as exc:
            _fail(f"Could not read --body-file: {exc}")
    body = body.strip()
    if not body:
        _fail("Reply body must not be empty")
    if len(body) > 10_000:
        _fail("Reply body must be at most 10000 Unicode code points")
    # Client.request performs one httpx call. There is deliberately no retry
    # around this POST: after an ambiguous failure, rerun with the same key.
    result = get_client().post(
        f"{API}/{comment_id}/replies",
        json_body={"body": body},
        headers={"Idempotency-Key": idempotency_key},
    )
    emit(result, fmt)


groups = [comments]
