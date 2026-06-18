from django.test import SimpleTestCase

from scripts.import_sprint_plan_markdown import parse_resources


class SprintPlanMarkdownResourceImportTest(SimpleTestCase):
    def test_parse_resources_builds_structured_rows(self):
        body = "\n".join([
            "- [Buildcamp](https://buildcamp.example.com)",
            "- Deployment - [Docs](https://docs.example.com/deploy)",
            "- Logfire - note with [Docs](https://logfire.pydantic.dev/)",
            "- amr_ai repo - https://github.com/juanpprim/amr_ai",
            "- Carlos's own project notes",
        ])

        resources = parse_resources(body)

        self.assertEqual(
            resources,
            [
                {
                    "title": "Buildcamp",
                    "url": "https://buildcamp.example.com",
                    "note": "",
                    "position": 0,
                },
                {
                    "title": "Deployment",
                    "url": "https://docs.example.com/deploy",
                    "note": "",
                    "position": 1,
                },
                {
                    "title": "Logfire",
                    "url": "https://logfire.pydantic.dev/",
                    "note": "note with [Docs](https://logfire.pydantic.dev/)",
                    "position": 2,
                },
                {
                    "title": "amr_ai repo",
                    "url": "https://github.com/juanpprim/amr_ai",
                    "note": "",
                    "position": 3,
                },
                {
                    "title": "Carlos's own project notes",
                    "url": "",
                    "note": "",
                    "position": 4,
                },
            ],
        )

    def test_parse_resources_keeps_multiline_note_text(self):
        resources = parse_resources(
            "- Deployment - note line one\n"
            "  continued note with [Docs](https://docs.example.com/deploy)"
        )

        self.assertEqual(resources[0]["title"], "Deployment")
        self.assertEqual(resources[0]["url"], "https://docs.example.com/deploy")
        self.assertIn("continued note", resources[0]["note"])
