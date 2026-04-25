"""Resolve a ``copy_file`` source to raw markdown.

This helper is content-type agnostic. It exists so that any sync flow that
wants to pull a body / description from a sibling markdown file can share
one validation + read pipeline instead of re-implementing the rules.

Resolution and validation rules live here, but everything that depends on
the calling content type stays in the caller:

- Markdown -> HTML rendering (model ``save()`` does this).
- Image-URL rewriting (needs repo + ``rel_path`` context the caller owns).
- Intra-content ``.md`` link rewriting (semantics differ per content type;
  each caller has its own rewriter).
- Building / using a ``page_lookup`` map.
- Logging "yaml description shadowed by file" notes.
- Choosing what to do when no source resolves (typically fall back to a
  yaml ``description`` field).

The helper returns RAW MARKDOWN with frontmatter stripped and a leading H1
stripped. Callers are responsible for any further per-content-type
processing.

Note on H1 stripping: ``content/utils/h1.py`` has a related
``strip_leading_title_h1`` that only strips a leading H1 when it matches a
known frontmatter title. That is a different (title-aware) policy. The
``copy_file`` helper here strips ANY leading H1 unconditionally — when an
author opts in to ``copy_file`` they have asked for the file body to be
used in place of a description, and a duplicated heading on the rendered
page is exactly the surprise we want to avoid.
"""
import os
import re

import frontmatter

# ATX H1: a single ``#`` followed by whitespace and at least one
# non-whitespace char. We exclude H2+ (``## ...``) and inspect only the
# first non-blank line of the body.
_ATX_H1_RE = re.compile(r'^#\s+\S')

# Setext H1 underline: a run of ``=`` characters on its own line.
_SETEXT_UNDERLINE_RE = re.compile(r'=+')


def _strip_leading_h1(body):
    """Strip a leading H1 heading from ``body`` when present.

    Recognised forms:
        # Title at top
        Title at top\n===== (setext)

    Only the FIRST non-blank line is inspected; deeper H1s are preserved.
    Returns the body unchanged when the first non-blank content isn't an H1.
    """
    if not body:
        return body

    lines = body.splitlines(keepends=True)
    # Find the first non-blank line.
    first_idx = None
    for i, line in enumerate(lines):
        if line.strip():
            first_idx = i
            break
    if first_idx is None:
        return body

    first = lines[first_idx]
    stripped = first.strip()

    # ATX H1: "# Heading" (but NOT "## Heading", "###...").
    if _ATX_H1_RE.match(stripped) and not stripped.startswith('##'):
        del lines[first_idx]
        return ''.join(lines)

    # Setext H1: "Heading\n=====" — the next non-blank line is "=" * n.
    if first_idx + 1 < len(lines):
        next_line = lines[first_idx + 1].strip()
        if next_line and _SETEXT_UNDERLINE_RE.fullmatch(next_line):
            # Remove both the heading text and the underline.
            del lines[first_idx:first_idx + 2]
            return ''.join(lines)

    return body


def _read_markdown_body(filepath):
    """Read a markdown file and return its body with frontmatter stripped."""
    post = frontmatter.load(filepath, encoding='utf-8')
    return post.content


def resolve_copy_file_content(folder, copy_file_setting, default='README.md'):
    """Resolve a ``copy_file`` source to ``(markdown_body, error_message)``.

    Resolution order:

    1. ``copy_file_setting`` truthy -> validate, then read that file from
       ``folder``.
    2. ``copy_file_setting`` falsy AND ``default`` is present in ``folder``
       -> read ``default``.
    3. Neither -> ``(None, None)`` (no source available, not an error).

    Validation (only applied to ``copy_file_setting``; ``default`` is never
    validated beyond ``os.path.isfile``):

    - Must be a string. Otherwise -> ``(None, "copy_file <repr> must be a
      string filename")``.
    - Must NOT contain ``/``, ``..``, or start with ``.``. Otherwise ->
      ``(None, "copy_file <repr> must be a filename in the folder, not a
      path")``.
    - Must end with ``.md`` (case-insensitive). Otherwise -> ``(None,
      "copy_file <repr> must be a .md file")``.
    - Must exist on disk in ``folder``. Otherwise -> ``(None, "copy_file
      <repr> not found in <folder>")``.

    Read pipeline (after validation passes):

    - Parse the file with the YAML-frontmatter parser the sync uses.
    - Strip a leading H1 from the body (ATX ``# Heading`` or Setext
      ``Heading\\n=====``).
    - Return ``(body, None)``. If the file is empty or has only frontmatter,
      return ``('', None)``.

    Args:
        folder: Absolute path to the content folder containing the source
            file.
        copy_file_setting: Value of ``copy_file`` from yaml frontmatter
            (``None`` / ``''`` / ``str``).
        default: Filename to fall back to when ``copy_file_setting`` is
            falsy. Defaults to ``'README.md'``. Pass ``None`` to disable
            the fallback.

    Returns:
        ``tuple[str | None, str | None]``: ``(markdown_body,
        error_message_or_None)``.

        - On success: ``(body_str, None)`` where ``body_str`` may be ``''``.
        - On no-source: ``(None, None)`` — caller decides what to do
          (typically fall back to a yaml ``description`` field).
        - On validation / IO failure for an explicitly declared
          ``copy_file``: ``(None, error_string)`` so the caller can append
          it to its own ``sync_errors`` list.
    """
    explicit_set = bool(copy_file_setting)

    if explicit_set:
        # Validate explicit copy_file before touching disk so authors get
        # actionable error messages rather than IOError surprises.
        if not isinstance(copy_file_setting, str):
            return (
                None,
                f'copy_file {copy_file_setting!r} must be a string filename',
            )

        candidate = copy_file_setting.strip()
        if (
            '/' in candidate
            or '..' in candidate
            or candidate.startswith('.')
        ):
            # Reject path traversal AND subdir paths AND hidden-file refs.
            # ``..`` in any position blocks both ``../foo.md`` and
            # ``foo/../bar.md``. ``/`` blocks subdirectories.
            return (
                None,
                f'copy_file {copy_file_setting!r} must be a filename in '
                f'the folder, not a path',
            )
        if not candidate.lower().endswith('.md'):
            return (
                None,
                f'copy_file {copy_file_setting!r} must be a .md file',
            )

        candidate_path = os.path.join(folder, candidate)
        if not os.path.isfile(candidate_path):
            return (
                None,
                f'copy_file {copy_file_setting!r} not found in {folder}',
            )

        source_path = candidate_path
    else:
        # Implicit fallback: ``default`` if it exists. Missing default is
        # NOT an error — it's just absence of a default.
        if default is None:
            return (None, None)
        default_path = os.path.join(folder, default)
        if not os.path.isfile(default_path):
            return (None, None)
        source_path = default_path

    # Read the markdown body (frontmatter stripped). We deliberately do not
    # catch parse failures here — a corrupt file is a real error and the
    # caller can decide whether to swallow it.
    body = _read_markdown_body(source_path)

    if not body or not body.strip():
        # File present but empty / only frontmatter.
        return ('', None)

    body = _strip_leading_h1(body)
    if not body.strip():
        return ('', None)

    return (body, None)
