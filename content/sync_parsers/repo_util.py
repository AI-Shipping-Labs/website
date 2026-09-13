"""Pure path helpers moved from the retired GitHub sync engine modules."""

import re
from pathlib import PurePath


def extract_sort_order(name):
    """Extract numeric prefix from a filename or directory name.
    '01-day-1' -> 1, '02-setup.md' -> 2, 'intro.md' -> 0
    """
    match = re.match(r'^(\d+)', name)
    return int(match.group(1)) if match else 0


def derive_slug(name):
    """Derive slug from filename/dirname, stripping numeric prefix.
    '01-day-1' -> 'day-1'
    '02-environment.md' -> 'environment'
    'lesson.md' -> 'lesson'
    """
    stem = name.rsplit('.', 1)[0] if '.' in name else name
    match = re.match(r'^\d+-(.+)', stem)
    return match.group(1) if match else stem


def _matches_ignore_patterns(rel_path, patterns):
    """Return True if ``rel_path`` matches any glob in ``patterns``.

    Uses :meth:`pathlib.PurePath.full_match` (Python 3.13+) so recursive
    ``**`` globs work as expected. ``rel_path`` must be relative to whichever
    directory the ignore patterns were declared against (course root for
    course-level ``ignore:``, module dir for module-level).
    """
    if not patterns:
        return False
    p = PurePath(rel_path)
    for pattern in patterns:
        if not pattern:
            continue
        try:
            if p.full_match(pattern):
                return True
        except (ValueError, TypeError):
            # Malformed glob – treat as non-matching rather than blowing up sync.
            continue
    return False


def _interview_question_filename(name):
    """Return True if a root-level filename looks like an interview question.

    Convention: ``<topic>.md`` at the repo root, lowercase kebab-case, not
    a README. Examples: ``python.md``, ``machine-learning.md``.
    """
    if not name.endswith('.md'):
        return False
    base = name[:-3]
    if not base:
        return False
    if name.upper() == 'README.MD':
        return False
    # Allow lowercase letters, digits, and dashes only.
    return all(c.islower() or c.isdigit() or c == '-' for c in base)
