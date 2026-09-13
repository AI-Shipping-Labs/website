"""Sync content sources through the package engine (A2.3).

Usage:
    uv run python manage.py sync_content --from-disk ~/git/ai-shipping-labs-content
    uv run python manage.py sync_content --source content
    uv run python manage.py sync_content --force
"""

import os
import shutil
import subprocess
import tempfile

from community_base.content_sync.models import ContentSource as PackageContentSource
from django.core.management.base import BaseCommand, CommandError

from integrations.services.content_sync import run_sync


class _DiskSnapshot:
    """Symlink-free HEAD snapshot of a local clone (dev/rehearsal flow).

    The package ``ImmutableCheckout`` refuses symlinks anywhere in the tree,
    and local content clones commonly carry tooling symlinks (for example
    ``.claude/skills``). A detached worktree of ``HEAD`` gives the engine a
    clean, committed tree while keeping ``git rev-parse HEAD`` working, so
    ``--from-disk`` runs pin the same commit a remote sync would.
    """

    def __init__(self, path):
        self.path = path
        self.temp_dir = None
        self.created_worktree = False
        self.removed_symlinks = 0

    def __enter__(self):
        self.temp_dir = tempfile.mkdtemp(prefix='sync-content-disk-')
        snapshot = os.path.join(self.temp_dir, 'repo')
        self._copy_without_symlinks(self.path, snapshot)
        return snapshot

    def _copy_without_symlinks(self, src_root, dst_root):
        """Copy the working tree, dropping symlinks (never followed by sync)."""
        os.makedirs(dst_root)
        removed = 0
        for current, dirs, files in os.walk(src_root):
            rel = os.path.relpath(current, src_root)
            target_dir = dst_root if rel == '.' else os.path.join(dst_root, rel)
            dirs[:] = [
                d for d in dirs
                if not os.path.islink(os.path.join(current, d))
            ]
            for name in dirs:
                os.makedirs(os.path.join(target_dir, name), exist_ok=True)
            for name in files:
                candidate = os.path.join(current, name)
                if os.path.islink(candidate):
                    removed += 1
                    continue
                shutil.copy2(candidate, os.path.join(target_dir, name))
        self.removed_symlinks = removed

    def _strip_symlinks(self, root):
        # Some committed entries are tooling symlinks (e.g. .claude/skills);
        # the package checkout refuses every symlink, so drop them here.
        # Content sync never followed symlinks under the legacy engine.
        removed = 0
        for current, dirs, files in os.walk(root):
            for name in list(dirs) + files:
                candidate = os.path.join(current, name)
                if os.path.islink(candidate):
                    os.unlink(candidate)
                    removed += 1
                    if name in dirs:
                        dirs.remove(name)
        self.removed_symlinks = removed
        return removed

    def __exit__(self, exc_type, exc, traceback):
        if self.created_worktree and self.temp_dir:
            subprocess.run(
                ['git', 'worktree', 'remove', '--force', self.temp_dir],
                cwd=self.path, capture_output=True, text=True,
            )
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        return False


class Command(BaseCommand):
    help = 'Sync content sources from GitHub or a local disk clone (package engine)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--from-disk',
            type=str,
            default=None,
            help='Path to a local clone of the content repo',
        )
        parser.add_argument(
            '--source',
            type=str,
            default=None,
            help='Content source slug to sync (default: all enabled)',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force a full sync even when the upstream commit is unchanged',
        )

    def handle(self, *args, **options):
        from_disk = options['from_disk']
        force = options['force']

        if from_disk:
            import os

            if os.path.islink(from_disk):
                raise CommandError(
                    f'Disk path is a symlink: {from_disk}. '
                    'Pass the real repository root instead.'
                )
            if not os.path.isdir(from_disk):
                raise CommandError(
                    f'Disk path does not exist: {from_disk}\n'
                    f'Clone it first: git clone '
                    f'git@github.com:AI-Shipping-Labs/content.git {from_disk}'
                )
            # Local-disk runs are development/rehearsal syncs: they must work
            # even when a source is disabled for a missing webhook secret.
            force = True

        sources = PackageContentSource.objects.all()
        if options['source']:
            sources = sources.filter(slug=options['source'])
        elif not force:
            sources = sources.filter(is_enabled=True)
        sources = list(sources)

        if not sources:
            raise CommandError(
                'No matching content sources. '
                'Run: uv run python manage.py seed_content_sources'
            )

        total_created = 0
        total_updated = 0
        has_errors = False

        disk_snapshot = _DiskSnapshot(from_disk or '.')
        with disk_snapshot as repo_dir:
            if from_disk and disk_snapshot.removed_symlinks:
                self.stdout.write(
                    f'  (snapshot: skipped {disk_snapshot.removed_symlinks} '
                    f'symlink(s); content sync never followed them)'
                )
            for source in sources:
                self.stdout.write(f'Syncing {source.repo_name}...')
                result = run_sync(source, repo_dir=repo_dir if from_disk else None, force=force)
                created = result.items_created
                updated = result.items_updated
                total_created += created
                total_updated += updated
                self.stdout.write(
                    f'  {created} created, {updated} updated, '
                    f'{result.items_unchanged} unchanged, {result.items_deleted} deleted'
                )
                for error in (result.errors or []):
                    error_msg = (
                        error.get('error', str(error))
                        if isinstance(error, dict) else str(error)
                    )
                    self.stderr.write(
                        self.style.ERROR(f'  ERROR [{source.repo_name}]: {error_msg}')
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
