"""One-time + ongoing cleanup of already-duplicated event pairs (issue #881).

Lists candidate duplicate pairs (a ``origin='studio'`` event and the
``origin='github', kind='workshop'`` sync artifact for the same session on the
same day) and merges them, folding each duplicate into its canonical Studio
event. Dry-run by default: the plan is printed and NOTHING is written. Pass
``--commit`` to perform the merge.

Usage::

    # Preview every candidate pair (writes nothing):
    uv run python manage.py merge_duplicate_events --all

    # Actually merge every candidate pair:
    uv run python manage.py merge_duplicate_events --all --commit

    # Merge a single explicit pair (canonical, duplicate):
    uv run python manage.py merge_duplicate_events \
        --canonical 12 --duplicate 34 --commit

The merge engine itself lives in ``events.services.event_merge`` so the Studio
tool and this command share one implementation. The audit row's ``user`` FK
needs a logged-in operator, which a CLI run does not have, so the command passes
``actor=None`` and the engine logs the summary instead of row-writing it.
"""

from django.core.management.base import BaseCommand, CommandError

from events.models import Event
from events.services.event_merge import (
    SelfMergeError,
    find_duplicate_event_pairs,
    merge_duplicate_events,
)


class Command(BaseCommand):
    help = "List and merge already-duplicated event pairs (#881)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="Process every detected candidate pair.",
        )
        parser.add_argument(
            "--canonical",
            type=int,
            default=None,
            help="Canonical (surviving) Event pk for a single explicit pair.",
        )
        parser.add_argument(
            "--duplicate",
            type=int,
            default=None,
            help="Duplicate (retired) Event pk for a single explicit pair.",
        )
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Perform the merge. Without this flag the command is a dry run.",
        )

    def handle(self, *args, **options):
        process_all = options["all"]
        canonical_pk = options["canonical"]
        duplicate_pk = options["duplicate"]
        commit = options["commit"]

        if process_all and (canonical_pk or duplicate_pk):
            raise CommandError(
                "Use either --all or an explicit --canonical/--duplicate pair, "
                "not both."
            )
        if not process_all and not (canonical_pk and duplicate_pk):
            raise CommandError(
                "Pass --all to process every candidate, or both --canonical and "
                "--duplicate for a single explicit pair."
            )

        if process_all:
            pairs = find_duplicate_event_pairs()
        else:
            canonical = Event.objects.filter(pk=canonical_pk).first()
            duplicate = Event.objects.filter(pk=duplicate_pk).first()
            if canonical is None:
                raise CommandError(f"No event with pk={canonical_pk}.")
            if duplicate is None:
                raise CommandError(f"No event with pk={duplicate_pk}.")
            pairs = [(canonical, duplicate)]

        mode = "COMMIT" if commit else "DRY-RUN"
        self.stdout.write(
            self.style.NOTICE(f"[{mode}] {len(pairs)} candidate pair(s) found.")
        )

        merged = 0
        skipped = 0
        for canonical, duplicate in pairs:
            try:
                plan = merge_duplicate_events(
                    canonical,
                    duplicate,
                    actor_label="cli:merge_duplicate_events",
                    actor=None,
                    dry_run=not commit,
                )
            except SelfMergeError as exc:
                skipped += 1
                self.stdout.write(self.style.WARNING(f"  skipped: {exc}"))
                continue

            label = (
                f"#{canonical.pk} '{canonical.title}' <- "
                f"#{duplicate.pk} '{duplicate.title}'"
            )
            if plan.already_merged:
                skipped += 1
                self.stdout.write(f"  already merged, skipped: {label}")
                continue

            merged += 1
            verb = "merged" if commit else "would merge"
            self.stdout.write(
                f"  {verb}: {label} "
                f"(moved={plan.registrations_moved}, "
                f"deduped={plan.registrations_deduped}, "
                f"filled={sorted(plan.fields_filled.keys()) or 'none'}, "
                f"workshop_relinked={plan.workshop_relinked})"
            )

        summary = (
            f"Done. pairs={len(pairs)} "
            f"{'merged' if commit else 'to_merge'}={merged} skipped={skipped}"
        )
        if commit:
            self.stdout.write(self.style.SUCCESS(summary))
        else:
            self.stdout.write(
                self.style.NOTICE(summary + " (dry run — nothing written)")
            )
