"""Compatibility facade for GitHub content sync.

The implementation lives in ``integrations.services.github_sync``. This module
keeps legacy imports and django-q task strings stable while callers migrate to
more focused modules.
"""

# ruff: noqa: F401

from integrations.services.github_sync.client import (
    clear_installation_repositories_cache,
    find_content_source,
    generate_github_app_token,
    list_installation_repositories,
    validate_webhook_signature,
)
from integrations.services.github_sync.common import (
    CONTENT_EXTENSIONS,
    GITHUB_API_BASE,
    IMAGE_EXTENSIONS,
    INSTALLATION_REPOS_CACHE_KEY,
    INSTALLATION_REPOS_CACHE_TIMEOUT,
    INSTRUCTOR_ID_RE,
    REQUIRED_FIELDS,
    SYNC_LOCK_TIMEOUT_MINUTES,
    GitHubSyncError,
    logger,
)
from integrations.services.github_sync.dispatchers.articles import _dispatch_articles
from integrations.services.github_sync.dispatchers.courses import (
    _build_course_unit_lookup,
    _build_workshop_page_lookup,
    _dispatch_courses,
    _reattach_course_fks,
    _resolve_workshop_landing_copy,
    _sync_course_modules,
    _sync_module_units,
    _sync_single_course,
)
from integrations.services.github_sync.dispatchers.curated_links import _dispatch_curated_links
from integrations.services.github_sync.dispatchers.downloads import _dispatch_downloads
from integrations.services.github_sync.dispatchers.events import (
    _coerce_event_datetime,
    _dispatch_events,
    _event_requests_zoom_meeting,
    _maybe_create_zoom_meeting_for_synced_event,
)
from integrations.services.github_sync.dispatchers.instructors import (
    _attach_instructors_to_course,
    _attach_instructors_to_event,
    _attach_instructors_to_workshop,
    _dispatch_instructors,
    _resolve_instructors_for_yaml,
)
from integrations.services.github_sync.dispatchers.interview_questions import _dispatch_interview_questions
from integrations.services.github_sync.dispatchers.projects import _dispatch_projects
from integrations.services.github_sync.dispatchers.workshops import (
    _coerce_workshop_date,
    _derive_workshop_event_content_id,
    _dispatch_workshops,
    _extract_workshop_folder_date,
    _link_or_create_workshop_event,
    _sync_single_workshop,
    _sync_workshop_pages,
)
from integrations.services.github_sync.media import (
    _check_broken_image_refs,
    _collect_image_paths,
    _md5_file,
    rewrite_cover_image_url,
    rewrite_image_urls,
    upload_images_to_s3,
)
from integrations.services.github_sync.orchestration import (
    _classify_repo_files,
    _count_content_files,
    _sync_repo,
    _sync_tiers_yaml,
    acquire_sync_lock,
    release_sync_lock,
    sync_content_source,
)
from integrations.services.github_sync.parsing import (
    _check_slug_collision,
    _compute_content_hash,
    _defaults_differ,
    _derive_readme_content_id,
    _derive_workshop_page_content_id,
    _extract_readme_title,
    _parse_markdown_file,
    _parse_yaml_file,
    _render_event_recap_file,
    _validate_frontmatter,
)
from integrations.services.github_sync.repo import (
    _interview_question_filename,
    _matches_ignore_patterns,
    _resolve_local_repo_sha,
    clone_or_pull_repo,
    derive_slug,
    extract_sort_order,
    fetch_remote_head_sha,
)
