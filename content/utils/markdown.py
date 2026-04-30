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
