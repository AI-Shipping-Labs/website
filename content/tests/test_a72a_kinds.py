"""A7.2a: site kinds are registered and match the conversion key sets."""

from __future__ import annotations

import tempfile
from pathlib import Path

from community_base.content_sync.check import run_check
from community_base.content_sync.kinds import get_kind, is_registered
from django.test import SimpleTestCase

from content.kinds import register_aisl_kinds


class AislKindRegistrationTests(SimpleTestCase):
    def test_the_five_site_kinds_are_registered(self) -> None:
        register_aisl_kinds()
        for name in (
            "workshop",
            "project",
            "curated_link",
            "interview_question",
            "member_wiki",
        ):
            self.assertTrue(is_registered(name), name)
            self.assertEqual(get_kind(name).name, name)

    def test_member_wiki_is_not_the_package_wiki_kind(self) -> None:
        register_aisl_kinds()
        self.assertNotEqual(get_kind("member_wiki").route("hub"), get_kind("wiki").route("hub"))
        self.assertEqual(get_kind("member_wiki").route("intro"), "topics/intro")
        self.assertEqual(get_kind("wiki").route("intro"), "wiki/intro")

    def test_markdown_extensions_are_appended_not_replaced(self) -> None:
        from community_base.content_sync.rendering import markdown_extensions

        extensions = markdown_extensions()
        self.assertEqual(extensions[0], "fenced_code")
        self.assertIn("codehilite", extensions)
        self.assertTrue(
            any("EventWidgetExtension" in str(item) for item in extensions)
            or "content.markdown_extensions.event_widget.EventWidgetExtension" in extensions
        )


class ConvertedFixtureCheckTests(SimpleTestCase):
    def test_check_content_accepts_a_converted_workshop_and_project(self) -> None:
        root = Path(tempfile.mkdtemp()) / "repo"
        (root / "2026/06/vector-search").mkdir(parents=True)
        (root / "2026/06/vector-search" / "workshop.yaml").write_text(
            "content_id: 11111111-1111-1111-1111-111111111111\n"
            "title: Vector Search\n"
            "slug: vector-search\n"
            "date: 2026-06-09\n"
            "byline: Ada Lovelace\n"
            "event_slug: vector-search\n"
        )
        (root / "content.yaml").write_text("schema_version: 1\ncollections:\n  - kind: workshop\n    path: .\n")
        errors = run_check(root, kind_modules=["content.kinds"])
        self.assertEqual(errors, 0)
