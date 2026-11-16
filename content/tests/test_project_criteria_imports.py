"""Regression: the project-criteria modules import cleanly as the first importer."""

import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

MODULES = (
    'content.services.project_criteria',
    'content.services.project_results',
    'content.services.project_phase',
    'content.views.project_criteria_page',
    'content.sync_parsers.families.course_projects',
)

CHILD_SCRIPT = """
import importlib, sys
import django
django.setup()
for name in list(sys.modules):
    if name.startswith(('content.sync_parsers', 'content.services', 'content.views')):
        del sys.modules[name]
importlib.import_module(sys.argv[1])
"""


class ProjectCriteriaFreshImportTest(SimpleTestCase):
    def test_each_module_imports_first_in_a_fresh_interpreter(self):
        env = {**os.environ, 'DEBUG': 'True', 'DJANGO_SETTINGS_MODULE': 'website.settings'}
        for module in MODULES:
            with self.subTest(module=module):
                result = subprocess.run(
                    [sys.executable, '-W', 'ignore', '-c', CHILD_SCRIPT, module],
                    cwd=Path(settings.BASE_DIR),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr[-2000:])
