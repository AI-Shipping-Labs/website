"""Shared constants and exceptions for GitHub content sync."""

import logging
import re

logger = logging.getLogger('integrations.services.github')

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.ico'}
CONTENT_EXTENSIONS = {'.md', '.yaml', '.yml'}

GITHUB_API_BASE = 'https://api.github.com'
INSTALLATION_REPOS_CACHE_KEY = 'github_installation_repositories'
INSTALLATION_REPOS_CACHE_TIMEOUT = 60

REQUIRED_FIELDS = {
    'article': ['title'],
    'course': ['title'],
    'module': ['title'],
    'unit': ['title'],
    'event': ['title'],
    'project': ['title'],
    'curated_link': ['title', 'url', 'item_id'],
    'download': ['title'],
    'marketing_page': ['content_id', 'title', 'public_path'],
    'workshop': ['content_id', 'slug', 'title', 'pages_required_level'],
    'workshop_page': ['title'],
    'instructor': ['id', 'name'],
    # Knowledge base pages (issue #1685): page identity in the package is
    # the slug, but the content repository's every-file-content_id
    # convention still applies to wiki/ and docs/ markdown.
    'wiki_pages': ['content_id', 'title'],
    'docs_pages': ['content_id', 'title'],
    # Member wiki topic pages (issue #1688): the private wiki repo carries
    # no content_id frontmatter; the file stem is the identity.
    'wiki_topics': ['title'],
}

INSTRUCTOR_ID_RE = re.compile(r'^[a-z0-9-]+$')
SYNC_LOCK_TIMEOUT_MINUTES = 10


class GitHubSyncError(Exception):
    """Raised when a GitHub sync operation fails."""
