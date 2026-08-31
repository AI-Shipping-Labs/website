import os
import re

from django.template import Context, Engine

from integrations.services.github_sync.checkout import (
    active_checkout,
    checkout_is_file,
    checkout_read_text,
    checkout_scope,
)

INCLUDE_RE = re.compile(r'<!--\s*include:([A-Za-z0-9_./-]+)\s*-->')


def _resolve_include_path(include_path, base_dir, repo_dir):
    """Resolve an include path without allowing escapes outside the repo."""
    if os.path.isabs(include_path):
        raise ValueError(f'Include path must be relative: {include_path}')
    if '..' in include_path.split('/'):
        raise ValueError(f'Include path escapes content repo: {include_path}')

    root = os.path.abspath(repo_dir)
    if include_path.startswith('widgets/'):
        candidate = os.path.join(root, include_path)
    else:
        candidate = os.path.join(base_dir, include_path)
    checkout = active_checkout()
    if checkout is not None:
        checkout.relative(candidate)
    elif os.path.commonpath([root, os.path.abspath(candidate)]) != root:
        raise ValueError(f'Include path escapes content repo: {include_path}')
    if not checkout_is_file(candidate):
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

    with checkout_scope(repo_dir):
        return _expand_content_includes_from_checkout(
            html, repo_dir=repo_dir, base_dir=base_dir, context=context,
        )


def _expand_content_includes_from_checkout(
    html, *, repo_dir, base_dir, context,
):

    engine = Engine.get_default()

    def replace(match):
        include_path = match.group(1).strip()
        resolved = _resolve_include_path(include_path, base_dir, repo_dir)

        template = engine.from_string(checkout_read_text(resolved))
        return template.render(Context(context, autoescape=True))

    return INCLUDE_RE.sub(replace, html)
