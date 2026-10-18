import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from scripts.affected_tests import CHECK_TAILWIND_COMMAND, build_plan
from scripts.tailwind_producers import (
    ProducerDiscoveryError,
    producer_javascript_files,
    producer_python_files,
)
from scripts.verify_tailwind_build import _producer_classes, verify_bundle


class TailwindProducerDiscoveryTest(SimpleTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        subprocess.run(["git", "init", "--quiet"], cwd=self.root, check=True)
        self._write(".gitignore", ".tmp/\n")
        self._write("sample/forms/member.py", 'CLASSES = "bg-tracked-1987"\n')
        self._write("sample/widgets/new.py", 'CLASSES = "text-untracked-1987"\n')
        self._write("studio/views/example.py", 'CLASSES = "ring-2"\n')
        self._write("static/js/state.js", 'const state = "hidden";\n')
        self._write("static/js/vendor/library.js", 'const state = "block";\n')
        self._write(".tmp/uv-cache/django/forms/boundfield.py", 'TEXT = "space-separated transforming"\n')
        subprocess.run(
            ["git", "add", ".gitignore", "sample/forms/member.py", "studio/views/example.py", "static/js"],
            cwd=self.root,
            check=True,
        )

    def _write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def test_discovery_keeps_owned_producers_and_excludes_ignored_cache(self):
        python_files = set(producer_python_files(self.root))
        javascript_files = set(producer_javascript_files(self.root))

        self.assertIn(self.root / "sample/forms/member.py", python_files)
        self.assertIn(self.root / "sample/widgets/new.py", python_files)
        self.assertIn(self.root / "studio/views/example.py", python_files)
        self.assertNotIn(self.root / ".tmp/uv-cache/django/forms/boundfield.py", python_files)
        self.assertIn(self.root / "static/js/state.js", javascript_files)
        self.assertNotIn(self.root / "static/js/vendor/library.js", javascript_files)
        # Synthetic tool inputs exercise ignored-cache discovery, not UI copy.
        ignored_cache_markers = {"space-separated", "transforming"}
        self.assertTrue(ignored_cache_markers.isdisjoint(_producer_classes(self.root)))

    def test_missing_first_party_selector_still_fails_bundle_verification(self):
        css_path = self._write("bundle.css", "a" * 20_001)

        # This synthetic marker identifies the verifier failure under test.
        missing_selector_marker = "bg-tracked-1987"
        with self.assertRaises(AssertionError) as raised:
            verify_bundle(self.root, css_path)
        self.assertIn(missing_selector_marker, str(raised.exception))

    def test_git_discovery_failure_is_clear_and_fail_closed(self):
        failure = subprocess.CalledProcessError(128, ["git", "ls-files"])

        with patch("scripts.tailwind_producers.subprocess.run", side_effect=failure):
            with self.assertRaisesRegex(ProducerDiscoveryError, "cannot discover repository-owned sources"):
                producer_python_files(self.root)

    def test_untracked_producer_and_discovery_owner_select_tailwind_gate(self):
        for relative in ("sample/widgets/new.py", "scripts/tailwind_producers.py"):
            plan = build_plan([relative], expander=lambda modules: {})
            self.assertIn(CHECK_TAILWIND_COMMAND, plan.extra_commands)
