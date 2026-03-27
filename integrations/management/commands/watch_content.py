"""Management command to watch a local content repo for changes and auto-sync.

Usage:
    uv run python manage.py watch_content ~/git/ai-shipping-labs-content
    uv run python manage.py watch_content ~/git/ai-shipping-labs-content --debounce 5
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


def _get_content_type_for_path(rel_path, path_to_source):
    """Map a relative file path to a ContentSource or the tiers sentinel.

    Args:
        rel_path: Path relative to the repo root (e.g. "blog/my-article.md").
        path_to_source: Dict mapping content_path prefixes to ContentSource instances.

    Returns:
        A ContentSource instance, the string "tiers" for tiers.yaml, or None.
    """
    normalized = rel_path.replace(os.sep, '/')
    # Check for tiers.yaml at root
    if normalized == 'tiers.yaml':
        return TIERS_SENTINEL

    # Match on first path component
    top_dir = normalized.split('/')[0]
    return path_to_source.get(top_dir)


def _build_path_mapping(sources):
    """Build a mapping from content_path to ContentSource.

    Args:
        sources: QuerySet of ContentSource objects.

    Returns:
        Dict[str, ContentSource] mapping content_path to source.
    """
    mapping = {}
    for source in sources:
        if source.content_path:
            mapping[source.content_path] = source
    return mapping


class DebouncedSyncer:
    """Collects file change events and triggers syncs after a debounce delay.

    Thread-safe. Each content type has its own independent debounce timer.
    """

    def __init__(self, debounce_seconds, repo_dir, stdout, stderr, style):
        self.debounce_seconds = debounce_seconds
        self.repo_dir = repo_dir
        self.stdout = stdout
        self.stderr = stderr
        self.style = style
        self._lock = threading.Lock()
        # Maps content_type (str) -> Timer
        self._timers = {}
        # Maps content_type (str) -> ContentSource (or TIERS_SENTINEL)
        self._pending = {}

    def schedule(self, content_type_key, source):
        """Schedule a sync for the given content type after the debounce delay.

        If a sync is already pending for this content type, the timer is reset.

        Args:
            content_type_key: String key identifying the content type.
            source: ContentSource instance or TIERS_SENTINEL string.
        """
        with self._lock:
            # Cancel existing timer for this content type
            if content_type_key in self._timers:
                self._timers[content_type_key].cancel()

            self._pending[content_type_key] = source
            timer = threading.Timer(
                self.debounce_seconds,
                self._execute_sync,
                args=[content_type_key],
            )
            timer.daemon = True
            timer.start()
            self._timers[content_type_key] = timer

    def _execute_sync(self, content_type_key):
        """Execute the sync for a content type after debounce expires."""
        with self._lock:
            source = self._pending.pop(content_type_key, None)
            self._timers.pop(content_type_key, None)

        if source is None:
            return

        if source == TIERS_SENTINEL:
            self._sync_tiers()
        else:
            self._sync_content_source(source)

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
        """Run sync_content_source for a single content type."""
        self.stdout.write(
            self.style.NOTICE(f'Syncing {source.content_type}...')
        )
        try:
            sync_log = sync_content_source(source, repo_dir=self.repo_dir)
            self.stdout.write(self.style.SUCCESS(
                f'  {source.content_type}: '
                f'created={sync_log.items_created}, '
                f'updated={sync_log.items_updated}, '
                f'deleted={sync_log.items_deleted}'
            ))
        except Exception as e:
            self.stderr.write(f'Error syncing {source.content_type}: {e}')

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
        # Import watchdog here so the module can be imported without it installed
        # (for testing internal helpers)
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            raise CommandError(
                'watchdog is not installed. Run: uv add watchdog'
            )

        repo_dir = os.path.expanduser(options['repo_dir'])
        debounce = options['debounce']

        # Validate repo directory
        if not os.path.isdir(repo_dir):
            raise CommandError(f'Directory does not exist: {repo_dir}')

        # Load content sources
        sources = list(ContentSource.objects.all())
        if not sources:
            raise CommandError(
                'No ContentSource records found. '
                'Run: uv run python manage.py seed_content_sources'
            )

        # Build path mapping
        path_to_source = _build_path_mapping(sources)

        # Print startup info
        self.stdout.write(self.style.SUCCESS('Content file watcher'))
        self.stdout.write(f'  Repo: {repo_dir}')
        self.stdout.write(f'  Debounce: {debounce}s')
        self.stdout.write('  Content types:')
        for content_path, source in sorted(path_to_source.items()):
            self.stdout.write(f'    {content_path}/ -> {source.content_type}')
        self.stdout.write('    tiers.yaml -> tiers config')
        self.stdout.write('')

        # Create debounced syncer
        syncer = DebouncedSyncer(
            debounce_seconds=debounce,
            repo_dir=repo_dir,
            stdout=self.stdout,
            stderr=self.stderr,
            style=self.style,
        )

        # Set up watchdog handler
        class ContentEventHandler(FileSystemEventHandler):
            def on_any_event(handler_self, event):
                # Only handle file events, not directory events
                if event.is_directory:
                    return

                # Get the file path relative to repo_dir
                src_path = event.src_path
                try:
                    rel_path = os.path.relpath(src_path, repo_dir)
                except ValueError:
                    return

                # Filter content files
                if not _is_content_file(rel_path):
                    return

                # Map to content type
                target = _get_content_type_for_path(
                    rel_path, path_to_source
                )
                if target is None:
                    return

                # Determine event label
                event_labels = {
                    'created': 'created',
                    'modified': 'modified',
                    'deleted': 'deleted',
                    'moved': 'moved',
                }
                event_type = getattr(event, 'event_type', 'modified')
                label = event_labels.get(event_type, event_type)

                # Determine content type key for debouncing
                if target == TIERS_SENTINEL:
                    content_key = TIERS_SENTINEL
                    type_label = 'tiers'
                else:
                    content_key = target.content_type
                    type_label = target.content_type

                self.stdout.write(
                    f'  [{label}] {rel_path} -> {type_label}'
                )
                syncer.schedule(content_key, target)

        # Start watching
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
