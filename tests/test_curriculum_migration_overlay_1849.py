"""Keep AISL's local initial curriculum migration and discover package additions."""

import importlib
import sys
import tempfile
from importlib.machinery import PathFinder
from pathlib import Path
from unittest import mock

from community_base.curriculum import migrations as package_migrations
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase

from content import cb_curriculum_migrations as overlay

SITE_MIGRATIONS = Path(overlay.__file__).resolve().parent
PROJECT_TMP = Path(__file__).resolve().parent.parent / ".tmp"
PROBE_NAME = "9999_overlay_probe"


def _resolved_origin(name):
    spec = PathFinder.find_spec(f"{overlay.__name__}.{name}", overlay.__path__)
    if spec is None or spec.origin is None:
        return None
    return Path(spec.origin).resolve()


def _write_probe(directory, predecessor):
    path = directory / f"{PROBE_NAME}.py"
    path.write_text(
        "from django.db import migrations\n\n"
        "class Migration(migrations.Migration):\n"
        f"    dependencies = [('cb_curriculum', '{predecessor}')]\n"
        "    operations = []\n",
        encoding="utf-8",
    )
    (directory / "0001_initial.py").write_text(
        "raise AssertionError('package initial migration selected')\n",
        encoding="utf-8",
    )
    return path


class CurriculumMigrationOverlayTest(SimpleTestCase):
    def test_local_history_precedes_installed_package_migrations(self):
        importlib.reload(overlay)
        paths = [Path(path).resolve() for path in overlay.__path__]
        self.assertEqual(paths[0], SITE_MIGRATIONS)
        for package_path in package_migrations.__path__:
            self.assertEqual(paths.count(Path(package_path).resolve()), 1)

        loader = MigrationLoader(None, ignore_no_migrations=True)
        loader.graph.validate_consistency()
        for name in (
            "0001_initial",
            "0002_alter_unit_kind",
            "0003_unit_body_html_source",
            "0004_module_syllabus_section",
        ):
            self.assertIn(("cb_curriculum", name), loader.disk_migrations)
            self.assertEqual(_resolved_origin(name), SITE_MIGRATIONS / f"{name}.py")
        self.assertIn(
            ("events", "0052_eventseries_visibility"),
            loader.disk_migrations[("cb_curriculum", "0001_initial")].dependencies,
        )
        self.assertEqual(len(loader.graph.leaf_nodes("cb_curriculum")), 1)

    def test_package_only_migration_is_loaded_once_after_reloads(self):
        (leaf,) = MigrationLoader(None, ignore_no_migrations=True).graph.leaf_nodes(
            "cb_curriculum",
        )
        PROJECT_TMP.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=PROJECT_TMP) as temporary:
            package_dir = Path(temporary)
            probe_path = _write_probe(package_dir, leaf[1])
            package_paths = list(package_migrations.__path__)
            package_paths.append(str(package_dir))
            try:
                with mock.patch.object(package_migrations, "__path__", package_paths):
                    importlib.invalidate_caches()
                    importlib.reload(overlay)
                    importlib.reload(overlay)
                    self.assertEqual(list(overlay.__path__).count(str(package_dir)), 1)
                    self.assertEqual(_resolved_origin("0001_initial"), SITE_MIGRATIONS / "0001_initial.py")
                    loader = MigrationLoader(None, ignore_no_migrations=True)
                    key = ("cb_curriculum", PROBE_NAME)
                    self.assertIn(key, loader.disk_migrations)
                    self.assertEqual(loader.graph.leaf_nodes("cb_curriculum"), [key])
                    module = importlib.import_module(f"{overlay.__name__}.{PROBE_NAME}")
                    self.assertEqual(Path(module.__file__).resolve(), probe_path)
            finally:
                sys.modules.pop(f"{overlay.__name__}.{PROBE_NAME}", None)
                importlib.reload(overlay)
                importlib.invalidate_caches()
