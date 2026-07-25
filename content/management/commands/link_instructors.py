"""Reconcile instructor account links (issue #1345).

For each ``Instructor`` with a non-empty ``email`` and a NULL ``user``, link
it to the verified platform account whose email iexact-matches. Operator
overrides (a non-null ``user``) are never touched. Idempotent — a second run
with no new verified accounts links nothing.
"""

from django.core.management.base import BaseCommand

from content.services.instructor_linking import reconcile_instructors


class Command(BaseCommand):
    help = (
        'Auto-link instructors to verified platform accounts by matching '
        'email. Fills only NULL links; never overwrites operator overrides.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Print what would be linked without writing to the database.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        summary = reconcile_instructors(dry_run=dry_run)

        prefix = '[dry-run] ' if dry_run else ''
        verb = 'would link' if dry_run else 'linked'
        self.stdout.write(
            f'{prefix}Instructor auto-link reconciliation:'
        )
        self.stdout.write(f'  scanned:        {summary["scanned"]}')
        self.stdout.write(
            f'  newly {verb}: {summary["newly_linked"]}'
        )
        self.stdout.write(
            f'  left unlinked:  {summary["left_unlinked"]} '
            f'(no email: {summary["no_email"]}, '
            f'no verified match: {summary["no_match"]})'
        )
        self.stdout.write(
            f'  already linked: {summary["already_linked"]}'
        )
