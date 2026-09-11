"""Replay a Maven cohort webhook payload through the real handler (issue #960).

Lets the owner verify the full flow without waiting on a live Maven delivery.
Feeds a sample (or supplied) payload through the SAME
``integrations.services.maven.handle_maven_event`` the webhook view calls, so
account resolve/create, override grant, Slack invite, welcome email, and the
removal notification all run for real.

Usage
=====

Dry-run first (no writes; reports intended actions)::

    uv run python manage.py replay_maven_event \
        --event user_cohort.enrolled --email me@example.com \
        --course "LLM Zoomcamp" --cohort "Spring 2026" --dry-run

Then for real (idempotent — a second run reports already_processed)::

    uv run python manage.py replay_maven_event \
        --event user_cohort.enrolled --email me@example.com \
        --course "LLM Zoomcamp" --cohort "Spring 2026"

``--course`` and ``--cohort`` have no defaults: they appear in the enrollee's
subject line, so a forgotten flag raises a ``CommandError`` instead of mailing
a real person about a placeholder course.

Supply a full sample body instead of the built-in default::

    uv run python manage.py replay_maven_event --payload ./sample.json
    uv run python manage.py replay_maven_event --payload '{"event": "...", ...}'
"""

import json

from django.core.management.base import BaseCommand, CommandError

from integrations.services.maven import (
    EVENT_ENROLLED,
    EVENT_REMOVED,
    MavenTransientError,
    _extract_cohort,
    _extract_course,
    _extract_email,
    handle_maven_event,
    normalize_payload,
)


class Command(BaseCommand):
    help = "Replay a sample Maven cohort webhook payload through the real handler."

    def add_arguments(self, parser):
        parser.add_argument(
            "--event",
            choices=[EVENT_ENROLLED, EVENT_REMOVED],
            default=EVENT_ENROLLED,
            help="Event type to replay (default: user_cohort.enrolled).",
        )
        parser.add_argument("--email", default="maven-test@example.com")
        # Issue #1565: no defaults. These values land in the enrollee's
        # subject line, so a forgotten flag must fail loudly rather than
        # mail a real person about a placeholder course.
        parser.add_argument("--cohort", default=None)
        parser.add_argument("--course", default=None)
        parser.add_argument(
            "--payload",
            default=None,
            help="Path to a JSON file OR an inline JSON string for a full payload.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report intended actions without writing anything.",
        )

    def handle(self, *args, **options):
        payload = normalize_payload(self._build_payload(options))
        dry_run = options["dry_run"]

        self.stdout.write(
            self.style.WARNING("DRY RUN — no writes") if dry_run else "REAL RUN"
        )
        self.stdout.write(f"Recipient: {_extract_email(payload) or '(none)'}")
        self.stdout.write(f"Course: {_extract_course(payload) or '(none)'}")
        self.stdout.write(f"Cohort: {_extract_cohort(payload) or '(none)'}")
        self.stdout.write(f"Payload: {json.dumps(payload)}")

        try:
            result = handle_maven_event(payload, dry_run=dry_run)
        except ValueError as exc:
            raise CommandError(f"Bad payload: {exc}") from exc
        except MavenTransientError as exc:
            raise CommandError(f"Transient failure (would be a 500 + retry): {exc}") from exc

        self.stdout.write(self.style.SUCCESS(f"Status: {result.status}"))
        if result.user_id is not None:
            self.stdout.write(
                f"User: #{result.user_id} (created={result.created_user})"
            )
        self.stdout.write("Actions:")
        for line in result.actions:
            self.stdout.write(f"  - {line}")

    def _build_payload(self, options):
        if options["payload"]:
            return self._load_payload(options["payload"])
        missing = [
            flag
            for flag in ("course", "cohort")
            if not str(options[flag] or "").strip()
        ]
        if missing:
            raise CommandError(
                "Missing required "
                + " and ".join(f"--{flag}" for flag in missing)
                + ". These values are used in the enrollee's subject line, so "
                "there is no safe default. Pass them explicitly, or supply a "
                "full body with --payload."
            )
        return {
            "event": options["event"],
            "email": options["email"],
            "cohort": options["cohort"],
            "course": options["course"],
        }

    def _load_payload(self, value):
        # Inline JSON object?
        stripped = value.strip()
        if stripped.startswith("{"):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise CommandError(f"Invalid inline JSON: {exc}") from exc
        # Otherwise treat as a file path.
        try:
            with open(value, encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError(f"Could not read --payload {value!r}: {exc}") from exc
