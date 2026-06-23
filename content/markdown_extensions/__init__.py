"""Custom python-markdown extensions used by the AISL render pipeline.

Each extension is shared by every ``render_markdown`` helper across the
content/events models so the rendering output stays consistent regardless
of content type.
"""

from content.markdown_extensions.event_widget import (
    EventWidgetExtension,
    EventWidgetPreprocessor,
)
from content.markdown_extensions.external_links import (
    ExternalLinksExtension,
    ExternalLinksTreeprocessor,
)
from content.markdown_extensions.mermaid import (
    MermaidExtension,
    MermaidPreprocessor,
)

__all__ = [
    'EventWidgetExtension',
    'EventWidgetPreprocessor',
    'ExternalLinksExtension',
    'ExternalLinksTreeprocessor',
    'MermaidExtension',
    'MermaidPreprocessor',
]
