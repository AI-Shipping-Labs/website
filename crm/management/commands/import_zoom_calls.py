"""Import historical Zoom 1:1 summaries into the CRM (issue #1951).

Reads the dated summaries from a local ``zoom-calls`` checkout
(``<path>/1x1/*-summary.md``), resolves each participant to a member
email through the staff API (aliases first, then user search), and
records every resolved call through ``POST /api/crm/calls`` with
``source=zoom`` and ``source_ref=<file stem>`` -- the ``(source,
source_ref)`` upsert makes re-runs idempotent.

Dry-run is the default: parse + resolve + report, write nothing.
``--apply`` performs the writes. Files that do not match the summary
shape and names that cannot be uniquely resolved are skipped and
reported, never guessed.
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from crm.services import zoom_call_import


class Command(BaseCommand):
    help = (
        'Import historical Zoom 1:1 summaries from a local zoom-calls '
        'checkout into the CRM via POST /api/crm/calls.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--path',
            required=True,
            help='Local zoom-calls checkout (reads <path>/1x1/*.md).',
        )
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Perform the writes. The default is a dry run.',
        )
        parser.add_argument(
            '--aliases',
            help=(
                'Optional JSON map {"display name or file stem": "email"} '
                'for name variants the user search cannot resolve.'
            ),
        )

    def handle(self, *args, **options):
        root = Path(options['path'])
        if not (root / '1x1').is_dir():
            raise CommandError(f'No 1x1/ directory under {root}')

        aliases = {}
        if options['aliases']:
            try:
                aliases = zoom_call_import.load_aliases(options['aliases'])
            except (OSError, ValueError) as exc:
                raise CommandError(f'Cannot read aliases file: {exc}')

        result = zoom_call_import.run_import(
            root=root,
            client=self._build_client(),
            aliases=aliases,
            apply=bool(options['apply']),
        )

        self._report(result, apply=bool(options['apply']))

    def _build_client(self):
        """Build the API client from the operator's env / repo .env.

        Module-level so tests can patch it with a stub (no network,
        no token). The token is never printed.
        """
        return zoom_call_import.CrmApiClient(
            zoom_call_import.resolve_base_url(),
            zoom_call_import.resolve_staff_token(),
        )

    def _report(self, result, *, apply):
        for skipped in result.skipped_files:
            self.stdout.write(
                f'SKIPPED FILE: {skipped.filename} — {skipped.reason}'
            )
        for row in result.rows:
            parsed = row['parsed']
            resolution = row['resolution']
            if resolution.status == zoom_call_import.MATCHED:
                email = resolution.email
            else:
                email = f'UNRESOLVED: {resolution.reason}'
            self.stdout.write(
                f'{parsed.stem} | {parsed.date.isoformat()} | '
                f'{parsed.name} | {email} | {row["disposition"]}'
            )
        if apply:
            self.stdout.write(
                f'Done: created={result.created} '
                f'updated={result.updated} '
                f'skipped_unresolved={result.skipped_unresolved} '
                f'skipped_files={len(result.skipped_files)}'
            )
        else:
            self.stdout.write(
                f'Dry run (wrote nothing): would_create='
                f'{result.would_create} would_update='
                f'{result.would_update} skipped_unresolved='
                f'{result.skipped_unresolved} skipped_files='
                f'{len(result.skipped_files)}'
            )
