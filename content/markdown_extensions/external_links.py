"""External-link rewriting for python-markdown (issue #303).

Adds ``target="_blank"`` and ``rel="...noopener"`` to ``<a>`` elements that
point to a host other than the resolved ``SITE_BASE_URL`` (DB override
preferred, env fallback). Internal links
(anchors, relative paths, root-relative paths, same-domain absolute
URLs, and non-http schemes such as ``mailto:`` and ``tel:``) are left
alone.

The rewrite runs as a :class:`Treeprocessor`, not a postprocessor, so the
DOM walk happens on parsed ``ElementTree`` nodes after inline parsing —
this is cleaner than regex-substituting on serialised HTML, and it
naturally avoids touching the mermaid stash placeholder (which only
becomes real HTML at postprocess time).

Notes on what the treeprocessor does and does NOT touch:

- Author-written ``[text](url)`` links produce real ``<a>`` element nodes
  in the tree, which we walk via ``root.iter('a')``.
- ``attr_list`` attaches attributes (``href``, ``rel``, etc.) to those
  same nodes, so we see and preserve them.
- Raw HTML ``<a>`` tags written directly in markdown source are stashed
  by python-markdown and reinjected at postprocess time, so the
  treeprocessor never sees them. That means a handwritten
  ``<a href="..." target="_self">`` is preserved verbatim — exactly what
  the spec requires.
"""

from urllib.parse import urlparse

from markdown.extensions import Extension
from markdown.treeprocessors import Treeprocessor

from integrations.config import site_base_url


def _site_hosts():
    """Return the set of lowercase hostnames considered "internal".

    Source of truth is the resolved ``SITE_BASE_URL`` (DB override >
    env), via ``integrations.config.site_base_url()``. We deliberately
    do NOT use ``ALLOWED_HOSTS`` because it can include wildcards (``*``)
    and dev hosts (``localhost``); a wildcard would silently treat every
    link as internal.

    Both the configured host and its ``www.``-flipped sibling are added
    so ``aishippinglabs.com`` and ``www.aishippinglabs.com`` are both
    recognised as the site.

    If ``SITE_BASE_URL`` is empty/unset we return an empty set, which
    means every absolute ``http(s)://...`` URL is treated as external.
    That's the safer default for a misconfigured deployment — and it
    matches the dev intent: with ``SITE_BASE_URL=http://localhost:8000``,
    a stray ``https://aishippinglabs.com/...`` link in dev content is
    correctly treated as external (i.e. opens in a new tab) rather than
    silently swept under the same-origin rug.

    Computed on every ``Treeprocessor.run()`` call (not at module
    import) so test ``@override_settings(SITE_BASE_URL=...)`` decorators
    take effect.
    """
    hosts = set()
    site_url = site_base_url() or ''
    if site_url:
        netloc = urlparse(site_url).netloc.lower()
        if netloc:
            hosts.add(netloc)
            if netloc.startswith('www.'):
                hosts.add(netloc[4:])
            else:
                hosts.add(f'www.{netloc}')
    return hosts


class ExternalLinksTreeprocessor(Treeprocessor):
    """Walk every ``<a>`` and rewrite external ones to open in a new tab.

    Decision logic per element:

    1. Missing/empty ``href`` -> skip.
    2. Non-http(s) scheme (``mailto:``, ``tel:``, ``javascript:``, anchor
       fragments like ``#x``) -> skip (internal).
    3. Empty ``netloc`` (relative path, root-relative path) -> skip.
    4. ``netloc`` matches the configured site host -> skip.
    5. Otherwise -> external. Set ``target="_blank"`` (only if not
       already set) and append ``noopener`` to ``rel`` (only if not
       already present), preserving any other rel tokens like
       ``noreferrer`` or ``nofollow``.
    """

    def run(self, root):
        site_hosts = _site_hosts()

        for el in root.iter('a'):
            href = el.get('href')
            if not href:
                continue
            href = href.strip()
            if not href:
                continue

            parsed = urlparse(href)
            scheme = (parsed.scheme or '').lower()
            netloc = (parsed.netloc or '').lower()

            # Non-http schemes (mailto:, tel:, javascript:) and pure
            # fragments (#section) are internal-ish — leave them alone.
            if scheme not in ('http', 'https'):
                continue
            # Relative or root-relative paths have no netloc.
            if not netloc:
                continue
            # Same-domain absolute URLs are internal.
            if netloc in site_hosts:
                continue

            # Honour an explicit author-set ``target`` (e.g. _self) and
            # do not overwrite it.
            if not el.get('target'):
                el.set('target', '_blank')

            # Preserve existing rel tokens; append ``noopener`` only if
            # it's not already there. Comparison is case-insensitive but
            # we keep authors' original casing on the tokens we don't
            # touch.
            existing_rel = el.get('rel', '') or ''
            tokens = existing_rel.split()
            lower_tokens = {t.lower() for t in tokens}
            if 'noopener' not in lower_tokens:
                tokens.append('noopener')
            el.set('rel', ' '.join(tokens))


class ExternalLinksExtension(Extension):
    """Register :class:`ExternalLinksTreeprocessor` at priority 0.

    Priority 0 runs after the built-in inline/block treeprocessors
    (``InlineProcessor``, ``PrettifyTreeprocessor``,
    ``UnescapeTreeprocessor``) so we walk the final ``<a>`` elements
    after attr_list has attached its attributes.
    """

    def extendMarkdown(self, md):
        md.treeprocessors.register(
            ExternalLinksTreeprocessor(md),
            'external_links',
            0,
        )
