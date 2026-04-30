from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, ProgrammingError

from content.services.qa_fixture_cleanup import (
    delete_cleanup_candidates,
    find_cleanup_candidates,
)


class Command(BaseCommand):
    help = (
        "Dry-run cleanup for obvious unsynced Playwright/QA content fixtures "
        "in the current database. Pass --apply to delete candidates."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Delete the listed candidates. Without this flag, nothing is deleted.',
        )

    def handle(self, *args, **options):
        try:
            candidates = find_cleanup_candidates()
        except (OperationalError, ProgrammingError) as exc:
            raise CommandError(
                'Could not inspect content tables. Run '
                '`uv run python manage.py migrate` for this database first.'
            ) from exc
        if not candidates:
            self.stdout.write('No likely unsynced QA/test fixture rows found.')
            return

        action = 'Deleting' if options['apply'] else 'Dry run: would delete'
        self.stdout.write(f'{action} {len(candidates)} likely unsynced QA/test fixture row(s):')
        for candidate in candidates:
            label = f'{candidate.model_label}#{candidate.pk}'
            descriptor = candidate.title or candidate.slug
            self.stdout.write(f'- {label}: {descriptor} ({candidate.slug})')

        if not options['apply']:
            self.stdout.write('No rows deleted. Re-run with --apply to delete these candidates.')
            return

        deleted_count = delete_cleanup_candidates(candidates)
        self.stdout.write(self.style.SUCCESS(f'Deleted {deleted_count} row(s).'))
