"""Regression guard: every registry ``docs_url`` anchor must resolve.

Each key in ``integrations.settings_registry.INTEGRATION_GROUPS`` MAY define a
``docs_url`` (issue #641) of the form ``_docs/integrations/<group>.md#<anchor>``.
Studio renders a (?) help icon linking to that anchor. If the target header does
not exist, the help link 404s. This test asserts every ``docs_url`` points at a
file that exists and an anchor that matches a real Markdown header, so a new
setting cannot ship without its doc section (issue #1323).
"""

import re

from django.conf import settings
from django.test import SimpleTestCase

from integrations.settings_registry import INTEGRATION_GROUPS

# Repo root: BASE_DIR is the Django project root, which is the repo root here.
REPO_ROOT = settings.BASE_DIR


def _github_anchor(header_text):
    """Slugify a Markdown header the way GitHub does for in-page anchors.

    Lowercase, strip characters that are not word chars / hyphens / spaces,
    then convert spaces to hyphens. This matches the anchors GitHub generates
    for the blob view Studio links to.
    """
    slug = header_text.strip().lower()
    slug = re.sub(r'[^\w\- ]', '', slug)
    return slug.replace(' ', '-')


def _headers_for(path):
    anchors = set()
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            match = re.match(r'^#{1,6}\s+(.*)', line)
            if match:
                anchors.add(_github_anchor(match.group(1)))
    return anchors


class DocsUrlAnchorResolutionTest(SimpleTestCase):
    """Every registered ``docs_url`` resolves to a real header."""

    def test_every_docs_url_anchor_resolves(self):
        failures = []
        # Cache parsed headers per doc file across keys.
        headers_cache = {}

        for group in INTEGRATION_GROUPS:
            for key in group['keys']:
                docs_url = key.get('docs_url')
                if not docs_url:
                    continue
                self.assertIn(
                    '#', docs_url,
                    f"{key['key']}: docs_url '{docs_url}' has no #anchor",
                )
                rel_path, anchor = docs_url.split('#', 1)
                abs_path = REPO_ROOT / rel_path

                if not abs_path.exists():
                    failures.append(
                        f"{key['key']}: target file '{rel_path}' does not exist"
                    )
                    continue

                if rel_path not in headers_cache:
                    headers_cache[rel_path] = _headers_for(abs_path)

                if anchor not in headers_cache[rel_path]:
                    failures.append(
                        f"{key['key']}: anchor '#{anchor}' not found in {rel_path}"
                    )

        self.assertEqual(
            failures, [],
            "Broken docs_url anchors (add the missing doc section):\n"
            + "\n".join(failures),
        )
