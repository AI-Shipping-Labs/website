"""
Management command to sync all content from GitHub (or a local clone) to the database.

Usage:
    uv run python manage.py sync_content --from-disk ~/git/ai-shipping-labs-content
    uv run python manage.py sync_content  # (from GitHub, requires credentials)
"""

import os

from django.core.management.base import BaseCommand, CommandError

from integrations.models import ContentSource
from integrations.services.github import sync_content_source
from integrations.services.github_sync.checkout import (
    ContentCheckoutError,
    checkout_is_dir,
    checkout_is_file,
    checkout_scope,
)
from integrations.services.github_sync.dispatchers.tiers import _sync_tiers_yaml


class Command(BaseCommand):
    help = 'Sync all content sources from GitHub or a local disk clone'

    def add_arguments(self, parser):
        parser.add_argument(
            '--from-disk',
            type=str,
            default=None,
            help='Path to a local clone of the content repo',
        )

    def handle(self, *args, **options):
        from_disk = options['from_disk']

        if from_disk and not checkout_is_dir(from_disk):
            raise CommandError(
                f'Disk path does not exist: {from_disk}\n'
                f'Clone it first: git clone git@github.com:AI-Shipping-Labs/content.git {from_disk}'
            )

        sources = ContentSource.objects.all()
        if not sources.exists():
            raise CommandError(
                'No content sources configured. '
                'Run: uv run python manage.py seed_content_sources'
            )

        total_created = 0
        total_updated = 0
        has_errors = False
        boundary_failed = False

        for source in sources:
            self.stdout.write(f'Syncing {source.repo_name}...')
            try:
                kwargs = {}
                if from_disk:
                    kwargs['repo_dir'] = from_disk
                result = sync_content_source(source, **kwargs)
                created = result.items_created
                updated = result.items_updated
                total_created += created
                total_updated += updated
                self.stdout.write(f'  {created} created, {updated} updated')
                for error in (result.errors or []):
                    error_msg = error.get('error', str(error)) if isinstance(error, dict) else str(error)
                    if (
                        isinstance(error, dict)
                        and error.get('filesystem_boundary') is True
                    ):
                        boundary_failed = True
                    self.stderr.write(
                        self.style.ERROR(
                            f'  ERROR [{source.repo_name}]: {error_msg}'
                        )
                    )
                    has_errors = True
            except ContentCheckoutError as e:
                boundary_failed = True
                self.stderr.write(
                    self.style.ERROR(f'  FAILED [{source.repo_name}]: {e}')
                )
                has_errors = True
            except Exception as e:
                self.stderr.write(
                    self.style.ERROR(f'  FAILED [{source.repo_name}]: {e}')
                )
                has_errors = True

        # Keep local-disk sync semantics identical to production GitHub sync:
        # write the full config blob and the scalar Tier fields together.
        if from_disk and not boundary_failed:
            with checkout_scope(from_disk):
                tiers_path = os.path.join(from_disk, 'tiers.yaml')
                if checkout_is_file(tiers_path):
                    self.stdout.write('Syncing tiers.yaml...')
                    try:
                        result = _sync_tiers_yaml(from_disk)
                        if result['synced']:
                            self.stdout.write('  tiers.yaml synced to database')
                        else:
                            self.stderr.write(
                                self.style.ERROR('  FAILED to sync tiers.yaml')
                            )
                            has_errors = True
                    except Exception as e:
                        self.stderr.write(
                            self.style.ERROR(f'  FAILED to sync tiers.yaml: {e}')
                        )
                        has_errors = True

        self.stdout.write('')
        self.stdout.write(
            self.style.SUCCESS(
                f'Done. {total_created} created, {total_updated} updated total.'
            )
        )

        if has_errors:
            raise CommandError('Content sync completed with errors.')
