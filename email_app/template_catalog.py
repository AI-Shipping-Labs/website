"""Catalog and mapping for Relay email templates (AC 2).

Provides the code-level inventory of all transactional email templates used by
package mail and the site, their approved default sender IDs, categories, and
versions.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import frontmatter
from django.conf import settings

from email_app.management.commands.sync_relay_templates import (
    resolve_default_sender_id,
)


@dataclass(frozen=True)
class TemplateMetadata:
    key: str
    default_sender_id: str
    category: str
    version: int = 1


def get_template_catalog(template_dir: Path | None = None) -> dict[str, TemplateMetadata]:
    """Return a mapping of all template keys to their metadata."""
    directory = template_dir or (settings.BASE_DIR / "email_app" / "email_templates")
    catalog: dict[str, TemplateMetadata] = {}

    for path in sorted(directory.glob("*.md")):
        key = path.stem
        post = frontmatter.load(str(path))
        category = str(post.metadata.get("category", "")).strip()
        sender_id = resolve_default_sender_id(key)
        catalog[key] = TemplateMetadata(
            key=key,
            default_sender_id=sender_id,
            category=category,
            version=1,
        )

    # Always ensure raw_transactional is in catalog
    catalog["raw_transactional"] = TemplateMetadata(
        key="raw_transactional",
        default_sender_id="noreply",
        category="",
        version=1,
    )

    return catalog
