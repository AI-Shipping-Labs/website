from pathlib import Path

from django.test import SimpleTestCase


class ProductDocPastRecordingsNavigationTest(SimpleTestCase):
    def setUp(self):
        repo_root = Path(__file__).resolve().parents[2]
        self.doc = (repo_root / "_docs" / "product.md").read_text(encoding="utf-8")

    def test_header_navigation_documents_past_recordings_under_community(self):
        self.assertIn(
            "Community dropdown: Membership (`/pricing`), "
            "Activities (`/activities#access-by-tier`), Community Sprints (`/sprints`), "
            "Events (`/events`), Past Recordings (`/events?filter=past`)",
            self.doc,
        )

    def test_resources_navigation_excludes_recordings(self):
        resources_line = next(
            line for line in self.doc.splitlines()
            if line.startswith("- Resources dropdown:")
        )

        for label in [
            "Blog",
            "Courses",
            "Workshops",
            "Learning Paths",
            "Project Ideas",
            "Interview Prep",
            "Curated Links",
        ]:
            self.assertIn(label, resources_line)
        self.assertIn("does not contain Past Recordings or Event Recordings", resources_line)

    def test_past_recordings_surface_and_terminology_are_canonical(self):
        self.assertIn(
            "| Past recordings listing | `/events?filter=past` | Canonical Events surface",
            self.doc,
        )
        self.assertIn(
            "| Past Recording | A completed Event with an available recording, "
            "listed canonically at `/events?filter=past`.",
            self.doc,
        )
