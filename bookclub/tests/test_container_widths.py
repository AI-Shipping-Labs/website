"""Guard the outer-width split between the books hub and book pages."""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, tag

EXTENDS_BASE_RE = re.compile(r"{%\s*extends\s+[\"']base\.html[\"']\s*%}")
PAGE_CONTAINER_RE = re.compile(
    r'<(?:div|section|main|article)\b[^>]*\bclass="(?P<classes>[^"]*)"',
    re.IGNORECASE,
)


def _outer_width(source):
    """Return the first centered block-level container width in ``source``."""
    for match in PAGE_CONTAINER_RE.finditer(source):
        classes = match.group('classes').split()
        if 'mx-auto' not in classes:
            continue
        widths = [token for token in classes if token.startswith('max-w-')]
        if widths:
            return widths[0]
    return None


@tag('core')
class PublicBookContainerWidthTest(SimpleTestCase):
    def test_hub_uses_detail_width_and_book_pages_use_reader_width(self):
        template_dir = Path(settings.BASE_DIR) / 'templates' / 'bookclub'
        pages = []
        mismatches = []

        for path in sorted(template_dir.glob('*.html')):
            source = path.read_text(encoding='utf-8')
            if not EXTENDS_BASE_RE.search(source):
                continue
            pages.append(path.name)
            width = _outer_width(source)
            expected = 'max-w-5xl' if path.name == 'index.html' else 'max-w-3xl'
            if width != expected:
                mismatches.append(
                    f'{path.relative_to(settings.BASE_DIR)}: '
                    f'expected {expected}, found {width or "no page container"}'
                )

        self.assertGreaterEqual(len(pages), 7, pages)
        self.assertEqual(
            mismatches,
            [],
            'The books hub must use max-w-5xl and actual book pages max-w-3xl:\n'
            + '\n'.join(mismatches),
        )
