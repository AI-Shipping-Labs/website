"""Retroactive `#plan-sprints` backfill command (issue #904).

The daily ingest (issue #889/#890/#891) only looks back
``FIRST_RUN_LOOKBACK_DAYS`` on its very first run and then rides a forward
watermark, so there is no way to pull OLDER channel history. This command
runs the same capture + parse + auto-apply path over an explicit
``--since`` date so a one-time retroactive backfill can be performed; the
daily task then keeps things current.

Dry-run is the default: the run is executed in full but rolled back, so an
operator sees exactly what a real run WOULD write before committing with
``--commit``. The underlying ingest is idempotent — the
``IngestedProgressEvent`` / ``AppliedProgressChange`` watermarks make a
committed re-run over the same window safe.

``--reparse`` (issue #927) is a separate operator path: instead of reading
channel history, it iterates the EXISTING persisted threads in the ``--since``
window, re-reads ``conversations.replies`` for each (forcing a re-fetch even
when the forward watermark would skip them), appends any genuinely-new reply
rows, recomputes ``reply_count``, and re-runs the Phase 2 parse + auto-apply.
It is idempotent: when no new reply arrives it adds 0 rows and applies 0
changes (the ``source_message_ts`` watermark holds). Dry-run by default.
"""

from datetime import date, datetime

from django.core.management.base import BaseCommand, CommandError

from crm.tasks.ingest_plan_sprints import ingest_plan_sprints, reparse_plan_sprints


class Command(BaseCommand):
    help = (
        "Retroactively ingest #plan-sprints history from a given date. "
        "Dry-run by default; pass --commit to persist."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--since",
            required=True,
            help="Read channel history from this date (YYYY-MM-DD, UTC midnight).",
        )
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Persist the ingest. Without this flag the run is rolled back (dry-run).",
        )
        parser.add_argument(
            "--reparse",
            action="store_true",
            help=(
                "Force-re-read conversations.replies for EXISTING threads in the "
                "--since window and re-run the Phase 2 parse for them, even though "
                "the forward watermark would skip them. Idempotent: adds 0 rows and "
                "applies 0 changes when nothing new arrived."
            ),
        )

    def handle(self, *args, **options):
        since_raw = options["since"]
        try:
            since = datetime.strptime(since_raw, "%Y-%m-%d").date()
        except ValueError as exc:
            raise CommandError(
                f"--since must be YYYY-MM-DD, got {since_raw!r}"
            ) from exc

        if since > date.today():
            raise CommandError("--since cannot be in the future")

        dry_run = not options["commit"]
        reparse = options["reparse"]
        mode = "DRY-RUN (no changes persisted)" if dry_run else "COMMIT"
        verb = "Re-parsing existing" if reparse else "Backfilling"
        self.stdout.write(f"{verb} #plan-sprints since {since} [{mode}]")

        if reparse:
            run = reparse_plan_sprints(since=since, dry_run=dry_run)
        else:
            run = ingest_plan_sprints(since=since, dry_run=dry_run)

        if run is None:
            raise CommandError(
                "Ingest skipped: Slack is disabled or #plan-sprints channel "
                "is not configured."
            )

        if run.status == "error":
            self.stderr.write(
                self.style.ERROR(f"Ingest failed: {run.error}")
            )
            raise CommandError("Backfill failed; see the error above.")

        if reparse:
            summary = (
                f"{run.messages_seen} threads re-read, "
                f"{run.replies_added} replies added, "
                f"{run.members_matched} members matched"
            )
        else:
            summary = (
                f"{run.messages_seen} messages seen, "
                f"{run.threads_persisted} new threads, "
                f"{run.replies_added} new replies, "
                f"{run.members_matched} members matched"
            )
        if dry_run:
            self.stdout.write(self.style.WARNING(f"DRY-RUN: would write {summary}"))
            self.stdout.write("Re-run with --commit to persist.")
        else:
            done = "Re-parse complete" if reparse else "Backfill complete"
            self.stdout.write(self.style.SUCCESS(f"{done}: {summary}"))
