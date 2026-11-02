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

Live end-to-end check: ``--http`` signs the payload with the configured
``MAVEN_WEBHOOK_SHARED_SECRET`` and POSTs it to the real webhook endpoint, so
auth, view parsing, and the handler are all exercised over HTTP::

    uv run python manage.py replay_maven_event --event user_cohort.enrolled \
        --email me+maventest@example.com --course "LLM Zoomcamp" \
        --cohort "Spring 2026" --http

In ``--http`` mode the payload is sent exactly as built, so an envelope in
``--payload`` also exercises the endpoint's own parsing; redirects are
refused so the secret can never follow one; the URL must be https (http only
for localhost); a ``{"status": "disabled"}`` answer fails the command; and
the secret is never printed. ``--url`` overrides the endpoint (defaults to
the production webhook).
"""

import http.client
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
)

from django.core.management.base import BaseCommand, CommandError

from integrations.maven_config import maven_shared_secret
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

DEFAULT_WEBHOOK_URL = "https://aishippinglabs.com/api/webhooks/maven"


class _NoRedirect(HTTPRedirectHandler):
    """Refuse redirects so the signed request can never follow one."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


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
            "--http",
            action="store_true",
            help=(
                "POST the built payload to the live webhook endpoint instead of "
                "running the handler in-process. Signs with "
                "MAVEN_WEBHOOK_SHARED_SECRET; the secret is never printed."
            ),
        )
        parser.add_argument(
            "--url",
            default=DEFAULT_WEBHOOK_URL,
            help="Webhook endpoint for --http (default: the production endpoint).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report intended actions without writing anything.",
        )

    def handle(self, *args, **options):
        raw_payload = self._build_payload(options)
        dry_run = options["dry_run"]
        if options["http"]:
            if dry_run:
                raise CommandError("--dry-run cannot be combined with --http.")
            self._validate_url(options["url"])
            self.stdout.write(f"HTTP POST to {options['url']}")
            self._print_context(normalize_payload(raw_payload))
            self._post_signed(raw_payload, options["url"])
            return
        payload = normalize_payload(raw_payload)
        self._print_context(payload)
        self._handle_locally(payload, dry_run)

    def _print_context(self, payload):
        self.stdout.write(f"Recipient: {_extract_email(payload) or '(none)'}")
        self.stdout.write(f"Course: {_extract_course(payload) or '(none)'}")
        self.stdout.write(f"Cohort: {_extract_cohort(payload) or '(none)'}")
        self.stdout.write(f"Payload: {json.dumps(payload)}")

    def _handle_locally(self, payload, dry_run):
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no writes"))
        else:
            self.stdout.write("REAL RUN")
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

    def _post_signed(self, raw_payload, url):
        """POST the payload to the live webhook, signed with the shared secret."""
        secret = maven_shared_secret()
        if not secret:
            raise CommandError("MAVEN_WEBHOOK_SHARED_SECRET is not configured.")
        request = Request(
            url,
            data=json.dumps(raw_payload).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Maven-Secret": secret,
            },
        )
        opener = build_opener(_NoRedirect)
        try:
            with opener.open(request, timeout=30) as response:
                body = response.read().decode("utf-8", "replace")
        except HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            message = f"HTTP {exc.code}: {body}"
            if exc.code == 403:
                message += (
                    " (the local MAVEN_WEBHOOK_SHARED_SECRET may not match "
                    "the target environment)"
                )
            if exc.code in (301, 302, 303, 307, 308):
                message += " (redirect refused; check --url)"
            raise CommandError(message) from exc
        except (URLError, TimeoutError, http.client.HTTPException) as exc:
            raise CommandError(f"Endpoint unreachable: {exc}") from exc
        self.stdout.write(self.style.SUCCESS(f"HTTP {response.status}"))
        self.stdout.write(body)
        self._fail_on_disabled(body)

    def _validate_url(self, url):
        parts = urlsplit(url)
        local_http = parts.scheme == "http" and parts.hostname in (
            "localhost",
            "127.0.0.1",
            "::1",
        )
        if parts.scheme == "https" or local_http:
            return
        raise CommandError(
            "--url must be https; plain http is allowed only for localhost."
        )

    def _fail_on_disabled(self, body):
        # A 200 {"status": "disabled"} is not a successful end-to-end proof:
        # the target environment has the Maven flow switched off.
        try:
            parsed = json.loads(body)
        except ValueError:
            return
        if not isinstance(parsed, dict):
            return
        status = parsed.get("status")
        if status == "disabled":
            raise CommandError(
                'Endpoint answered but MAVEN_ENROLLMENT_ENABLED is off there '
                '(status="disabled").'
            )
