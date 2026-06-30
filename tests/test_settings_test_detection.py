from django.test import SimpleTestCase

from website import settings as project_settings


class TestCommandDetectionTest(SimpleTestCase):
    def test_detects_django_test_runner(self):
        self.assertTrue(project_settings._is_test_command(['manage.py', 'test']))

    def test_detects_pytest_script_entrypoint(self):
        self.assertTrue(project_settings._is_test_command(['/repo/.venv/bin/pytest', '-q']))

    def test_detects_python_module_pytest_entrypoint(self):
        self.assertTrue(project_settings._is_test_command(['python', '-m', 'pytest', '-q']))

    def test_does_not_treat_runserver_as_tests(self):
        self.assertFalse(project_settings._is_test_command(['manage.py', 'runserver']))
