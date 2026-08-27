from django.test import SimpleTestCase

from website import settings as project_settings


class TestCommandDetectionTest(SimpleTestCase):
    def test_detects_django_test_runner(self):
        self.assertTrue(
            project_settings._is_test_command(['manage.py', 'test'], environ={})
        )

    def test_detects_pytest_script_entrypoint(self):
        self.assertTrue(
            project_settings._is_test_command(['/repo/.venv/bin/pytest', '-q'], environ={})
        )

    def test_detects_python_module_pytest_entrypoint(self):
        self.assertTrue(
            project_settings._is_test_command(['python', '-m', 'pytest', '-q'], environ={})
        )

    def test_does_not_treat_runserver_as_tests(self):
        self.assertFalse(
            project_settings._is_test_command(['manage.py', 'runserver'], environ={})
        )


class XdistWorkerDetectionTest(SimpleTestCase):
    """pytest-xdist workers must report TESTING=True (issue #1470).

    execnet spawns each worker with ``sys.argv == ['-c']``, so argv sniffing
    alone reported False in exactly the processes that run the tests. That
    silently enabled real S3 calls, Logfire, and WAL journaling inside every
    worker of a ``pytest -n N`` run.
    """

    def test_xdist_worker_argv_is_detected_via_the_worker_env_var(self):
        self.assertTrue(
            project_settings._is_test_command(
                ['-c'], environ={'PYTEST_XDIST_WORKER': 'gw0'}
            )
        )

    def test_xdist_worker_argv_alone_is_not_enough(self):
        """Guards the regression: without the env var, ``['-c']`` reads False."""
        self.assertFalse(project_settings._is_test_command(['-c'], environ={}))

    def test_blank_worker_env_var_is_ignored(self):
        self.assertFalse(
            project_settings._is_test_command(
                ['manage.py', 'runserver'], environ={'PYTEST_XDIST_WORKER': '  '}
            )
        )

    def test_worker_env_var_does_not_mislabel_a_production_process(self):
        """Only a set, non-blank worker id flips the answer."""
        self.assertFalse(
            project_settings._is_test_command(['gunicorn', 'website.wsgi'], environ={})
        )
