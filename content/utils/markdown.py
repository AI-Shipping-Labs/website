"""Shared markdown rendering helpers for content and event models."""

import markdown as markdown_lib

from content.markdown_extensions import ExternalLinksExtension, MermaidExtension

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


def _build_extensions(
    *,
    include_mermaid=True,
    include_external_links=True,
    include_codehilite=True,
):
    extensions = []
    if include_mermaid:
        extensions.append(MermaidExtension())
    if include_external_links:
        extensions.append(ExternalLinksExtension())
    for name in MARKDOWN_CORE_EXTENSIONS:
        if name == 'codehilite' and not include_codehilite:
            continue
        extensions.append(name)
    return extensions


def _build_extension_configs(*, codehilite_guess_lang=False, include_codehilite=True):
    configs = {
        name: config.copy()
        for name, config in MARKDOWN_EXTENSION_CONFIGS.items()
        if include_codehilite or name != 'codehilite'
    }
    if include_codehilite:
        configs['codehilite']['guess_lang'] = codehilite_guess_lang
    return configs


def render_markdown(
    text,
    *,
    include_mermaid=True,
    include_external_links=True,
    include_codehilite=True,
    codehilite_guess_lang=False,
):
    """Convert markdown to HTML with the platform's runtime extension set.

    ``include_codehilite=False`` drops the codehilite extension entirely, so
    fenced code blocks render as plain ``<pre><code>`` without the syntax-
    highlight CSS classes. Email callers use this because inboxes have no
    codehilite stylesheet (issue #989).
    """
    return markdown_lib.markdown(
        text,
        extensions=_build_extensions(
            include_mermaid=include_mermaid,
            include_external_links=include_external_links,
            include_codehilite=include_codehilite,
        ),
        extension_configs=_build_extension_configs(
            codehilite_guess_lang=codehilite_guess_lang,
            include_codehilite=include_codehilite,
        ),
    )


def render_email_markdown(text):
    """Render markdown for email using the canonical renderer (issue #989).

    Email is the same parser as the website (lists, tables, links, emphasis,
    escaping) but with the two browser-only features disabled:

    - ``include_mermaid=False`` — emails cannot run the mermaid JS that turns
      the placeholder into a diagram.
    - ``include_codehilite=False`` — inboxes ship no codehilite stylesheet, so
      the highlight classes would be dead markup.

    External-link handling stays on so a bare external link still opens in a
    new tab with ``rel="noopener"``. This is the single email-markdown entry
    point; no email path should call the ``markdown`` library directly.
    """
    return render_markdown(
        text,
        include_mermaid=False,
        include_codehilite=False,
    )
