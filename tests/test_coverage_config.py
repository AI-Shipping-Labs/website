import tomllib
from pathlib import Path

from django.apps import apps
from django.test import SimpleTestCase

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _coverage_config():
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    return pyproject["tool"]["coverage"]


def _is_first_party_app(app_config):
    app_path = Path(app_config.path).resolve()

    try:
        relative_app_path = app_path.relative_to(PROJECT_ROOT)
    except ValueError:
        return False

    return len(relative_app_path.parts) == 1 and (app_path / "tests").is_dir()


class CoverageConfigTest(SimpleTestCase):
    def test_coverage_source_includes_tested_first_party_apps(self):
        coverage_sources = set(_coverage_config()["run"]["source"])
        tested_first_party_apps = sorted(
            app_config.name
            for app_config in apps.get_app_configs()
            if _is_first_party_app(app_config)
        )

        missing_apps = [
            app_name
            for app_name in tested_first_party_apps
            if app_name not in coverage_sources
        ]

        self.assertEqual(
            [],
            missing_apps,
            "Add these tested first-party production apps to "
            f"[tool.coverage.run].source: {', '.join(missing_apps)}",
        )

    def test_coverage_omit_policy_keeps_non_runtime_artifacts_out(self):
        coverage_omits = set(_coverage_config()["run"]["omit"])
        expected_omits = {
            "*/migrations/*",
            "*/tests/*",
            "playwright_tests/*",
            "tests/*",
            ".venv/*",
            "htmlcov/*",
            "build/*",
            "dist/*",
            ".cache/*",
            ".pytest_cache/*",
            ".ruff_cache/*",
        }

        self.assertEqual(set(), expected_omits - coverage_omits)

    def test_coverage_threshold_remains_85_percent(self):
        self.assertEqual(85, _coverage_config()["report"]["fail_under"])
