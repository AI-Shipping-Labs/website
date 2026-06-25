"""Backfill the standard Zoom meeting settings onto existing upcoming meetings
(issue #1004).

The config change in ``integrations/services/zoom.py`` only affects NEWLY
created meetings. Meetings created before the change still carry the old
settings on Zoom's side. This command PATCHes the full current settings payload
(the configured ``auto_recording`` — ``cloud`` by default — plus
``join_before_host: False`` and the configured waiting-room flag) onto every
upcoming event that already has a ``zoom_meeting_id``, WITHOUT recreating the
meeting — so the join URL (already mailed out in calendar invites and shown on
event pages) is preserved. Because it sends the whole settings body, running it
turns ON cloud auto-recording for any pre-existing meeting that lacked it
(provided cloud recording is enabled at the Zoom account level, #1081).

Usage::

    # Preview the meetings that would be patched (no Zoom calls):
    uv run python manage.py apply_zoom_meeting_settings --dry-run

    # Patch every upcoming Zoom meeting in place:
    uv run python manage.py apply_zoom_meeting_settings

Idempotent: PATCHing already-correct settings is a no-op on Zoom's side, so
re-running is safe. Partial-failure tolerant: a single per-event PATCH error is
logged and counted but does not abort the run — remaining events are still
processed (mirrors ``events/tasks/create_series_zoom_meetings.py``).
"""

from django.core.management.base import BaseCommand

from events.models import Event
from integrations.services.zoom import update_meeting_settings


class Command(BaseCommand):
    help = (
        "Apply the standard Zoom meeting settings (auto_recording — cloud by "
        "default — plus join-before-host off and the configured waiting-room "
        "flag) to existing upcoming meetings without changing their join URL "
        "(#1004, #1081)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help=(
                "List the events that would be patched without calling Zoom."
            ),
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        # ``is_upcoming`` is time-derived (not a DB field), so filter in Python.
        # Narrow the queryset first to events that actually have a meeting id.
        targets = [
            event
            for event in Event.objects.exclude(zoom_meeting_id="")
            if event.is_upcoming
        ]

        mode = "DRY-RUN" if dry_run else "APPLY"
        self.stdout.write(
            self.style.NOTICE(
                f"[{mode}] {len(targets)} upcoming Zoom meeting(s) to update."
            )
        )

        patched = 0
        failed = 0
        for event in targets:
            label = f"#{event.pk} '{event.title}' (meeting {event.zoom_meeting_id})"

            if dry_run:
                self.stdout.write(f"  would patch: {label}")
                continue

            try:
                update_meeting_settings(event)
            except Exception as exc:  # noqa: BLE001 - resilient batch
                # One failure (e.g. a Zoom 429 or ZoomAPIError) must not abort
                # the run; record it and keep processing the rest.
                failed += 1
                self.stderr.write(
                    self.style.ERROR(f"  failed: {label} — {exc}")
                )
                continue

            patched += 1
            self.stdout.write(f"  patched: {label}")

        if dry_run:
            self.stdout.write(
                self.style.NOTICE(
                    f"Done (dry run). Would patch {len(targets)} meeting(s); "
                    "nothing was changed."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Patched {patched} meeting(s), failed {failed}."
                )
            )
