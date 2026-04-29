"""Shared markdown rendering helpers for content and event models."""

import markdown as markdown_lib

from content.markdown_extensions import ExternalLinksExtension, MermaidExtension

MARKDOWN_EXTENSIONS = [
    MermaidExtension,
    ExternalLinksExtension,
    'fenced_code',
    'codehilite',
    'tables',
    'attr_list',
    'md_in_html',
]

MARKDOWN_EXTENSION_CONFIGS = {
    'codehilite': {
        'css_class': 'codehilite',
        'guess_lang': False,
    },
}


def render_markdown(text):
    """Convert markdown to HTML with the platform's runtime extension set."""
    extensions = [
        extension() if isinstance(extension, type) else extension
        for extension in MARKDOWN_EXTENSIONS
    ]
    return markdown_lib.markdown(
        text,
        extensions=extensions,
        extension_configs=MARKDOWN_EXTENSION_CONFIGS,
    )
