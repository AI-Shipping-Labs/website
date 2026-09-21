"""AISL site-registered content kinds (A7.2a).

Member-wiki ruling, recorded before any kind is registered: public ``wiki/``
pages are the package ``wiki`` kind and live in ``community_base.knowledge_base``.
Member-gated pages under ``_wiki/`` are this site's ``member_wiki`` kind, stored
in the ``topics`` app and gated at Basic and above. The C7.12 ``aisl-wiki``
profile that writes ``kind: wiki, path: wiki`` must not be pointed at ``_wiki/``;
A7.3 converts that section as ``member_wiki``.

Importing this module is side-effect free. ``register_aisl_kinds()`` is
idempotent and is the module ``check_content --kinds content.kinds`` loads.
"""

from __future__ import annotations

from community_base.content_sync.kinds import (
    SHAPE_DOCUMENT,
    SHAPE_MANIFEST,
    DirNode,
    KeySpec,
    KindSpec,
    Layout,
    Problem,
    RawItem,
    is_registered,
    register_kind,
)
from community_base.content_sync.kinds.layouts import (
    FlatLayout,
    ItemDirectoryLayout,
    is_asset_dir,
)


class NestedManifestLayout(Layout):
    """Find ``document`` at any depth (workshop.yaml under year/month/slug)."""

    def __init__(self, part: str, document: str) -> None:
        self.part = part
        self.document = document

    def walk(self, root: DirNode) -> tuple[list[RawItem], list[tuple[str, Problem]]]:
        items: list[RawItem] = []
        self._visit(root, items)
        return items, []

    def _visit(self, node: DirNode, items: list[RawItem]) -> None:
        if node.has(self.document):
            items.append(
                RawItem(
                    part=self.part,
                    path=node.joined(self.document),
                    container=node.path,
                    name=node.path.rsplit("/", 1)[-1] or self.document,
                )
            )
        for child in node.dirs:
            if not is_asset_dir(child):
                self._visit(child, items)


def register_aisl_kinds() -> None:
    """Register the five AISL-owned kinds. Safe to call more than once."""

    specs = (
        KindSpec(
            name="workshop",
            shape=SHAPE_MANIFEST,
            layout=NestedManifestLayout(part="workshop", document="workshop.yaml"),
            requires_date=True,
            keys={
                "byline": KeySpec("text", max_length=300),
                "event_slug": KeySpec("slug"),
                "pages_required_level": KeySpec("level"),
                "landing_required_level": KeySpec("level"),
                "code_repo_url": KeySpec("url"),
                "materials": KeySpec("mapping"),
                "recording": KeySpec("mapping"),
            },
            route=lambda path: f"workshops/{path}",
        ),
        KindSpec(
            name="project",
            shape=SHAPE_DOCUMENT,
            layout=ItemDirectoryLayout(part="project", document="index.md"),
            requires_date=True,
            keys={
                "byline": KeySpec("text", max_length=300),
                "difficulty": KeySpec("string"),
            },
            route=lambda path: f"projects/{path}",
        ),
        KindSpec(
            name="curated_link",
            shape=SHAPE_DOCUMENT,
            layout=FlatLayout(part="curated_link"),
            requires_date=True,
            keys={
                "url": KeySpec("url", required=True),
                "category": KeySpec("slug"),
                "published": KeySpec("boolean"),
            },
            route=lambda path: f"curated-links/{path}",
        ),
        KindSpec(
            name="interview_question",
            shape=SHAPE_DOCUMENT,
            layout=FlatLayout(part="interview_question"),
            keys={
                "sections": KeySpec("list"),
            },
            allow_unknown=True,
            route=lambda path: f"interview-questions/{path}",
        ),
        KindSpec(
            name="member_wiki",
            shape=SHAPE_DOCUMENT,
            layout=FlatLayout(part="member_wiki"),
            keys={
                "related": KeySpec("slug_list"),
            },
            route=lambda path: f"topics/{path}",
        ),
    )
    for spec in specs:
        if not is_registered(spec.name):
            register_kind(spec.name, spec)


def register():
    """``check_content --kinds content.kinds`` imports this name if present."""

    register_aisl_kinds()


register_aisl_kinds()
