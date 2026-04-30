"""Sync orchestration, repository classification, and lock handling."""

import os
import shutil
import tempfile

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
    # Acquire sync lock (Edge Case 4: Concurrent Syncs)
    # Skip locking when repo_dir is provided (testing mode)
    use_lock = repo_dir is None
    if use_lock and not acquire_sync_lock(source):
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
            sync_log = SyncLog.objects.create(
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
        return sync_log

    # Cheap HEAD-SHA skip check (issue #235).
    #
    # If the upstream HEAD hasn't moved since the last successful sync, a
    # full clone + file walk would be a no-op. Use ``git ls-remote HEAD``
    # (a single network round-trip, no object download) to compare. Skip
    # the optimisation when:
    #   - ``force=True`` (operator clicked "Force resync" or webhook fired)
    #   - ``repo_dir`` is provided (no remote to check; tests + ``--from-disk``)
    #   - the previous sync didn't end in ``success`` (we want to retry)
    #   - we don't yet have a baseline (first-ever sync)
    #
    # If the HEAD lookup itself fails (network error, missing auth) we
    # fall through and run the sync rather than silently skipping —
    # better to do the work than to lie about being up to date.
    if (
        repo_dir is None
        and not force
        and source.last_synced_commit
        and source.last_sync_status == 'success'
    ):
        head_sha = fetch_remote_head_sha(source.repo_name, source.is_private)
        if head_sha and head_sha == source.last_synced_commit:
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
            if use_lock:
                release_sync_lock(source)
            logger.info(
                'Skipping sync for %s — HEAD unchanged (%s).',
                source.repo_name, head_sha[:7],
            )
            return sync_log

    # Issue #274: queued → running transition.
    #
    # The Studio trigger views now create a SyncLog at status='queued' the
    # moment the operator clicks "Sync now". When the worker (this code)
    # finally picks up the task, we UPDATE that existing row instead of
    # creating a duplicate — otherwise the dashboard would show two rows
    # for one logical sync (a queued one and a running one).
    #
    # We look for the most recent queued row for this source within the
    # last queued-watchdog window (only those rows could plausibly belong
    # to *this* enqueue call). If none found — direct CLI invocation,
    # webhook bypass, etc. — fall back to creating one as before.
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

    temp_dir = None
    commit_sha = ''
    try:
        if repo_dir is None:
            temp_dir = tempfile.mkdtemp(prefix='github-sync-')
            commit_sha = clone_or_pull_repo(
                source.repo_name, temp_dir, source.is_private,
            )
            repo_dir = temp_dir
        else:
            # For testing / --from-disk, resolve the SHA from the local
            # clone if possible; otherwise fall back to the legacy
            # ``test-commit-sha`` marker so downstream per-item
            # ``source_commit`` writes (and tests asserting on it) still
            # have a value.
            commit_sha = _resolve_local_repo_sha(repo_dir) or 'test-commit-sha'

        # The walker now operates on the whole repo and dispatches per file —
        # no more per-source ``content_path`` slicing. Edge Case 5: Max files
        # guard still applies; we just count over the whole repo.
        content_dir = repo_dir
        file_count = _count_content_files(content_dir)
        if file_count > source.max_files:
            raise GitHubSyncError(
                f'Repository contains more than {source.max_files} content files. '
                f'Increase max_files on the ContentSource or reduce repo size.'
            )

        # Upload images to S3 before content sync (so CDN URLs are live)
        # Edge Case 3: S3 errors do not abort sync
        s3_stats = upload_images_to_s3(content_dir, source)
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

        # Sync tiers.yaml from repo root into SiteConfig
        tiers_result = _sync_tiers_yaml(repo_dir)

        # Collect known image paths for broken reference checks (Edge Case 8)
        known_images = _collect_image_paths(content_dir)

        # One walker for the entire repo. The walker dispatches each file to
        # the appropriate leaf parser based on filename, frontmatter, and
        # location. See ``_sync_repo``.
        stats = _sync_repo(source, content_dir, commit_sha, sync_log,
                           known_images=known_images)

        # Merge S3 errors into stats
        all_errors = s3_errors + stats.get('errors', [])

        # Update sync log
        sync_log.items_created = stats.get('created', 0)
        sync_log.items_updated = stats.get('updated', 0)
        sync_log.items_unchanged = stats.get('unchanged', 0)
        sync_log.items_deleted = stats.get('deleted', 0)
        sync_log.items_detail = stats.get('items_detail', [])
        sync_log.tiers_synced = tiers_result.get('synced', False)
        sync_log.tiers_count = tiers_result.get('count', 0)
        sync_log.errors = all_errors
        sync_log.commit_sha = commit_sha or ''

        if sync_log.errors:
            sync_log.status = 'partial'
        else:
            sync_log.status = 'success'

        sync_log.finished_at = timezone.now()
        sync_log.save()

        # Update source
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
        # Issue #235: only persist last_synced_commit on success/partial
        # (i.e. the sync at least completed without raising). On failure
        # we keep the previous last-good SHA so the next skip check still
        # has a baseline. We treat ``partial`` (per-file errors) as good
        # enough — the repo was processed, we know what we have.
        if commit_sha and commit_sha != 'test-commit-sha':
            source.last_synced_commit = commit_sha
        source.save()

    except Exception as e:
        logger.exception('Sync failed for %s', source.repo_name)
        sync_log.status = 'failed'
        sync_log.finished_at = timezone.now()
        sync_log.errors = [{'file': '', 'error': str(e)}]
        # Record which SHA we were trying to sync against (helps debug
        # repeated failures), but do NOT update ContentSource —
        # ``last_synced_commit`` is a "last known good" pointer.
        if commit_sha:
            sync_log.commit_sha = commit_sha
        sync_log.save()

        source.last_sync_status = 'failed'
        source.last_sync_log = f'Sync failed: {e}'
        source.save()

    finally:
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)

        # Release sync lock and check for follow-up (Edge Case 9)
        if use_lock:
            follow_up = release_sync_lock(source)
            if follow_up:
                logger.info(
                    'Follow-up sync requested for %s, enqueuing.',
                    source.repo_name,
                )
                try:
                    from django_q.tasks import async_task
                    async_task(
                        'integrations.services.github.sync_content_source',
                        source,
                        task_name=f'sync-{source.repo_name}-followup',
                    )
                except ImportError:
                    sync_content_source(source)

    return sync_log


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
    course_dirs = []
    workshop_dirs = []
    claimed_prefixes = []  # rel paths (with trailing /) of claimed subtrees

    # Pass 1: find course.yaml and workshop.yaml to identify claimed subtrees.
    for root, dirs, files in os.walk(repo_dir, topdown=True):
        # Skip .git
        dirs[:] = [d for d in dirs if d != '.git' and not d.startswith('.git')]
        if 'course.yaml' in files:
            course_dirs.append(root)
            rel = os.path.relpath(root, repo_dir)
            prefix = '' if rel == '.' else rel + os.sep
            claimed_prefixes.append(prefix)
            # Don't descend further: course parser handles its own subtree.
            dirs[:] = []
            continue
        if 'workshop.yaml' in files:
            workshop_dirs.append(root)
            rel = os.path.relpath(root, repo_dir)
            prefix = '' if rel == '.' else rel + os.sep
            claimed_prefixes.append(prefix)
            dirs[:] = []
            continue

    def _is_under_claimed(rel_path):
        for prefix in claimed_prefixes:
            if not prefix:
                # The repo root itself is a claimed dir (single-course mode)
                # which means EVERY file at the root is claimed by the
                # course parser. The course parser will pick the ones it
                # wants; everything else is silently ignored.
                return True
            if rel_path.startswith(prefix):
                return True
        return False

    article_files = []
    project_files = []
    event_files = []
    instructor_files = []
    curated_link_files = []
    download_files = []
    interview_files = []

    # Pass 2: walk every file and classify by inspection. We re-walk so
    # subtree pruning is independent of the first pass — callers don't
    # rely on path order within categories anyway.
    for root, dirs, files in os.walk(repo_dir, topdown=True):
        dirs[:] = [d for d in dirs if d != '.git' and not d.startswith('.git')]

        for filename in files:
            filepath = os.path.join(root, filename)
            rel_path = os.path.relpath(filepath, repo_dir)

            # tiers.yaml is handled separately in sync_content_source.
            if rel_path == 'tiers.yaml':
                continue
            # course.yaml / workshop.yaml are dispatched by their subtree.
            if filename in ('course.yaml', 'workshop.yaml'):
                continue
            # Any file under a claimed subtree is owned by the
            # course/workshop parser — skip it for everything else.
            if _is_under_claimed(rel_path):
                continue

            # Path-component checks for special directories.
            parts = rel_path.split(os.sep)
            ext = os.path.splitext(filename)[1].lower()

            if 'instructors' in parts and ext in ('.yaml', '.yml'):
                instructor_files.append(rel_path)
                continue
            if 'curated-links' in parts and ext in ('.md', '.yaml', '.yml'):
                curated_link_files.append(rel_path)
                continue
            if 'downloads' in parts and ext in ('.yaml', '.yml'):
                download_files.append(rel_path)
                continue

            # Files under an ``events/`` or ``recordings/`` subtree:
            # events, regardless of whether the YAML contains a
            # ``start_datetime`` (legacy recording-only events have
            # only ``published_at``). This mirrors the directory-based
            # dispatch the old per-type orchestrators used.
            if (
                ('events' in parts or 'recordings' in parts)
                and ext in ('.yaml', '.yml', '.md')
            ):
                event_files.append(rel_path)
                continue

            if ext in ('.yaml', '.yml'):
                # YAML with start_datetime -> event (catches events that
                # live outside an ``events/`` subtree).
                try:
                    data = _parse_yaml_file(filepath)
                except ValueError as exc:
                    # Malformed YAML — record an error so the dashboard
                    # surfaces it, then skip classification.
                    if classify_errors is not None:
                        classify_errors.append({
                            'file': rel_path,
                            'error': str(exc),
                        })
                    continue
                if 'start_datetime' in data or 'published_at' in data:
                    # ``published_at``-only files were treated as
                    # recording-style events by the legacy orchestrator;
                    # preserve that.
                    event_files.append(rel_path)
                continue

            if ext == '.md':
                if filename.upper() == 'README.MD':
                    continue
                # Path-based dispatch for the monorepo's well-known
                # subtrees first so a partially-formed frontmatter still
                # gets routed correctly. The article/project per-file
                # parsers do their own validation and will surface a
                # SyncLog error when frontmatter is missing.
                if 'blog' in parts:
                    article_files.append(rel_path)
                    continue
                if 'projects' in parts:
                    project_files.append(rel_path)
                    continue
                if 'interview-questions' in parts:
                    interview_files.append(rel_path)
                    continue
                try:
                    metadata, _body = _parse_markdown_file(filepath)
                except ValueError:
                    # Hand the file to the article dispatcher anyway so
                    # its per-file try/except records the parse error
                    # AND adds the filename-derived slug to
                    # ``failed_slugs`` (excluding it from stale-cleanup
                    # soft-delete). Articles are the most permissive
                    # default for unclassified markdown.
                    article_files.append(rel_path)
                    continue
                # Project: ``difficulty`` claims it. ``author`` alone
                # was historically the project marker but plenty of
                # articles also carry ``author``; ``difficulty`` is the
                # discriminator we keep.
                if metadata.get('difficulty'):
                    project_files.append(rel_path)
                    continue
                # Article: has a date field.
                if metadata.get('date'):
                    article_files.append(rel_path)
                    continue
                # Interview question: root-level kebab-case .md with no
                # special frontmatter we already matched.
                if (
                    '/' not in rel_path
                    and os.sep not in rel_path
                    and _interview_question_filename(filename)
                ):
                    interview_files.append(rel_path)
                    continue
                # else: silently ignore (README, docs, etc.)

    return {
        'course_dirs': course_dirs,
        'workshop_dirs': workshop_dirs,
        'article_files': article_files,
        'project_files': project_files,
        'event_files': event_files,
        'instructor_files': instructor_files,
        'curated_link_files': curated_link_files,
        'download_files': download_files,
        'interview_files': interview_files,
    }


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
