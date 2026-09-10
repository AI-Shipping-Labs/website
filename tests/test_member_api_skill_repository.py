"""Repository contract for the member API operator skill."""

from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIRECTORY = ROOT / "skills" / "ai-shipping-labs-member-api"


class MemberApiSkillRepositoryTest(SimpleTestCase):
    def test_member_api_skill_directory_contains_required_docs(self):
        self.assertTrue(SKILL_DIRECTORY.joinpath("README.md").is_file())
        self.assertTrue(SKILL_DIRECTORY.joinpath("SKILL.md").is_file())
