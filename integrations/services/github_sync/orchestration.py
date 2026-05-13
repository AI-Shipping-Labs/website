"""Sync orchestration, repository classification, and lock handling."""

import os
import shutil
import tempfile
from dataclasses import dataclass

from django.db import IntegrityError
from django.utils import timezone

from integrations.models import ContentSource, SyncLog
from integrations.services.github_sync.common import (
    CONTENT_EXTENSIONS,
    IMAGE_EXTENSIONS,
    SYNC_LOCK_TIMEOUT_MINUTES,
    GitHubSyncError,
    logger,
)
from integrations.services.github_sync.dispatchers.articles import _dispatch_articles
from integrations.services.github_sync.dispatchers.courses import _dispatch_courses
from integrations.services.github_sync.dispatchers.curated_links import _dispatch_curated_links
from integrations.services.github_sync.dispatchers.downloads import _dispatch_downloads
from integrations.services.github_sync.dispatchers.events import _dispatch_events
from integrations.services.github_sync.dispatchers.instructors import _dispatch_instructors
from integrations.services.github_sync.dispatchers.interview_questions import _dispatch_interview_questions
from integrations.services.github_sync.dispatchers.projects import _dispatch_projects
from integrations.services.github_sync.dispatchers.tiers import _sync_tiers_yaml
from integrations.services.github_sync.dispatchers.workshops import _dispatch_workshops
from integrations.services.github_sync.media import _collect_image_paths, upload_images_to_s3
from integrations.services.github_sync.parsing import _parse_markdown_file, _parse_yaml_file
from integrations.services.github_sync.repo import (
    _interview_question_filename,
    _resolve_local_repo_sha,
    clone_or_pull_repo,
    fetch_remote_head_sha,
)


@dataclass(frozen=True)
class PreparedRepo:
    repo_dir: str
    temp_dir: str | None
    commit_sha: str


@dataclass(frozen=True)
class SyncPipelineResult:
    stats: dict
    s3_errors: list
    tiers_result: dict


@dataclass(frozen=True)
class RepoClassification:
    course_dirs: list
    workshop_dirs: list
    article_files: list
    project_files: list
    event_files: list
    instructor_files: list
    curated_link_files: list
    download_files: list
    interview_files: list

    def as_dict(self):
        return {
            'course_dirs': self.course_dirs,
            'workshop_dirs': self.workshop_dirs,
            'article_files': self.article_files,
            'project_files': self.project_files,
            'event_files': self.event_files,
            'instructor_files': self.instructor_files,
            'curated_link_files': self.curated_link_files,
            'download_files': self.download_files,
            'interview_files': self.interview_files,
        }


def _is_head_unchanged_skip(log):
    """Return True for skip logs written by the HEAD-unchanged fast path."""
    return (
        log is not None
        and log.status == 'skipped'
        and bool(log.commit_sha)
        and any(
            (error or {}).get('error') == 'HEAD unchanged'
            for error in (log.errors or [])
        )
    )


def _can_skip_for_unchanged_head(source):
    """Decide whether the cheap HEAD check may short-circuit this sync."""
    if not source.last_synced_commit:
        return False

    latest_terminal_log = (
        SyncLog.objects.filter(source=source)
        .exclude(status__in=['queued', 'running'])
        .order_by('-started_at')
        .first()
    )
    if latest_terminal_log is None:
        return source.last_sync_status == 'success'
    if latest_terminal_log.status == 'success':
        return True
    return (
        _is_head_unchanged_skip(latest_terminal_log)
        and latest_terminal_log.commit_sha == source.last_synced_commit
    )


def sync_content_source(source, repo_dir=None, batch_id=None, force=False):
    """Sync content from a GitHub repo into the database.

    This is the main sync function that:
    1. Clones/pulls the repo
    2. Parses all content files
    3. Upserts content into the database
    4. Soft-deletes content no longer in the repo
    5. Logs the result

    Args:
        source: ContentSource instance.
        repo_dir: Optional pre-cloned repo directory (for testing /
            ``--from-disk`` syncs). When provided, the cheap HEAD-SHA
            skip optimisation (issue #235) is bypassed because there's
            no remote to compare against.
        batch_id: Optional UUID to group logs from the same "Sync All" action.
        force: If True, bypass the HEAD-SHA skip check and force a full
            sync even when the upstream HEAD hasn't changed (issue #235).
            Used by the Studio "Force resync" button and the webhook
            handler (which already knows a new commit landed).

    Returns:
        SyncLog or None: The sync log entry.
    """
    use_lock = repo_dir is None
    if use_lock and not acquire_sync_lock(source):
        return _write_lock_skipped_log(source, batch_id)

    head_skip_log = _maybe_skip_unchanged_head(
        source, repo_dir=repo_dir, batch_id=batch_id, force=force,
    )
    if head_skip_log is not None:
        if use_lock:
            release_sync_lock(source)
        return head_skip_log

    sync_log = _start_sync_log(source, batch_id)
    prepared = None
    try:
        prepared = _prepare_repo(source, repo_dir)
        pipeline_result = _run_content_pipeline(
            source, prepared.repo_dir, prepared.commit_sha, sync_log,
        )
        _finish_successful_sync(source, sync_log, prepared, pipeline_result)
    except Exception as e:
        _mark_sync_failed(source, sync_log, e, prepared)
    finally:
        _cleanup_prepared_repo(prepared)
        if use_lock:
            _release_lock_and_enqueue_follow_up(source)

    return sync_log


def _write_lock_skipped_log(source, batch_id):
    logger.info(
        'Sync already in progress for %s, skipping.', source.repo_name,
    )
    # Issue #221: the in-memory ``source`` may point to a row that was
    # deleted (or rolled back under SQLite contention) before this task
    # ran. Writing the SyncLog with a stale FK raises IntegrityError and
    # fails the worker task. Confirm the source still exists, and treat
    # the SyncLog write itself as best-effort.
    if not ContentSource.objects.filter(pk=source.pk).exists():
        logger.warning(
            'Skipping SyncLog for %s: ContentSource %s no longer exists.',
            source.repo_name, source.pk,
        )
        return None
    try:
        return SyncLog.objects.create(
            source=source,
            batch_id=batch_id,
            status='skipped',
            finished_at=timezone.now(),
            errors=[
                {'file': '', 'error': 'Sync already in progress, skipped.'},
            ],
        )
    except IntegrityError:
        logger.warning(
            'Could not write skipped SyncLog for %s (FK gone); '
            'returning without raising.',
            source.repo_name,
        )
        return None


def _maybe_skip_unchanged_head(source, *, repo_dir, batch_id, force):
    """Write and return a skip log when the remote HEAD is unchanged."""
    if (
        repo_dir is not None
        or force
        or not _can_skip_for_unchanged_head(source)
    ):
        return None

    head_sha = fetch_remote_head_sha(source.repo_name, source.is_private)
    if not head_sha or head_sha != source.last_synced_commit:
        return None

    now = timezone.now()
    sync_log = SyncLog.objects.create(
        source=source,
        batch_id=batch_id,
        status='skipped',
        commit_sha=head_sha,
        finished_at=now,
        errors=[{'file': '', 'error': 'HEAD unchanged'}],
    )
    source.last_synced_at = now
    source.last_sync_status = 'skipped'
    source.last_sync_log = f'Skipped: HEAD unchanged ({head_sha[:7]})'
    source.save(update_fields=[
        'last_synced_at', 'last_sync_status', 'last_sync_log',
        'updated_at',
    ])
    logger.info(
        'Skipping sync for %s — HEAD unchanged (%s).',
        source.repo_name, head_sha[:7],
    )
    return sync_log


def _start_sync_log(source, batch_id):
    from django.conf import settings as _settings

    queued_window = timezone.now() - timezone.timedelta(
        minutes=getattr(_settings, 'SYNC_QUEUED_THRESHOLD_MINUTES', 10),
    )
    queued_log = SyncLog.objects.filter(
        source=source,
        status='queued',
        started_at__gte=queued_window,
    ).order_by('-started_at').first()
    if queued_log is not None:
        queued_log.status = 'running'
        # Carry batch_id from the worker call if the trigger view didn't
        # know it (single-source trigger); otherwise the trigger view's
        # batch_id was already written when the queued row was created.
        if batch_id and not queued_log.batch_id:
            queued_log.batch_id = batch_id
            queued_log.save(update_fields=['status', 'batch_id'])
        else:
            queued_log.save(update_fields=['status'])
        sync_log = queued_log
    else:
        sync_log = SyncLog.objects.create(
            source=source,
            batch_id=batch_id,
            status='running',
        )

    source.last_sync_status = 'running'
    source.save(update_fields=['last_sync_status', 'updated_at'])
    return sync_log


def _prepare_repo(source, repo_dir):
    if repo_dir is None:
        temp_dir = tempfile.mkdtemp(prefix='github-sync-')
        commit_sha = clone_or_pull_repo(
            source.repo_name, temp_dir, source.is_private,
        )
        return PreparedRepo(
            repo_dir=temp_dir, temp_dir=temp_dir, commit_sha=commit_sha,
        )

    # For testing / --from-disk, resolve the SHA from the local clone if
    # possible; otherwise fall back to the legacy ``test-commit-sha`` marker.
    commit_sha = _resolve_local_repo_sha(repo_dir) or 'test-commit-sha'
    return PreparedRepo(repo_dir=repo_dir, temp_dir=None, commit_sha=commit_sha)


def _run_content_pipeline(source, repo_dir, commit_sha, sync_log):
    _enforce_max_content_files(source, repo_dir)
    s3_stats = upload_images_to_s3(repo_dir, source)
    s3_errors = s3_stats.get('errors', [])
    if s3_errors:
        logger.warning(
            'S3 image upload had %d error(s) for %s, continuing with content sync.',
            len(s3_errors), source.repo_name,
        )
    logger.info(
        'S3 upload for %s: %d uploaded, %d skipped',
        source.repo_name, s3_stats['uploaded'], s3_stats['skipped'],
    )

    tiers_result = _sync_tiers_yaml(repo_dir)
    known_images = _collect_image_paths(repo_dir)
    stats = _sync_repo(
        source, repo_dir, commit_sha, sync_log, known_images=known_images,
    )
    return SyncPipelineResult(
        stats=stats, s3_errors=s3_errors, tiers_result=tiers_result,
    )


def _enforce_max_content_files(source, repo_dir):
    file_count = _count_content_files(repo_dir)
    if file_count > source.max_files:
        raise GitHubSyncError(
            f'Repository contains more than {source.max_files} content files. '
            f'Increase max_files on the ContentSource or reduce repo size.'
        )


def _finish_successful_sync(source, sync_log, prepared, pipeline_result):
    stats = pipeline_result.stats
    tiers_result = pipeline_result.tiers_result
    sync_log.items_created = stats.get('created', 0)
    sync_log.items_updated = stats.get('updated', 0)
    sync_log.items_unchanged = stats.get('unchanged', 0)
    sync_log.items_deleted = stats.get('deleted', 0)
    sync_log.items_detail = stats.get('items_detail', [])
    sync_log.tiers_synced = tiers_result.get('synced', False)
    sync_log.tiers_count = tiers_result.get('count', 0)
    sync_log.errors = pipeline_result.s3_errors + stats.get('errors', [])
    sync_log.commit_sha = prepared.commit_sha or ''
    sync_log.status = 'partial' if sync_log.errors else 'success'
    sync_log.finished_at = timezone.now()
    sync_log.save()

    source.last_synced_at = timezone.now()
    source.last_sync_status = sync_log.status
    source.last_sync_log = (
        f"Created: {sync_log.items_created}, "
        f"Updated: {sync_log.items_updated}, "
        f"Unchanged: {sync_log.items_unchanged}, "
        f"Deleted: {sync_log.items_deleted}"
    )
    if sync_log.errors:
        source.last_sync_log += f"\nErrors: {len(sync_log.errors)}"
    if prepared.commit_sha and prepared.commit_sha != 'test-commit-sha':
        source.last_synced_commit = prepared.commit_sha
    source.save()


def _mark_sync_failed(source, sync_log, error, prepared):
    logger.exception('Sync failed for %s', source.repo_name)
    sync_log.status = 'failed'
    sync_log.finished_at = timezone.now()
    sync_log.errors = [{'file': '', 'error': str(error)}]
    if prepared is not None and prepared.commit_sha:
        sync_log.commit_sha = prepared.commit_sha
    sync_log.save()

    source.last_sync_status = 'failed'
    source.last_sync_log = f'Sync failed: {error}'
    source.save()


def _cleanup_prepared_repo(prepared):
    if prepared and prepared.temp_dir and os.path.exists(prepared.temp_dir):
        shutil.rmtree(prepared.temp_dir, ignore_errors=True)


def _release_lock_and_enqueue_follow_up(source):
    follow_up = release_sync_lock(source)
    if not follow_up:
        return
    logger.info(
        'Follow-up sync requested for %s, enqueuing.',
        source.repo_name,
    )
    try:
        from django_q.tasks import async_task

        from jobs.tasks.names import build_task_name

        async_task(
            'integrations.services.github.sync_content_source',
            source,
            task_name=build_task_name(
                'Sync content source',
                source.repo_name,
                'GitHub sync follow-up',
            ),
        )
    except ImportError:
        sync_content_source(source)


def _classify_repo_files(repo_dir, classify_errors=None):
    """First-pass walk: classify every file in the repo for dispatch.

    Returns a dict with per-type lists of repo-relative paths plus a list
    of claimed course/workshop directories (relative to ``repo_dir``).

    The walker prunes course and workshop subtrees: any file under a path
    that contains a ``course.yaml`` or ``workshop.yaml`` ancestor is
    handled by the course/workshop parser, not by the article/event/etc.
    leaf parsers. This is the path-claim ordering risk called out in
    issue #310 — a course unit ``.md`` with a ``date:`` field would
    otherwise get double-claimed as an Article.

    Returns:
        dict with keys:
          ``course_dirs`` — list of absolute paths to course root dirs
              (each contains a ``course.yaml``).
          ``workshop_dirs`` — list of absolute paths to workshop root
              dirs (each contains a ``workshop.yaml``).
          ``article_files`` — list of repo-relative paths to ``.md``
              files identified as articles (have ``date:`` frontmatter,
              not under a course/workshop dir, not in special subdirs).
          ``project_files`` — list of repo-relative paths to ``.md``
              files identified as projects (``difficulty`` + ``author``).
          ``event_files`` — list of repo-relative paths to YAML files
              with ``start_datetime``.
          ``instructor_files`` — list of YAML files under an
              ``instructors/`` subtree.
          ``curated_link_files`` — list of ``.md``/YAML files under a
              ``curated-links/`` subtree.
          ``download_files`` — list of YAML files under a ``downloads/``
              subtree.
          ``interview_files`` — list of root-level ``.md`` files matching
              the interview-question convention (lowercase kebab-case,
              not README) AND having no special frontmatter.
    """
    return RepoFileClassifier(repo_dir, classify_errors).classify().as_dict()


class RepoFileClassifier:
    """Two-pass repo classifier that preserves the legacy dict boundary."""

    def __init__(self, repo_dir, classify_errors=None):
        self.repo_dir = repo_dir
        self.classify_errors = classify_errors
        self.course_dirs = []
        self.workshop_dirs = []
        self.claimed_prefixes = []
        self.article_files = []
        self.project_files = []
        self.event_files = []
        self.instructor_files = []
        self.curated_link_files = []
        self.download_files = []
        self.interview_files = []

    def classify(self):
        self._claim_structured_subtrees()
        self._classify_unclaimed_files()
        return RepoClassification(
            course_dirs=self.course_dirs,
            workshop_dirs=self.workshop_dirs,
            article_files=self.article_files,
            project_files=self.project_files,
            event_files=self.event_files,
            instructor_files=self.instructor_files,
            curated_link_files=self.curated_link_files,
            download_files=self.download_files,
            interview_files=self.interview_files,
        )

    def _claim_structured_subtrees(self):
        for root, dirs, files in os.walk(self.repo_dir, topdown=True):
            self._prune_git_dirs(dirs)
            if 'course.yaml' in files:
                self._claim_subtree(root, self.course_dirs)
                dirs[:] = []
                continue
            if 'workshop.yaml' in files:
                self._claim_subtree(root, self.workshop_dirs)
                dirs[:] = []

    def _claim_subtree(self, root, target):
        target.append(root)
        rel = os.path.relpath(root, self.repo_dir)
        prefix = '' if rel == '.' else rel + os.sep
        self.claimed_prefixes.append(prefix)

    def _is_under_claimed(self, rel_path):
        for prefix in self.claimed_prefixes:
            if not prefix or rel_path.startswith(prefix):
                return True
        return False

    def _classify_unclaimed_files(self):
        for root, dirs, files in os.walk(self.repo_dir, topdown=True):
            self._prune_git_dirs(dirs)
            for filename in files:
                self._classify_file(root, filename)

    @staticmethod
    def _prune_git_dirs(dirs):
        dirs[:] = [d for d in dirs if d != '.git' and not d.startswith('.git')]

    def _classify_file(self, root, filename):
        filepath = os.path.join(root, filename)
        rel_path = os.path.relpath(filepath, self.repo_dir)
        if self._is_structural_or_claimed_file(rel_path, filename):
            return

        parts = rel_path.split(os.sep)
        ext = os.path.splitext(filename)[1].lower()
        if self._classify_by_special_dir(rel_path, parts, ext):
            return
        if ext in ('.yaml', '.yml'):
            self._classify_yaml(filepath, rel_path)
        elif ext == '.md':
            self._classify_markdown(filepath, rel_path, filename, parts)

    def _is_structural_or_claimed_file(self, rel_path, filename):
        return (
            rel_path == 'tiers.yaml'
            or filename in ('course.yaml', 'workshop.yaml')
            or self._is_under_claimed(rel_path)
        )

    def _classify_by_special_dir(self, rel_path, parts, ext):
        if 'instructors' in parts and ext in ('.yaml', '.yml'):
            self.instructor_files.append(rel_path)
            return True
        if 'curated-links' in parts and ext in ('.md', '.yaml', '.yml'):
            self.curated_link_files.append(rel_path)
            return True
        if 'downloads' in parts and ext in ('.yaml', '.yml'):
            self.download_files.append(rel_path)
            return True
        if (
            ('events' in parts or 'recordings' in parts)
            and ext in ('.yaml', '.yml', '.md')
        ):
            self.event_files.append(rel_path)
            return True
        return False

    def _classify_yaml(self, filepath, rel_path):
        try:
            data = _parse_yaml_file(filepath)
        except ValueError as exc:
            if self.classify_errors is not None:
                self.classify_errors.append({
                    'file': rel_path,
                    'error': str(exc),
                })
            return
        if 'start_datetime' in data or 'published_at' in data:
            self.event_files.append(rel_path)

    def _classify_markdown(self, filepath, rel_path, filename, parts):
        if filename.upper() == 'README.MD':
            return
        if 'blog' in parts:
            self.article_files.append(rel_path)
            return
        if 'projects' in parts:
            self.project_files.append(rel_path)
            return
        if 'interview-questions' in parts:
            self.interview_files.append(rel_path)
            return
        try:
            metadata, _body = _parse_markdown_file(filepath)
        except ValueError:
            self.article_files.append(rel_path)
            return
        if metadata.get('difficulty'):
            self.project_files.append(rel_path)
        elif metadata.get('date'):
            self.article_files.append(rel_path)
        elif (
            '/' not in rel_path
            and os.sep not in rel_path
            and _interview_question_filename(filename)
        ):
            self.interview_files.append(rel_path)


_DEFAULT_WORKSHOPS_REPO = 'AI-Shipping-Labs/workshops'


def _resolve_workshops_repo_name(source):
    """Return the GitHub repo name to use when matching cross-workshop URLs.

    Issue #526. Cross-workshop links use the configured workshops content
    source's ``repo_name`` so detection isn't hardcoded to one host. Strategy:

    1. If the current ``source`` ITSELF holds workshop folders (i.e. this
       sync is happening on the workshops repo), use ``source.repo_name``.
    2. Else look for any other ``ContentSource`` whose name contains
       ``"workshop"`` (matches ``AI-Shipping-Labs/workshops`` and the legacy
       ``workshops-content`` seed alike) and use the first match.
    3. Fall back to the production default ``AI-Shipping-Labs/workshops``.

    Step 1 is the common path; the lookup runs while we're already syncing
    the workshops repo. The DB lookup is a defensive secondary path.
    """
    if source is not None and source.repo_name:
        return source.repo_name
    try:
        candidate = (
            ContentSource.objects
            .filter(repo_name__icontains='workshop')
            .order_by('repo_name')
            .values_list('repo_name', flat=True)
            .first()
        )
    except Exception:
        candidate = None
    return candidate or _DEFAULT_WORKSHOPS_REPO


def _build_cross_workshop_lookup(workshop_dirs, repo_dir, errors=None):
    """Build a sync-wide ``{folder_name: workshop-meta}`` lookup once per sync.

    Issue #526. ``rewrite_cross_workshop_md_links`` consumes this map to
    rewrite ``..``-relative and absolute-GitHub-URL workshop references in
    every workshop body. The map is keyed by the on-disk dated-slug folder
    name (e.g. ``2026-04-21-end-to-end-agent-deployment``) because that's
    what authors write in their links.

    Each value carries:

    - ``slug``: the URL slug from ``workshop.yaml``.
    - ``title``: the workshop title from ``workshop.yaml``.
    - ``content_id``: the workshop UUID from ``workshop.yaml``.
    - ``url``: the public landing URL (``/workshops/<slug>``).
    - ``pages``: ``{filename: {'slug', 'title'}}`` for every ``.md`` page
      with a frontmatter ``title:`` (README is excluded — links to it are
      rewritten to the bare landing URL by the rewriter).

    A workshop whose ``workshop.yaml`` is missing required fields (``slug:``
    in particular) is skipped with an ``errors`` entry. Folders whose
    ``workshop.yaml`` cannot be parsed are also skipped — link resolution
    surfaces a separate "folder not found" warning later.

    Args:
        workshop_dirs: Absolute paths from
            ``_classify_repo_files()['workshop_dirs']``.
        repo_dir: Absolute path to the cloned repo (used to compute
            relative paths for error messages).
        errors: Optional list to append per-workshop build-time errors to.

    Returns:
        dict: ``{folder_name: meta}``.
    """
    from integrations.services.github_sync.repo import derive_slug

    lookup = {}
    for workshop_path in workshop_dirs:
        folder_name = os.path.basename(workshop_path.rstrip(os.sep))
        yaml_path = os.path.join(workshop_path, 'workshop.yaml')
        try:
            data = _parse_yaml_file(yaml_path)
        except Exception:
            # Unparseable workshop.yaml is reported elsewhere by the
            # workshop dispatcher; here we just skip.
            continue
        if not isinstance(data, dict):
            continue
        slug = data.get('slug')
        if not slug:
            if errors is not None:
                rel_yaml = os.path.relpath(yaml_path, repo_dir)
                errors.append({
                    'file': rel_yaml,
                    'error': (
                        f'workshop.yaml at {rel_yaml} is missing required '
                        f'field "slug"; cannot resolve cross-workshop links '
                        f'to it.'
                    ),
                })
            continue

        title = data.get('title') or slug
        content_id = data.get('content_id') or ''

        pages = {}
        try:
            page_filenames = sorted(os.listdir(workshop_path))
        except OSError:
            page_filenames = []
        for filename in page_filenames:
            if (
                not filename.endswith('.md')
                or filename.upper() == 'README.MD'
                or filename.startswith('.')
            ):
                continue
            page_path = os.path.join(workshop_path, filename)
            if not os.path.isfile(page_path):
                continue
            try:
                metadata, _ = _parse_markdown_file(page_path)
            except Exception:
                continue
            page_title = metadata.get('title')
            if not page_title:
                continue
            page_slug = metadata.get('slug') or derive_slug(filename)
            pages[filename] = {
                'slug': page_slug,
                'title': page_title,
            }

        lookup[folder_name] = {
            'slug': slug,
            'title': title,
            'content_id': content_id,
            'url': f'/workshops/{slug}',
            'pages': pages,
        }

    return lookup


def _sync_repo(source, repo_dir, commit_sha, sync_log, known_images=None):
    """Walk a cloned repo and dispatch each content file to its parser.

    Issue #310. Replaces the per-content-type orchestrators
    (``_sync_articles``, ``_sync_courses``, ``_sync_workshops``,
    ``_sync_events``, ``_sync_resources``, ``_sync_instructors``,
    ``_sync_projects``, ``_sync_interview_questions``).

    Returns the same ``stats`` shape they did:
    ``{created, updated, unchanged, deleted, errors, items_detail}``.

    Walker contract:

    - One classification pass identifies course / workshop subtrees and
      buckets every other file by content type via filename + frontmatter
      + location.
    - Course / workshop subtrees are handed off to the dedicated single
      parsers (``_sync_single_course`` / ``_sync_single_workshop``), so
      a course unit ``.md`` with a ``date:`` field never gets
      double-claimed as an Article.
    - Per-type dispatch handlers process their assigned files, then
      perform stale-content cleanup against the union of seen + failed
      slugs/ids for that type.
    """
    stats = {
        'created': 0, 'updated': 0, 'unchanged': 0, 'deleted': 0,
        'errors': [], 'items_detail': [],
    }
    # Errors raised while classifying files (broken YAML/frontmatter)
    # are recorded into ``stats['errors']`` so they surface on the
    # SyncLog the same way the legacy per-type orchestrators did.
    classified = _classify_repo_files(
        repo_dir, classify_errors=stats['errors'],
    )

    # Issue #526: build the sync-wide cross-workshop lookup once, before
    # any workshop body is rewritten, so `rewrite_cross_workshop_md_links`
    # can resolve `../<folder>/...` and full-GitHub-URL references to
    # native `/workshops/<slug>` URLs across the whole sync run.
    cross_workshop_lookup = _build_cross_workshop_lookup(
        classified['workshop_dirs'], repo_dir, errors=stats['errors'],
    )
    workshops_repo_name = _resolve_workshops_repo_name(source)

    # Each dispatch helper runs even when its file list is empty so the
    # stale-content cleanup sweep fires. Without this, deleting the last
    # course/workshop/article from the repo would leave the matching
    # rows ``status='published'`` forever.
    _dispatch_courses(
        source, repo_dir, classified['course_dirs'],
        commit_sha, stats, known_images=known_images,
    )
    _dispatch_workshops(
        source, repo_dir, classified['workshop_dirs'],
        commit_sha, stats, known_images=known_images,
        cross_workshop_lookup=cross_workshop_lookup,
        workshops_repo_name=workshops_repo_name,
    )
    _dispatch_articles(
        source, repo_dir, classified['article_files'],
        commit_sha, stats, known_images=known_images,
    )
    _dispatch_projects(
        source, repo_dir, classified['project_files'],
        commit_sha, stats, known_images=known_images,
    )
    _dispatch_events(
        source, repo_dir, classified['event_files'],
        commit_sha, stats, known_images=known_images,
    )
    _dispatch_instructors(
        source, repo_dir, classified['instructor_files'],
        commit_sha, stats,
    )
    _dispatch_curated_links(
        source, repo_dir, classified['curated_link_files'],
        commit_sha, stats,
    )
    _dispatch_downloads(
        source, repo_dir, classified['download_files'],
        commit_sha, stats,
    )
    _dispatch_interview_questions(
        source, repo_dir, classified['interview_files'],
        commit_sha, stats, known_images=known_images,
    )

    return stats

def _count_content_files(content_dir):
    """Count content files (.md, .yaml, .yml) in a directory tree."""
    count = 0
    for root, dirs, files in os.walk(content_dir):
        if '.git' in root:
            continue
        for filename in files:
            ext = os.path.splitext(filename)[1].lower()
            if ext in CONTENT_EXTENSIONS:
                count += 1
            elif ext not in IMAGE_EXTENSIONS and filename != 'README.md':
                logger.debug(
                    'Skipping non-content, non-image file: %s',
                    os.path.join(root, filename),
                )
    return count

def acquire_sync_lock(source):
    """Attempt to acquire the sync lock for a ContentSource.

    Uses atomic queryset UPDATE to prevent race conditions. A lock older than
    SYNC_LOCK_TIMEOUT_MINUTES is considered stale and can be reclaimed.

    Returns:
        bool: True if lock was acquired, False if already locked.
    """
    from django.db.models import Q

    now = timezone.now()
    stale_threshold = now - timezone.timedelta(minutes=SYNC_LOCK_TIMEOUT_MINUTES)

    updated = ContentSource.objects.filter(
        pk=source.pk,
    ).filter(
        Q(sync_locked_at__isnull=True) | Q(sync_locked_at__lt=stale_threshold),
    ).update(sync_locked_at=now)

    if updated == 0:
        return False

    source.refresh_from_db()
    return True


def release_sync_lock(source):
    """Release the sync lock and check for pending sync requests.

    Returns:
        bool: True if a follow-up sync was requested.
    """
    source.refresh_from_db()
    was_requested = source.sync_requested
    source.sync_locked_at = None
    source.sync_requested = False
    source.save(update_fields=['sync_locked_at', 'sync_requested', 'updated_at'])
    return was_requested
