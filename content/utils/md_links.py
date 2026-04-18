"""Rewrite intra-content markdown links to platform URLs.

During sync, authors write course unit links in their natural relative form:

    [Setup](02-setup.md)
    [README](README.md)
    [Other module](../03-other-module/01-foo.md)

The platform serves these units at URLs without the ``.md`` extension and with
the numeric prefix stripped (the same slug derivation the sync uses for
``Unit.slug``). Without rewriting, every internal link 404s.

This module provides :func:`rewrite_md_links`, which scans a markdown body for
``[label](path.md)`` style links (including optional ``#anchor`` fragments) and
rewrites them to absolute platform URLs using the supplied unit-slug lookup.

Resolution rules (see issue #226):

- Sibling ``02-setup.md`` -> ``/courses/<course>/<module>/<unit-slug>``
- ``README.md`` (sibling) -> the module's overview page
  ``/courses/<course>/<module>/`` (issue #222). The README is no longer a
  sibling Unit; the sync registers it under the sentinel slug
  ``__module_overview__`` in the unit lookup so we can recognise it here.
- Cross-module same-course ``../03-other-module/01-foo.md``
  -> ``/courses/<course>/03-other-module/<unit-slug>``.
- Cross-course links (more than one ``..`` segment, or a leading ``/``)
  are out of scope: left untouched, logged as a warning.
- External ``http(s)://`` links: untouched.
- Anchor-only links (``#section``): untouched.
- Unresolvable ``.md`` link (filename not in the supplied lookup):
  link text is left intact, a warning is logged.

The rewriter operates on the raw markdown (before HTML conversion) because
matching ``[label](url)`` against a regex is far simpler than parsing the
rendered HTML, and ``[label]`` is opaque to us anyway.
"""

import logging
import posixpath
import re

logger = logging.getLogger(__name__)

# Match a markdown inline link: [label](target). Supports nested brackets in
# the label as long as they are balanced one level deep, and a target that may
# include an optional ``#fragment``. We deliberately do NOT match images
# (``![alt](path)``) — those are handled by ``rewrite_image_urls``.
_MD_LINK_RE = re.compile(
    r'(?<!\!)'                # not an image
    r'\[(?P<label>[^\]]*)\]'  # [label]
    r'\((?P<target>[^)\s]+)'  # (target — no spaces, no closing paren
    r'(?P<title>\s+"[^"]*")?' # optional "title"
    r'\)'
)


def _is_external(target):
    """Return True for absolute URLs we should never rewrite."""
    lower = target.lower()
    return (
        lower.startswith(('http://', 'https://', 'mailto:', 'tel:', 'ftp://'))
        or lower.startswith('//')
    )


def rewrite_md_links(
    body,
    course_slug,
    module_slug,
    unit_lookup,
    source_path=None,
    sync_errors=None,
):
    """Rewrite intra-content ``.md`` links in ``body`` to platform URLs.

    Args:
        body: Raw markdown text.
        course_slug: The slug of the course that owns this body.
        module_slug: The slug of the module that owns this body.
        unit_lookup: Mapping ``{module_slug: {filename: unit_slug}}`` covering
            every unit in the course. ``filename`` is the basename of the
            source file (e.g. ``"02-setup.md"`` or ``"README.md"``); the value
            is the destination ``Unit.slug`` (e.g. ``"setup"`` or ``"readme"``).
        source_path: Repo-relative path of the file being rewritten, used only
            for log/warning messages.
        sync_errors: Optional list to append warning records to. Each record
            has shape ``{'file': source_path, 'error': '...'}`` so it surfaces
            on the SyncLog.

    Returns:
        str: The body with internal ``.md`` links replaced.
    """
    if not body:
        return body

    def _warn(message):
        logger.warning(message)
        if sync_errors is not None:
            sync_errors.append({
                'file': source_path or '',
                'error': message,
            })

    def _resolve(target):
        """Return rewritten URL, or None to leave the original untouched."""
        # Anchor-only and external links: never touch.
        if not target or target.startswith('#') or _is_external(target):
            return None

        # Split off optional fragment.
        if '#' in target:
            path_part, _, fragment = target.partition('#')
            fragment = '#' + fragment
        else:
            path_part = target
            fragment = ''

        # We only rewrite .md targets. Anything else (PDFs, .py, image refs,
        # already-rewritten URLs) is left alone.
        if not path_part.lower().endswith('.md'):
            return None

        # Absolute-within-repo paths (leading "/") are out of scope: we don't
        # know the repo root from a unit's perspective, and these are unusual.
        if path_part.startswith('/'):
            _warn(
                f'Cannot rewrite absolute-within-repo link "{target}" '
                f'in {source_path or "(unknown file)"}: not supported.'
            )
            return None

        # Normalise the path. ``posixpath.normpath`` collapses ``./`` and
        # double slashes but leaves leading ``..`` intact (we use that to
        # tell sibling, cross-module, and cross-course links apart).
        normalised = posixpath.normpath(path_part)
        parts = normalised.split('/')

        # Count leading ".." segments to classify the link.
        up_count = 0
        for part in parts:
            if part == '..':
                up_count += 1
            else:
                break
        remaining = parts[up_count:]

        if up_count == 0 and len(remaining) == 1:
            # Sibling: same module.
            target_module_slug = module_slug
            filename = remaining[0]
        elif up_count == 1 and len(remaining) == 2:
            # Cross-module same-course: ../<other-module>/<file.md>
            # parts[0] is the destination module directory name. The repo
            # dir name may include a numeric prefix (e.g. "03-other-module")
            # while Module.slug strips it, so we map dir name -> slug.
            target_module_dir = remaining[0]
            filename = remaining[1]
            target_module_slug = _module_dir_to_slug(
                target_module_dir, unit_lookup,
            )
            if target_module_slug is None:
                _warn(
                    f'Could not resolve module "{target_module_dir}" for link '
                    f'"{target}" in {source_path or "(unknown file)"}.'
                )
                return None
        elif up_count >= 2:
            # Two or more levels up escapes the course — cross-course,
            # which is explicitly out of scope.
            _warn(
                f'Cross-course or out-of-tree link "{target}" in '
                f'{source_path or "(unknown file)"} left as-is.'
            )
            return None
        else:
            # Anything else (nested subdirs inside a module, etc.) is out of
            # scope — we have no slug for those depths.
            _warn(
                f'Cannot resolve nested link "{target}" in '
                f'{source_path or "(unknown file)"}: unsupported depth.'
            )
            return None

        target_module_units = (
            unit_lookup.get(target_module_slug, {})
            if unit_lookup else {}
        )
        unit_slug = target_module_units.get(filename)
        # Filename lookups should be case-insensitive for README.md-style
        # files where authors mix case.
        if unit_slug is None:
            for known_filename, known_slug in target_module_units.items():
                if known_filename.lower() == filename.lower():
                    unit_slug = known_slug
                    break

        if unit_slug is None:
            _warn(
                f'Unresolvable .md link "{target}" in '
                f'{source_path or "(unknown file)"}: '
                f'no unit found for filename "{filename}" in module '
                f'"{target_module_slug}".'
            )
            return None

        # README.md targets resolve to the module's overview page
        # (issue #222) rather than a /readme unit URL. No trailing slash:
        # the project uses ``RemoveTrailingSlashMiddleware``.
        if unit_slug == '__module_overview__':
            return (
                f'/courses/{course_slug}/{target_module_slug}{fragment}'
            )

        return (
            f'/courses/{course_slug}/{target_module_slug}/{unit_slug}{fragment}'
        )

    def _replace(match):
        target = match.group('target')
        title = match.group('title') or ''
        rewritten = _resolve(target)
        if rewritten is None:
            return match.group(0)
        label = match.group('label')
        return f'[{label}]({rewritten}{title})'

    # When course_slug is missing we cannot construct platform URLs; skip
    # rewriting entirely (defensive: callers should always supply it).
    if not course_slug:
        return body

    return _MD_LINK_RE.sub(_replace, body)


def _module_dir_to_slug(module_dir_name, unit_lookup):
    """Map a repo module directory name (e.g. "03-other-module") to module slug.

    Sync derives ``Module.slug`` from the directory name with
    :func:`derive_slug`, which strips a leading ``\\d+-`` prefix. To stay
    in lock-step without importing the sync helper (and to handle the case
    where authors used the slug directly in the link), we accept either:

    - a direct match against a known module slug;
    - a numeric-prefix-stripped match (``03-other-module`` -> ``other-module``).
    """
    if not unit_lookup:
        return None
    # Direct hit (author wrote `../other-module/foo.md`).
    if module_dir_name in unit_lookup:
        return module_dir_name
    # Strip a numeric prefix and try again.
    stripped = re.sub(r'^\d+-', '', module_dir_name)
    if stripped in unit_lookup:
        return stripped
    return None
