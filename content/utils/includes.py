import os
import re

from django.template import Context, Engine

INCLUDE_RE = re.compile(r'<!--\s*include:([A-Za-z0-9_./-]+)\s*-->')


def _resolve_include_path(include_path, base_dir, repo_dir):
    """Resolve an include path without allowing escapes outside the repo."""
    if os.path.isabs(include_path):
        raise ValueError(f'Include path must be relative: {include_path}')

    root = os.path.realpath(repo_dir)
    candidate = os.path.realpath(os.path.join(base_dir, include_path))
    if os.path.commonpath([root, candidate]) != root:
        raise ValueError(f'Include path escapes content repo: {include_path}')
    if not os.path.isfile(candidate):
        raise FileNotFoundError(f'Include file not found: {include_path}')
    return candidate


def expand_content_includes(html, *, repo_dir, base_dir, context):
    """Expand content-owned HTML include markers in rendered HTML.

    Authors can place ``<!-- include:relative/path.html -->`` in markdown.
    The referenced file lives in the content repo and is rendered at sync
    time with a small explicit context, then stored in the database as part
    of the final HTML.
    """
    if not html:
        return html

    engine = Engine.get_default()

    def replace(match):
        include_path = match.group(1).strip()
        resolved = _resolve_include_path(include_path, base_dir, repo_dir)

        with open(resolved, 'r', encoding='utf-8') as f:
            template = engine.from_string(f.read())
        return template.render(Context(context, autoescape=True))

    return INCLUDE_RE.sub(replace, html)
