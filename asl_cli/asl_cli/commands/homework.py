"""``asl homework`` -- list homework and record submissions on a member's behalf."""

from __future__ import annotations

import pathlib
import sys

import click

from asl_cli.commands._shared import emit, format_option, get_client

API = "/api"


def _text(value: str | None) -> str | None:
    """``@path`` reads a file, ``@-`` reads stdin, anything else is literal."""
    if value is None or not value.startswith("@"):
        return value
    if value == "@-":
        return sys.stdin.read()
    return pathlib.Path(value[1:]).read_text()


def _answers(pairs: tuple[str, ...]) -> dict[str, str]:
    answers = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            raise click.BadParameter(
                f"expected KEY=VALUE, got {pair!r}", param_hint="--answer",
            )
        answers[key.strip()] = _text(value)
    return answers


def _common(body, *, submitted_at, note, replace, dry_run, notify, lip_links):
    if lip_links:
        body["learning_in_public_links"] = list(lip_links)
    if submitted_at:
        body["submitted_at"] = submitted_at
    if note is not None:
        body["note"] = _text(note)
    body["replace"] = replace
    body["dry_run"] = dry_run
    body["notify"] = notify
    return body


def _common_options(func):
    options = [
        click.option(
            "--as", "member", required=True,
            help="Member to submit for: email (primary or alias) or numeric user id.",
        ),
        click.option(
            "--lip-link", "lip_links", multiple=True,
            help="Learning-in-public link (repeatable).",
        ),
        click.option(
            "--submitted-at", default=None,
            help="Backdate the submission (ISO-8601, e.g. 2026-09-01T09:30:00Z). Default: now.",
        ),
        click.option(
            "--note", default=None,
            help="Why staff recorded it, e.g. 'Sent by email 2026-09-01' (or @file).",
        ),
        click.option(
            "--replace", is_flag=True,
            help="Overwrite the member's existing submission (refused by default).",
        ),
        click.option(
            "--dry-run", is_flag=True,
            help="Validate and score, show the result, write nothing.",
        ),
        click.option(
            "--notify", is_flag=True,
            help="Tell the member (bell + email). Default: no notification.",
        ),
    ]
    for option in reversed(options):
        func = option(func)
    return func


@click.group()
def homework():
    """Course homework and staff-recorded submissions."""


@homework.command("list")
@click.argument("course")
@format_option
def homework_list(course, fmt):
    """List COURSE's homework per cohort with the question keys --answer takes (staff-only)."""
    emit(get_client().get(f"{API}/courses/{course}/homework"), fmt)


@homework.command("submit")
@_common_options
@click.option("--course", default=None, help="Course slug (with --homework).")
@click.option("--homework", "homework_slug", default=None, help="Homework slug (see `asl homework list`).")
@click.option(
    "--cohort", default=None,
    help="Cohort key, when the homework slug exists in several cohorts.",
)
@click.option("--homework-id", type=int, default=None, help="Homework id instead of --course/--homework.")
@click.option(
    "--answer", "answer_pairs", multiple=True, metavar="KEY=VALUE",
    help=(
        "Answer for a question key (repeatable). VALUE may be @file or @- (stdin). "
        "Choice questions take the 1-based option index(es), e.g. 2 or 1,3, or the exact option text."
    ),
)
@click.option("--link", "homework_link", default=None, help="Homework URL (e.g. the GitHub repo).")
@click.option("--time-lectures", type=float, default=None, help="Hours spent on lectures.")
@click.option("--time-homework", type=float, default=None, help="Hours spent on the homework.")
@format_option
def homework_submit(
    member, lip_links, submitted_at, note, replace, dry_run, notify,
    course, homework_slug, cohort, homework_id, answer_pairs, homework_link,
    time_lectures, time_homework, fmt,
):
    """Record a homework submission on a member's behalf (staff-only).

    Scored exactly like the member's own submit, marked as recorded by
    staff, and audit-logged with your token. Fails if the member already
    submitted unless --replace. Sends nothing to the member unless --notify.
    """
    if homework_id is None and not (course and homework_slug):
        raise click.UsageError("Pass --course and --homework, or --homework-id.")
    body = {"kind": "homework", "user": member}
    if homework_id is not None:
        body["homework_id"] = homework_id
    else:
        body["course"] = course
        body["homework"] = homework_slug
        if cohort:
            body["cohort"] = cohort
    answers = _answers(answer_pairs)
    if answers:
        body["answers"] = answers
    if homework_link:
        body["homework_link"] = homework_link
    if time_lectures is not None:
        body["time_spent_lectures"] = time_lectures
    if time_homework is not None:
        body["time_spent_homework"] = time_homework
    _common(
        body, submitted_at=submitted_at, note=note, replace=replace,
        dry_run=dry_run, notify=notify, lip_links=lip_links,
    )
    emit(get_client().post(f"{API}/submissions/on-behalf", json_body=body), fmt)


@homework.command("submit-project")
@_common_options
@click.option("--course", required=True, help="Course slug.")
@click.option(
    "--project", default=None,
    help="Dated project attempt slug; omit for a course without dated attempts.",
)
@click.option("--url", "project_url", required=True, help="Project URL (e.g. the GitHub repo).")
@click.option("--description", default=None, help="Project description (or @file / @-).")
@click.option("--commit", "commit_id", default=None, help="Commit hash (7-40 hex characters).")
@click.option("--time-spent", type=float, default=None, help="Hours spent on the project.")
@format_option
def homework_submit_project(
    member, lip_links, submitted_at, note, replace, dry_run, notify,
    course, project, project_url, description, commit_id, time_spent, fmt,
):
    """Record a project submission on a member's behalf (staff-only).

    Same guarantees as `asl homework submit`; a project already in peer
    review is never replaced.
    """
    body = {"kind": "project", "user": member, "course": course, "project_url": project_url}
    if project:
        body["project"] = project
    if description is not None:
        body["description"] = _text(description)
    if commit_id:
        body["commit_id"] = commit_id
    if time_spent is not None:
        body["time_spent"] = time_spent
    _common(
        body, submitted_at=submitted_at, note=note, replace=replace,
        dry_run=dry_run, notify=notify, lip_links=lip_links,
    )
    emit(get_client().post(f"{API}/submissions/on-behalf", json_body=body), fmt)


groups = [homework]
