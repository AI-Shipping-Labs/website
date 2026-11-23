"""``asl announcements`` -- cohort-scoped course announcements.

Every announcement targets one cohort of its course; its first publish
emails that cohort's active enrollments once. ``preview`` shows the exact
recipient count without sending anything, and ``publish`` refuses to run
without a cohort on a multi-cohort course and without ``--yes``.
"""

from __future__ import annotations

import pathlib
import sys

import click

from asl_cli.commands._shared import emit, format_option, get_client

API = "/api"

BLOCKED_MESSAGES = {
    "no_cohort": "it has no cohort, so publishing would email nobody",
    "already_published": "it is already published; publishing again sends no email",
    "already_emailed": "its email was already sent; it is never sent twice",
}


def _text(value: str | None) -> str | None:
    """``@path`` reads a file, ``@-`` reads stdin, anything else is literal."""
    if value is None or not value.startswith("@"):
        return value
    if value == "@-":
        return sys.stdin.read()
    return pathlib.Path(value[1:]).read_text()


def _cohort_options(func):
    func = click.option(
        "--cohort-id", type=int, default=None,
        help="Target cohort by numeric id (instead of --cohort).",
    )(func)
    return click.option(
        "--cohort", default=None,
        help="Target cohort by external key, e.g. 4 (see 'asl sprints course-cohorts COURSE').",
    )(func)


def _cohort_body(cohort, cohort_id):
    if cohort is not None and cohort_id is not None:
        raise click.UsageError("Pass --cohort or --cohort-id, not both.")
    if cohort is not None:
        return {"cohort": cohort}
    if cohort_id is not None:
        return {"cohort_id": cohort_id}
    return {}


def _detail(course, announcement_id):
    return f"{API}/courses/{course}/announcements/{announcement_id}"


def _get_preview(course, announcement_id, cohort_body):
    return get_client().get(
        f"{_detail(course, announcement_id)}/recipients",
        params=cohort_body or None,
    )


def _cohort_label(cohort):
    return f'{cohort["name"]} (key {cohort["external_key"]}, id {cohort["id"]})'


def _summary(preview):
    if preview["cohort"] is None:
        target = "no cohort"
    else:
        target = _cohort_label(preview["cohort"])
    return (
        f'Announcement #{preview["announcement_id"]} on {preview["course"]} '
        f'[{preview["status"]}] -> {target}: '
        f'{preview["recipient_count"]} recipient(s). '
        f'(Whole course: {preview["course_active_enrollment_count"]} active '
        f'enrollments across {preview["course_cohort_count"]} cohort(s); '
        "only the cohort is ever emailed.)"
    )


@click.group()
def announcements():
    """Cohort-scoped course announcements (preview before publishing)."""


@announcements.command("list")
@click.argument("course")
@click.option("--status", type=click.Choice(["draft", "published"]), default=None)
@click.option("--cohort", default=None, help="Only this cohort external key.")
@format_option
def announcements_list(course, status, cohort, fmt):
    """List COURSE's announcements with their cohort (staff-only)."""
    params = {k: v for k, v in (("status", status), ("cohort", cohort)) if v}
    data = get_client().get(
        f"{API}/courses/{course}/announcements", params=params or None,
    )
    if fmt == "table":
        rows = [{
            "id": row["id"],
            "status": row["status"],
            "cohort": (row["cohort"] or {}).get("external_key", ""),
            "email_queued_at": row["email_queued_at"] or "",
            "title": row["title"],
        } for row in data["announcements"]]
        emit(rows, fmt, columns=["id", "status", "cohort", "email_queued_at", "title"])
        return
    emit(data, fmt)


@announcements.command("show")
@click.argument("course")
@click.argument("announcement_id", type=int)
@format_option
def announcements_show(course, announcement_id, fmt):
    """Show one announcement of COURSE (staff-only)."""
    emit(get_client().get(_detail(course, announcement_id)), fmt)


@announcements.command("create")
@click.argument("course")
@_cohort_options
@click.option("--title", required=True, help="Announcement title.")
@click.option("--body", required=True, help="Markdown body, @file, or @- for stdin.")
@format_option
def announcements_create(course, cohort, cohort_id, title, body, fmt):
    """Create a DRAFT announcement for one cohort of COURSE. Sends nothing.

    Publish it afterwards with 'asl announcements publish'.
    """
    target = _cohort_body(cohort, cohort_id)
    if not target:
        raise click.UsageError("A cohort is required: pass --cohort KEY or --cohort-id ID.")
    payload = {"title": title, "body": _text(body), "status": "draft", **target}
    created = get_client().post(
        f"{API}/courses/{course}/announcements", json_body=payload,
    )
    emit(created, fmt)
    click.echo(
        f'Draft #{created["id"]} created for {_cohort_label(created["cohort"])}. '
        f'Preview recipients: asl announcements preview {course} {created["id"]}',
        err=True,
    )


@announcements.command("update")
@click.argument("course")
@click.argument("announcement_id", type=int)
@_cohort_options
@click.option("--title", default=None, help="New title.")
@click.option("--body", default=None, help="New markdown body, @file, or @- for stdin.")
@format_option
def announcements_update(course, announcement_id, cohort, cohort_id, title, body, fmt):
    """Edit an announcement's title, body, or (while a draft) cohort. Sends nothing."""
    payload = _cohort_body(cohort, cohort_id)
    if title is not None:
        payload["title"] = title
    if body is not None:
        payload["body"] = _text(body)
    if not payload:
        raise click.UsageError("Nothing to update: pass --title, --body, --cohort or --cohort-id.")
    emit(get_client().patch(_detail(course, announcement_id), json_body=payload), fmt)


@announcements.command("preview")
@click.argument("course")
@click.argument("announcement_id", type=int)
@_cohort_options
@format_option
def announcements_preview(course, announcement_id, cohort, cohort_id, fmt):
    """Show the exact recipient count and cohort WITHOUT sending anything.

    --cohort/--cohort-id previews another cohort without storing it.
    """
    preview = _get_preview(course, announcement_id, _cohort_body(cohort, cohort_id))
    emit(preview, fmt)
    click.echo(_summary(preview), err=True)


@announcements.command("publish")
@click.argument("course")
@click.argument("announcement_id", type=int)
@_cohort_options
@click.option("--dry-run", is_flag=True, help="Show who would be emailed; publish nothing.")
@click.option("--yes", is_flag=True, help="Confirm: publish and email the previewed cohort.")
@format_option
def announcements_publish(course, announcement_id, cohort, cohort_id, dry_run, yes, fmt):
    """Publish a draft and email its cohort ONCE.

    Always previews first and prints the recipient count. Refuses to run
    without a cohort on a multi-cohort course, when --cohort disagrees with
    the draft's stored cohort, and without --yes. The publish request carries
    the previewed count, so the server refuses it if the audience changed.
    """
    target = _cohort_body(cohort, cohort_id)
    preview = _get_preview(course, announcement_id, target)
    click.echo(_summary(preview), err=True)

    resolved = preview["cohort"]
    stored_id = preview["stored_cohort_id"]
    if not target and preview["course_cohort_count"] > 1:
        raise click.ClickException(
            f'Refusing to publish: {preview["course"]} has '
            f'{preview["course_cohort_count"]} cohorts. Name the audience '
            "explicitly with --cohort KEY (or --cohort-id ID)"
            + (f', e.g. --cohort {resolved["external_key"]}.' if resolved else ".")
        )
    if resolved is None:
        raise click.ClickException(
            "Refusing to publish: the announcement has no cohort. "
            "Pass --cohort KEY (or --cohort-id ID)."
        )
    if target and stored_id is not None and stored_id != resolved["id"]:
        raise click.ClickException(
            f"Refusing to publish: the draft is scoped to cohort id {stored_id}, "
            f"not {_cohort_label(resolved)}. Change it first with "
            f"'asl announcements update {course} {announcement_id} "
            f"--cohort {resolved['external_key']}', then preview again."
        )
    if preview["blocked_reason"]:
        reason = BLOCKED_MESSAGES.get(preview["blocked_reason"], preview["blocked_reason"])
        raise click.ClickException(f"Refusing to publish: {reason}.")

    if dry_run:
        emit(preview, fmt)
        click.echo("Dry run: nothing published, nothing sent.", err=True)
        return
    if not yes:
        raise click.ClickException(
            f'Not published. Re-run with --yes to publish and email these '
            f'{preview["recipient_count"]} learner(s) of {resolved["name"]}.'
        )

    payload = {
        "status": "published",
        "expected_recipient_count": preview["recipient_count"],
    }
    if stored_id is None:
        payload["cohort_id"] = resolved["id"]
    published = get_client().patch(_detail(course, announcement_id), json_body=payload)
    emit(published, fmt)
    click.echo(
        f'Published #{announcement_id}; emailing {preview["recipient_count"]} '
        f'learner(s) of {resolved["name"]} once.',
        err=True,
    )


# ``asl announcement ...`` works too.
announcement_alias = click.Group(
    name="announcement",
    commands=announcements.commands,
    help="Alias of 'asl announcements'.",
    hidden=True,
)

groups = [announcements, announcement_alias]
