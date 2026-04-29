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
    'workshop': ['content_id', 'slug', 'title', 'pages_required_level'],
    'workshop_page': ['title'],
    'instructor': ['id', 'name'],
}

INSTRUCTOR_ID_RE = re.compile(r'^[a-z0-9-]+$')
SYNC_LOCK_TIMEOUT_MINUTES = 10


class GitHubSyncError(Exception):
    """Raised when a GitHub sync operation fails."""

