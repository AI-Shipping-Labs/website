from pathlib import Path

from django.test import SimpleTestCase


class ProductDocNavigationTest(SimpleTestCase):
    def setUp(self):
        repo_root = Path(__file__).resolve().parents[2]
        self.doc = (repo_root / "_docs" / "product.md").read_text(encoding="utf-8")

    def test_header_documents_membership_as_top_level_and_no_activities_item(self):
        self.assertIn(
            "Primary navigation (desktop): Community, Learning, Blog, Membership, About",
            self.doc,
        )
        community_line = next(
            line for line in self.doc.splitlines()
            if line.startswith("- Community dropdown:")
        )
        self.assertIn("Events (`/events`)", community_line)
        self.assertIn("Community Sprints (`/sprints`)", community_line)
        self.assertIn("Book Club (`/books`)", community_line)
        self.assertIn("Activities is not a separate navigation destination", community_line)

    def test_learning_navigation_documents_current_destinations(self):
        learning_line = next(
            line for line in self.doc.splitlines()
            if line.startswith("- Learning dropdown:")
        )

        for label in [
            "Courses",
            "Workshops",
            "Learning Paths",
            "Interview Prep",
            "Downloads",
        ]:
            self.assertIn(label, learning_line)

    def test_past_events_surface_and_recording_terminology_are_canonical(self):
        self.assertIn(
            "The Past events history includes every public published finished "
            "event and highlights recordings when available.",
            self.doc,
        )
        self.assertIn(
            "| Past events listing | `/events?filter=past` | Canonical Events surface "
            "for every public published finished event",
            self.doc,
        )
        self.assertIn(
            "| Past Recording | A recording available for a completed Event and "
            "highlighted within the full finished-event history at "
            "`/events?filter=past`.",
            self.doc,
        )
        self.assertIn(
            "after the event ends, it becomes discoverable in the full history "
            "at `/events?filter=past`, with a recording highlighted when available",
            self.doc,
        )


class ProductDocMembershipTest(SimpleTestCase):
    def setUp(self):
        repo_root = Path(__file__).resolve().parents[2]
        self.doc = (repo_root / "_docs" / "product.md").read_text(encoding="utf-8")

    def test_membership_page_documents_merged_journey(self):
        membership_row = next(
            line
            for line in self.doc.splitlines()
            if line.startswith("| Membership page | `/membership` |")
        )

        self.assertIn("all four plans and billing controls first", membership_row)
        self.assertIn("extended paid-tier benefits", membership_row)
        self.assertIn("one active sprint", membership_row)
        self.assertIn("one upcoming event", membership_row)
        self.assertIn("canonical shared cards", membership_row)

    def test_tiers_yaml_single_source_contract_is_documented(self):
        self.assertIn(
            "Both the compact Membership plan bullets and the detailed benefit "
            "rows read the same `benefits` records.",
            self.doc,
        )
        self.assertIn(
            "A benefit with an empty `description` stays in its plan card and "
            "is intentionally omitted from the detailed explanation list.",
            self.doc,
        )
