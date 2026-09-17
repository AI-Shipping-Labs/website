"""Markdown rendering for member topic pages (issue #1688).

Topic bodies are authored in the private wiki repository, where sibling
pages are linked as relative ``.md`` files. On the site those links must
navigate to ``/topics/<slug>/``. Rendering rewrites them before the shared
package sanitizer runs, so a stored ``body_html`` never carries a raw
``*.md`` link. Absolute ``https://aishippinglabs.com/...`` canonical links
pass through untouched, as do fragments, site-absolute paths and images.
"""

import os
import re

from community_base.curriculum.rendering import strip_leading_title_h1
from community_base.knowledge_base.rendering import render_markdown

_MD_LINK_PATTERN = re.compile(
    r'(?P<bang>!?)'
    r'\[(?P<text>[^\]]*)\]'
    r'\(\s*(?P<url>\S+?)(?:\s+"[^"]*")?\s*\)'
)

# URLs that never name a sibling markdown file: absolute web URLs (the
# wiki's canonical links), site-absolute paths, fragments and mailto.
_KEEP_PREFIXES = ('http://', 'https://', '/', '#', 'mailto:')


def rewrite_relative_md_links(markdown_text):
    """Rewrite relative ``*.md`` links to ``/topics/<slug>/`` URLs.

    The slug is the target's file stem, matching the sync contract: the
    slug of ``_wiki/<stem>.md`` is ``<stem>``. Non-markdown targets and
    everything under ``_KEEP_PREFIXES`` are returned unchanged.
    """

    def _replace(match):
        url = match.group('url')
        if url.startswith(_KEEP_PREFIXES):
            return match.group(0)
        stem, ext = os.path.splitext(url)
        if ext != '.md':
            return match.group(0)
        slug = stem.rsplit('/', 1)[-1]
        return (
            f'{match.group("bang")}'
            f'[{match.group("text")}](/topics/{slug}/)'
        )

    return _MD_LINK_PATTERN.sub(_replace, markdown_text)


def render_topic_body(body, title):
    """Render one topic body to sanitized HTML for storage in ``body_html``."""

    return render_markdown(
        rewrite_relative_md_links(strip_leading_title_h1(body or '', title)),
    )
