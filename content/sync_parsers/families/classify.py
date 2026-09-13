"""Repository classification and cross-workshop lookup for site parsers.

Moved from ``integrations/services/github_sync/orchestration.py`` when the
package engine became the only synchronization engine. Paths are resolved
against the synthetic checkout view root.
"""

import os

from django.db import DatabaseError

from community_base.content_sync.models import ContentSource as PackageContentSource

from content.sync_parsers.checkout_view import (
    checkout_is_file,
    checkout_listdir,
    checkout_walk,
)
from content.sync_parsers.common import (
    CONTENT_EXTENSIONS,
    IMAGE_EXTENSIONS,
    logger,
)
from content.sync_parsers.parsing import (
    _parse_markdown_file,
    _parse_yaml_file,
)
from content.sync_parsers.repo_util import (
    _interview_question_filename,
    derive_slug,
)


class RepoClassification:
    def __init__(
        self,
        course_dirs,
        workshop_dirs,
        article_files,
        project_files,
        event_files,
        instructor_files,
        curated_link_files,
        download_files,
        marketing_page_files,
        interview_files,
    ):
        self.course_dirs = course_dirs
        self.workshop_dirs = workshop_dirs
        self.article_files = article_files
        self.project_files = project_files
        self.event_files = event_files
        self.instructor_files = instructor_files
        self.curated_link_files = curated_link_files
        self.download_files = download_files
        self.marketing_page_files = marketing_page_files
        self.interview_files = interview_files


def classify_checkout(run):
    """First-pass walk: classify every checkout entry for parser dispatch.

    Classifier parse errors (broken YAML/frontmatter) are recorded on the run
    so they surface on the SyncLog the same way the legacy per-type
    orchestrators did.
    """
    run._classify_errors = []
    classifier = RepoFileClassifier(run.repo_dir, run._classify_errors)
    return classifier.classify()


def count_content_files(run):
    """Count content files (.md, .yaml, .yml) in the checkout."""
    count = 0
    for root, dirs, files in checkout_walk(run.repo_dir):
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


class RepoFileClassifier:
    """Two-pass repo classifier returning a :class:`RepoClassification`."""

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
        self.marketing_page_files = []
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
            marketing_page_files=self.marketing_page_files,
            interview_files=self.interview_files,
        )

    def _claim_structured_subtrees(self):
        for root, dirs, files in checkout_walk(self.repo_dir):
            self._prune_git_dirs(dirs)
            rel_root = os.path.relpath(root, self.repo_dir)
            if rel_root != '.' and self._is_under_claimed(rel_root + os.sep):
                continue
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
        for root, dirs, files in checkout_walk(self.repo_dir):
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
        try:
            metadata, _body = _parse_markdown_file(filepath)
        except ValueError:
            metadata = None
        if metadata and metadata.get('content_type') == 'marketing_page':
            self.marketing_page_files.append(rel_path)
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
        if metadata is None:
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


def resolve_workshops_repo_name(source):
    """Return the GitHub repo name to use when matching cross-workshop URLs.

    Issue #526. Cross-workshop links use the configured workshops content
    source's ``repo_name`` so detection isn't hardcoded to one host.

    1. If the current ``source`` itself holds workshop folders, use
       ``source.repo_name``.
    2. Else look for any package ``ContentSource`` whose name contains
       ``"workshop"`` and use the first match.
    3. Fall back to the production default ``AI-Shipping-Labs/workshops``.
    """
    if source is not None and source.repo_name:
        return source.repo_name
    try:
        candidate = (
            PackageContentSource.objects
            .filter(repo_name__icontains='workshop')
            .order_by('repo_name')
            .values_list('repo_name', flat=True)
            .first()
        )
    except DatabaseError:
        # Lookup is a defensive secondary path; if the DB is briefly
        # unreachable we fall through to the production default. Programmer
        # errors must surface (#605).
        candidate = None
    return candidate or _DEFAULT_WORKSHOPS_REPO


def build_cross_workshop_lookup(workshop_dirs, repo_dir, errors=None):
    """Build a sync-wide ``{folder_name: workshop-meta}`` lookup once per sync.

    Issue #526. ``rewrite_cross_workshop_md_links`` consumes this map to
    rewrite ``..``-relative and absolute-GitHub-URL workshop references in
    every workshop body. A workshop whose ``workshop.yaml`` is missing
    required fields (``slug:`` in particular) is skipped with an ``errors``
    entry.
    """
    lookup = {}
    for workshop_path in workshop_dirs:
        folder_name = os.path.basename(workshop_path.rstrip(os.sep))
        yaml_path = os.path.join(workshop_path, 'workshop.yaml')
        try:
            data = _parse_yaml_file(yaml_path)
        except (ValueError, OSError):
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

        url_key = slug

        pages = {}
        try:
            page_filenames = checkout_listdir(workshop_path)
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
            if not checkout_is_file(page_path):
                continue
            try:
                metadata, _ = _parse_markdown_file(page_path)
            except (ValueError, OSError):
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
            'url_key': url_key,
            'url': f'/workshops/{url_key}',
            'pages': pages,
        }

    return lookup
