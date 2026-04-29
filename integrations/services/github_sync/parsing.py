"""Markdown/YAML parsing, validation, and diff helpers."""

import hashlib
import os
import uuid

import frontmatter
import yaml

from integrations.services.github_sync.common import REQUIRED_FIELDS, logger
from integrations.services.github_sync.media import rewrite_image_urls


def _extract_readme_title(body, fallback):
    """Return the first Markdown H1 heading in ``body`` or ``fallback``."""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith('# ') and not stripped.startswith('## '):
            return stripped[2:].strip() or fallback
    return fallback


def _derive_readme_content_id(repo_name, module_source_path):
    """Derive a stable UUIDv5 content_id for a module's README-as-unit.

    Used when the README has no explicit ``content_id`` in frontmatter. The
    namespace key combines the repo name and module source path so the UUID is
    stable across syncs and unique across modules/repos.
    """
    key = f'{repo_name}:{module_source_path}:readme'
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def _derive_workshop_page_content_id(repo_name, page_source_path):
    """Derive a stable UUIDv5 content_id for a workshop page.

    Workshop pages are markdown files under ``YYYY/<date-slug>/*.md``. Authors
    rarely want to hand-write a UUID for every page, so the sync derives a
    stable one from ``(repo_name, source_path)``.
    """
    key = f'{repo_name}:{page_source_path}:workshop_page'
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def _parse_markdown_file(filepath):
    """Parse a markdown file with YAML frontmatter.

    Args:
        filepath: Path to the markdown file.

    Returns:
        tuple: (metadata dict, body string)

    Raises:
        ValueError: When the YAML frontmatter cannot be parsed. The
            message has the form ``Failed to parse frontmatter in
            <basename>: <yaml.YAMLError>`` (issue #286). The original
            ``YAMLError`` is preserved as ``__cause__``.
    """
    try:
        post = frontmatter.load(filepath, encoding='utf-8')
    except yaml.YAMLError as exc:
        basename = os.path.basename(filepath)
        raise ValueError(
            f'Failed to parse frontmatter in {basename}: {exc}'
        ) from exc
    return dict(post.metadata), post.content


def _parse_yaml_file(filepath):
    """Parse a YAML file.

    Args:
        filepath: Path to the YAML file.

    Returns:
        dict: Parsed YAML data.

    Raises:
        ValueError: When the file content is not valid YAML or the loaded
            YAML is not a mapping (issue #286). The message has the form
            ``Failed to parse <basename>: <yaml.YAMLError>`` for parse
            failures, or ``Invalid YAML in <filepath>: expected a mapping,
            got <type>`` for top-level lists/scalars. Previously a top-level
            YAML list or scalar would silently slip through
            ``yaml.safe_load(f) or {}`` because the truthy list never
            triggered the ``or {}`` fallback.
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        basename = os.path.basename(filepath)
        raise ValueError(
            f'Failed to parse {basename}: {exc}'
        ) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(
            f'Invalid YAML in {filepath}: expected a mapping, got '
            f'{type(data).__name__}'
        )
    return data


def _validate_frontmatter(metadata, content_type, filepath):
    """Validate that required frontmatter fields are present.

    Args:
        metadata: Parsed frontmatter dict.
        content_type: Content type key from REQUIRED_FIELDS.
        filepath: File path for error messages.

    Raises:
        ValueError: If required fields are missing.
    """
    required = REQUIRED_FIELDS.get(content_type, [])
    # A field is "missing" when the key is absent or its value is None,
    # an empty string, or an empty list. Crucially, ``0`` counts as
    # present — numeric fields like ``pages_required_level`` legitimately
    # accept zero (``LEVEL_OPEN``) and must not trip the "missing" check.
    missing = [
        f for f in required
        if metadata.get(f) is None or metadata.get(f) == '' or metadata.get(f) == []
    ]
    if missing:
        raise ValueError(
            f"Missing required field(s) in {filepath}: {', '.join(missing)}"
        )


def _check_slug_collision(model_class, slug, source_repo, filepath):
    """Check for slug collision with a different source.

    Returns True if a collision exists (file should be skipped).
    Returns False if no collision.
    """
    existing = model_class.objects.filter(slug=slug).exclude(
        source_repo=source_repo,
    ).first()
    if existing:
        other_source = existing.source_repo or 'studio'
        logger.warning(
            "Slug collision: '%s' already exists from source '%s' "
            "(source_repo=%s). Skipped %s.",
            slug, other_source, existing.source_repo, filepath,
        )
        return True
    return False

def _compute_content_hash(text):
    """Compute MD5 hex digest of text for rename detection."""
    return hashlib.md5(text.encode('utf-8')).hexdigest()


# Fields ignored when deciding whether a re-sync actually changed an item.
# ``source_commit`` bumps on every sync to whatever the current HEAD is, so
# comparing it would mark every item as updated (defeating the whole point of
# issue #225). ``source_repo`` and ``source_path`` are scope/identity fields
# we look up by; they would always be equal and including them is just noise.
_NO_CHANGE_IGNORED_FIELDS = frozenset({
    'source_commit',
    'source_repo',
    'source_path',
})


def _defaults_differ(instance, defaults):
    """Return True if any value in ``defaults`` differs from ``instance``.

    Used by the per-content-type sync helpers to decide whether an existing
    row needs to be re-saved on a re-sync (issue #225). When this returns
    False the sync skips the save AND skips the items_detail entry, so the
    sync report only lists items whose content actually changed.

    Notes on normalization:

    - ``tags`` is a JSONField list that the model ``save()`` normalizes
      (lowercase, hyphenated). The incoming defaults have not been
      normalized yet, so we normalize before comparing — otherwise an
      author-cased tag like ``Python`` would always look different from
      the stored ``python`` and the row would re-save on every sync.
    - Fields in :data:`_NO_CHANGE_IGNORED_FIELDS` are skipped because they
      either change every run (``source_commit``) or are scope keys that
      cannot differ for a row we just looked up (``source_repo``,
      ``source_path``).
    """
    for field, new_value in defaults.items():
        if field in _NO_CHANGE_IGNORED_FIELDS:
            continue
        current = getattr(instance, field, None)
        if field == 'tags' and isinstance(new_value, list):
            from content.utils.tags import normalize_tags
            new_value = normalize_tags(new_value)
        # ``content_id`` is stored as a UUID but YAML frontmatter parses
        # to a string. Coerce both sides to a UUID for the comparison so a
        # re-sync of the same file doesn't look like a diff.
        if field == 'content_id' and current is not None and new_value is not None:
            if isinstance(current, uuid.UUID) and isinstance(new_value, str):
                try:
                    new_value = uuid.UUID(new_value)
                except (ValueError, AttributeError):
                    pass
            elif isinstance(new_value, uuid.UUID) and isinstance(current, str):
                try:
                    current = uuid.UUID(current)
                except (ValueError, AttributeError):
                    pass
        if current != new_value:
            return True
    return False

def _render_event_recap_file(repo_dir, event_rel_path, data, source, rel_path):
    """Render an event recap markdown file and content-owned includes.

    ``recap_file`` is resolved relative to the event YAML/Markdown file. The
    recap file may have frontmatter used by content includes.

    Recap markdown and includes are trusted content-repo input, matching the
    rest of the synced markdown pipeline. Do not point this at user uploads.
    """
    recap_file = data.get('recap_file') or data.get('recap-file') or ''
    if not recap_file:
        return {
            'recap_file': '',
            'recap_markdown': '',
            'recap_html': '',
            'recap_data': {},
        }

    if os.path.isabs(recap_file):
        raise ValueError(f'recap_file must be relative in {rel_path}')

    event_base = os.path.dirname(event_rel_path)
    recap_rel_path = os.path.normpath(os.path.join(event_base, recap_file))
    repo_root = os.path.realpath(repo_dir)
    recap_path = os.path.realpath(os.path.join(repo_dir, recap_rel_path))
    if os.path.commonpath([repo_root, recap_path]) != repo_root:
        raise ValueError(f'recap_file escapes content repo in {rel_path}')
    if not os.path.isfile(recap_path):
        raise FileNotFoundError(f'recap_file not found in {rel_path}: {recap_file}')

    metadata, body = _parse_markdown_file(recap_path)
    recap_data = metadata
    if recap_data and not isinstance(recap_data, dict):
        raise ValueError(f'frontmatter in {recap_rel_path} must be a mapping/dict')

    recap_base_dir = os.path.dirname(recap_rel_path)
    body = rewrite_image_urls(body, source.repo_name, recap_base_dir)

    from django.template.loader import render_to_string
    from django.utils.safestring import mark_safe

    from content.utils.includes import expand_content_includes
    from content.utils.linkify import linkify_urls
    from events.models.event import render_markdown

    event_context = {
        'title': data.get('title', ''),
        'slug': data.get('slug', ''),
        'description': data.get('description', ''),
        'start_datetime': data.get('start_datetime'),
        'end_datetime': data.get('end_datetime'),
        'location': data.get('location', ''),
        'recording_url': data.get('recording_url', '') or data.get('video_url', ''),
        'recording_embed_url': (
            data.get('recording_embed_url', '')
            or data.get('google_embed_url', '')
        ),
        'recording_s3_url': data.get('recording_s3_url', ''),
        'timestamps': data.get('timestamps', []),
    }
    event_context['recording_html'] = mark_safe(render_to_string(
        'events/_recording_embed.html',
        {'event': event_context},
    ))
    html = linkify_urls(render_markdown(body))
    html = expand_content_includes(
        html,
        repo_dir=repo_dir,
        base_dir=os.path.dirname(recap_path),
        context={
            'data': recap_data,
            'event': event_context,
        },
    )

    return {
        'recap_file': recap_file,
        'recap_markdown': body,
        'recap_html': html,
        'recap_data': recap_data,
    }
