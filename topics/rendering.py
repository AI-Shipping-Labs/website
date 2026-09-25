"""Markdown rendering for member topic pages (issues #1688, #1815).

Topic bodies are authored in the private wiki repository, where sibling
pages are linked as relative ``.md`` files. On the site those links must
navigate to ``/topics/<slug>/``. Rendering rewrites them before the shared
package sanitizer runs, so a stored ``body_html`` never carries a raw
``*.md`` link. Absolute ``https://aishippinglabs.com/...`` canonical links
pass through untouched, as do fragments, site-absolute paths and images.

Since #1815 the rewrite resolves each stem against the published
``TopicPage`` slugs: a stem without a published page renders as plain
text with no anchor, so a stored ``body_html`` never carries an internal
``/topics/`` href that 404s. The hub stem links to ``/topics/`` itself --
``index`` is reserved and there is no ``/topics/index/`` route.
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

# The hub stem: index.md is the hub page rendered at /topics/, and the
# slug is reserved in the page namespace (no /topics/index/ route).
# Mirrors topics.models.HUB_SLUG without importing models from here.
_HUB_SLUG = 'index'
_HUB_URL = '/topics/'


def published_topic_slugs():
    """The slugs an internal ``/topics/`` href may point at.

    Late import: ``topics.models`` imports this module for its
    save-time render hook, so the reverse import has to wait until
    call time.
    """

    from topics.models import (  # noqa: PLC0415 - model imports this module for the save() render hook
        STATUS_PUBLISHED,
        TopicPage,
    )

    return set(
        TopicPage.objects.filter(status=STATUS_PUBLISHED)
        .values_list('slug', flat=True)
    )


def rewrite_relative_md_links(markdown_text, published_slugs=None):
    """Rewrite relative ``*.md`` links to ``/topics/<slug>/`` URLs.

    The slug is the target's file stem, matching the sync contract: the
    slug of ``_wiki/<stem>.md`` is ``<stem>``. Non-markdown targets and
    everything under ``_KEEP_PREFIXES`` are returned unchanged.

    With ``published_slugs``, only stems in that set become links; any
    other stem degrades to its plain link text with no anchor, so a
    stored ``body_html`` never carries an internal href that 404s
    (#1815). The hub stem points at ``/topics/`` instead of the
    reserved-slug detail path. ``None`` keeps the unconditional legacy
    rewrite for callers that resolve the set themselves.
    """

    def _replace(match):
        url = match.group('url')
        if url.startswith(_KEEP_PREFIXES):
            return match.group(0)
        stem, ext = os.path.splitext(url)
        if ext != '.md':
            return match.group(0)
        slug = stem.rsplit('/', 1)[-1]
        if published_slugs is not None and slug not in published_slugs:
            return match.group('text')
        href = _HUB_URL if slug == _HUB_SLUG else f'/topics/{slug}/'
        return (
            f'{match.group("bang")}'
            f'[{match.group("text")}]({href})'
        )

    return _MD_LINK_PATTERN.sub(_replace, markdown_text)


def render_topic_body(body, title, published_slugs=None):
    """Render one topic body to sanitized HTML for storage in ``body_html``.

    Relative ``.md`` links resolve against the published slug set -- the
    live set unless one is passed in -- so a page is never stored with a
    dead internal href: an absent target renders as plain text until a
    later sync publishes it.
    """

    if published_slugs is None:
        published_slugs = published_topic_slugs()
    return render_markdown(
        rewrite_relative_md_links(
            strip_leading_title_h1(body or '', title),
            published_slugs=published_slugs,
        ),
    )
