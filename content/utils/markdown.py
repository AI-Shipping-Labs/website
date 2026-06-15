"""Shared markdown rendering helpers for content and event models."""

import markdown as markdown_lib
import nh3

from content.markdown_extensions import ExternalLinksExtension, MermaidExtension
from content.utils.linkify import linkify_urls

# nh3 (ammonia) allowlist for sanitising rendered markdown HTML. It covers
# every element the platform renderer legitimately emits (headings, lists,
# tables, code/pre, blockquotes, links, images, and the mermaid/codehilite
# ``<div>``/``<span class=...>`` wrappers) while stripping anything else —
# notably ``<script>``/``<style>`` and inline event handlers — so a raw
# ``<script>`` in an untrusted description is removed rather than executed.
# Markdown-generated structure (a real ``<ul>``, an ``<a target rel>``) is
# preserved unchanged, so legitimate content renders identically.
_SANITIZE_TAGS = {
    'a', 'abbr', 'b', 'blockquote', 'br', 'code', 'div', 'em', 'h1', 'h2',
    'h3', 'h4', 'h5', 'h6', 'hr', 'i', 'img', 'li', 'ol', 'p', 'pre', 'span',
    'strong', 'sub', 'sup', 'table', 'tbody', 'td', 'th', 'thead', 'tr', 'ul',
}
_SANITIZE_ATTRIBUTES = {
    'a': {'href', 'title', 'target', 'rel'},
    'img': {'src', 'alt', 'title'},
    'div': {'class'},
    'span': {'class'},
    'code': {'class'},
    'pre': {'class'},
    'td': {'align'},
    'th': {'align'},
}


def sanitize_html(html):
    """Strip dangerous HTML (``<script>``, event handlers, etc.) from already
    rendered markdown while preserving the platform's legitimate output tags.
    """
    return nh3.clean(
        html,
        tags=_SANITIZE_TAGS,
        attributes=_SANITIZE_ATTRIBUTES,
        link_rel=None,  # keep the rel emitted by ExternalLinksExtension as-is
    )

MARKDOWN_CORE_EXTENSIONS = [
    'fenced_code',
    'codehilite',
    'tables',
    'attr_list',
    'md_in_html',
]

MARKDOWN_EXTENSIONS = [
    MermaidExtension,
    ExternalLinksExtension,
    *MARKDOWN_CORE_EXTENSIONS,
]

MARKDOWN_EXTENSION_CONFIGS = {
    'codehilite': {
        'css_class': 'codehilite',
        'guess_lang': False,
    },
}


def _build_extensions(*, include_mermaid=True, include_external_links=True):
    extensions = []
    if include_mermaid:
        extensions.append(MermaidExtension())
    if include_external_links:
        extensions.append(ExternalLinksExtension())
    extensions.extend(MARKDOWN_CORE_EXTENSIONS)
    return extensions


def _build_extension_configs(*, codehilite_guess_lang=False):
    configs = {
        name: config.copy()
        for name, config in MARKDOWN_EXTENSION_CONFIGS.items()
    }
    configs['codehilite']['guess_lang'] = codehilite_guess_lang
    return configs


def render_markdown(
    text,
    *,
    include_mermaid=True,
    include_external_links=True,
    codehilite_guess_lang=False,
):
    """Convert markdown to HTML with the platform's runtime extension set."""
    return markdown_lib.markdown(
        text,
        extensions=_build_extensions(
            include_mermaid=include_mermaid,
            include_external_links=include_external_links,
        ),
        extension_configs=_build_extension_configs(
            codehilite_guess_lang=codehilite_guess_lang,
        ),
    )


def render_description_html(text):
    """Render an event / event-series description to safe display HTML.

    Issue #988: events and series descriptions render through the same
    markdown + bare-URL-linkify pipeline as courses/articles, then through
    ``sanitize_html`` so a raw ``<script>`` in an untrusted description is
    stripped rather than executed (an event detail page is a public surface).
    Returns ``''`` for empty input.
    """
    if not text:
        return ''
    return sanitize_html(linkify_urls(render_markdown(text)))
