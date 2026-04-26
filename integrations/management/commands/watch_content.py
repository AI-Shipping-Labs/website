"""Management command to watch a local content repo for changes and auto-sync.

Usage:
    uv run python manage.py watch_content ~/git/ai-shipping-labs-content
    uv run python manage.py watch_content ~/git/ai-shipping-labs-content --debounce 5

Issue #310: with one ``ContentSource`` per repo, the watcher debounces a
single sync per repo rather than per content type. The TIERS_SENTINEL
shortcut is preserved for ``tiers.yaml``-only updates so a tier-config
edit doesn't kick off the whole repo walker.
"""

import os
import threading
import time
from pathlib import Path

import yaml
from django.core.management.base import BaseCommand, CommandError

from integrations.models import ContentSource
from integrations.services.github import sync_content_source

# File extensions that should trigger a sync
CONTENT_EXTENSIONS = {'.md', '.yaml', '.yml'}

# Patterns to ignore (dotfiles, editor temps, caches)
IGNORE_PREFIXES = ('.', '__pycache__')
IGNORE_SUFFIXES = ('.swp', '.swo', '~', '.tmp', '.bak')

# Sentinel value for tiers.yaml changes
TIERS_SENTINEL = 'tiers'


def _is_content_file(rel_path):
    """Check if a relative path is a content file we should react to.

    Returns True for .md, .yaml, .yml files that are not dotfiles,
    editor temp files, or inside ignored directories.
    """
    parts = Path(rel_path).parts
    for part in parts:
        for prefix in IGNORE_PREFIXES:
            if part.startswith(prefix):
                return False
    name = Path(rel_path).name
    for suffix in IGNORE_SUFFIXES:
        if name.endswith(suffix):
            return False
    ext = Path(rel_path).suffix.lower()
    return ext in CONTENT_EXTENSIONS


class DebouncedSyncer:
    """Collects file change events and triggers syncs after a debounce delay.

    Thread-safe. Each repo has its own independent debounce timer.
    """

    def __init__(self, debounce_seconds, repo_dir, stdout, stderr, style):
        self.debounce_seconds = debounce_seconds
        self.repo_dir = repo_dir
        self.stdout = stdout
        self.stderr = stderr
        self.style = style
        self._lock = threading.Lock()
        # Maps key (str) -> Timer
        self._timers = {}
        # Maps key (str) -> ContentSource (or TIERS_SENTINEL)
        self._pending = {}

    def schedule(self, key, target):
        """Schedule a sync for ``target`` after the debounce delay.

        If a sync is already pending for this key, the timer is reset.
        """
        with self._lock:
            if key in self._timers:
                self._timers[key].cancel()

            self._pending[key] = target
            timer = threading.Timer(
                self.debounce_seconds,
                self._execute_sync,
                args=[key],
            )
            timer.daemon = True
            timer.start()
            self._timers[key] = timer

    def _execute_sync(self, key):
        """Execute the sync after debounce expires."""
        with self._lock:
            target = self._pending.pop(key, None)
            self._timers.pop(key, None)

        if target is None:
            return

        if target == TIERS_SENTINEL:
            self._sync_tiers()
        else:
            self._sync_content_source(target)

    def _sync_tiers(self):
        """Read tiers.yaml and clear the tier config cache."""
        self.stdout.write(self.style.NOTICE('Syncing tiers.yaml...'))
        try:
            tiers_path = Path(self.repo_dir) / 'tiers.yaml'
            if not tiers_path.exists():
                self.stderr.write('tiers.yaml not found, skipping.')
                return
            with open(tiers_path, encoding='utf-8') as f:
                tiers_data = yaml.safe_load(f) or []
            from content.models import SiteConfig
            SiteConfig.objects.update_or_create(
                key='tiers',
                defaults={'data': tiers_data},
            )
            self.stdout.write(self.style.SUCCESS('tiers.yaml reloaded.'))
        except Exception as e:
            self.stderr.write(f'Error syncing tiers.yaml: {e}')

    def _sync_content_source(self, source):
        """Run sync_content_source for a single repo."""
        self.stdout.write(
            self.style.NOTICE(f'Syncing {source.repo_name}...')
        )
        try:
            sync_log = sync_content_source(source, repo_dir=self.repo_dir)
            self.stdout.write(self.style.SUCCESS(
                f'  {source.repo_name}: '
                f'created={sync_log.items_created}, '
                f'updated={sync_log.items_updated}, '
                f'deleted={sync_log.items_deleted}'
            ))
        except Exception as e:
            self.stderr.write(f'Error syncing {source.repo_name}: {e}')

    def cancel_all(self):
        """Cancel all pending timers."""
        with self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
            self._pending.clear()


class Command(BaseCommand):
    help = 'Watch a local content repo for changes and auto-sync to the database.'

    def add_arguments(self, parser):
        parser.add_argument(
            'repo_dir',
            type=str,
            help='Path to the local content repo clone.',
        )
        parser.add_argument(
            '--debounce',
            type=float,
            default=2.0,
            help='Seconds to wait after a change before syncing (default: 2).',
        )

    def handle(self, *args, **options):
        # Import watchdog here so the module can be imported without it
        # installed (for testing internal helpers)
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            raise CommandError(
                'watchdog is not installed. Run: uv add watchdog'
            )

        repo_dir = os.path.expanduser(options['repo_dir'])
        debounce = options['debounce']

        if not os.path.isdir(repo_dir):
            raise CommandError(f'Directory does not exist: {repo_dir}')

        # The watcher debounces against a single repo. We pick the
        # ContentSource whose repo matches the directory's basename if
        # one exists; otherwise the first registered source. The watch
        # debounces all changes (apart from tiers.yaml) into one sync of
        # that repo.
        sources = list(ContentSource.objects.all())
        if not sources:
            raise CommandError(
                'No ContentSource records found. '
                'Run: uv run python manage.py seed_content_sources'
            )

        # Try to match by repo basename, fall back to first source.
        repo_basename = os.path.basename(os.path.normpath(repo_dir))
        target_source = next(
            (
                s for s in sources
                if s.repo_name.split('/')[-1] == repo_basename
            ),
            sources[0],
        )

        # Print startup info
        self.stdout.write(self.style.SUCCESS('Content file watcher'))
        self.stdout.write(f'  Repo: {repo_dir}')
        self.stdout.write(f'  Debounce: {debounce}s')
        self.stdout.write(f'  Synced as: {target_source.repo_name}')
        self.stdout.write('  Triggers:')
        self.stdout.write('    *.md / *.yaml / *.yml -> repo sync')
        self.stdout.write('    tiers.yaml -> tiers config only')
        self.stdout.write('')

        syncer = DebouncedSyncer(
            debounce_seconds=debounce,
            repo_dir=repo_dir,
            stdout=self.stdout,
            stderr=self.stderr,
            style=self.style,
        )

        class ContentEventHandler(FileSystemEventHandler):
            def on_any_event(handler_self, event):
                if event.is_directory:
                    return

                src_path = event.src_path
                try:
                    rel_path = os.path.relpath(src_path, repo_dir)
                except ValueError:
                    return

                if not _is_content_file(rel_path):
                    return

                # tiers.yaml at the root has its own debounce key so a
                # tier-config edit doesn't drag the whole repo through a
                # full walker.
                normalized = rel_path.replace(os.sep, '/')
                if normalized == 'tiers.yaml':
                    target = TIERS_SENTINEL
                    key = TIERS_SENTINEL
                    type_label = 'tiers'
                else:
                    target = target_source
                    key = target_source.repo_name
                    type_label = 'repo'

                event_labels = {
                    'created': 'created',
                    'modified': 'modified',
                    'deleted': 'deleted',
                    'moved': 'moved',
                }
                event_type = getattr(event, 'event_type', 'modified')
                label = event_labels.get(event_type, event_type)

                self.stdout.write(
                    f'  [{label}] {rel_path} -> {type_label}'
                )
                syncer.schedule(key, target)

        observer = Observer()
        observer.schedule(
            ContentEventHandler(), repo_dir, recursive=True
        )
        observer.start()
        self.stdout.write(
            self.style.SUCCESS('Watching for changes... (Ctrl+C to stop)')
        )

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stdout.write('')
            self.stdout.write('Shutting down watcher...')
            syncer.cancel_all()
            observer.stop()

        observer.join()
        self.stdout.write(self.style.SUCCESS('Watcher stopped.'))
