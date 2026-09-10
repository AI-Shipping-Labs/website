"""Native policy tests for the Playwright local-server port resolver."""

import os
from unittest.mock import patch

from django.test import SimpleTestCase

from playwright_tests import conftest

PORT_OVERRIDE = "PLAYWRIGHT_DJANGO_PORT"
FAKE_FREE_PORT = 49152


class ConftestPortResolutionTest(SimpleTestCase):
    def setUp(self):
        super().setUp()
        self.environment = patch.dict(os.environ, {}, clear=False)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        os.environ.pop(PORT_OVERRIDE, None)

        self.free_port_patch = patch.object(
            conftest,
            "_pick_free_port",
            return_value=FAKE_FREE_PORT,
        )
        self.pick_free_port = self.free_port_patch.start()
        self.addCleanup(self.free_port_patch.stop)

        conftest._LOCAL_PORT = None
        self.addCleanup(setattr, conftest, "_LOCAL_PORT", None)

    def test_zero_falls_back_to_free_port(self):
        """``PLAYWRIGHT_DJANGO_PORT=0`` must auto-pick, never bind literal 0."""
        os.environ[PORT_OVERRIDE] = "0"

        self.assertEqual(conftest._resolved_local_port(), FAKE_FREE_PORT)
        self.pick_free_port.assert_called_once_with()

    def test_invalid_or_nonpositive_falls_back_to_free_port(self):
        """Negative, out-of-range, whitespace, and garbage overrides auto-pick."""
        for raw in ("-1", "-5000", "abc", "8000x", "  ", "70000"):
            with self.subTest(raw=raw):
                conftest._LOCAL_PORT = None
                self.pick_free_port.reset_mock()
                os.environ[PORT_OVERRIDE] = raw

                self.assertEqual(conftest._resolved_local_port(), FAKE_FREE_PORT)
                self.pick_free_port.assert_called_once_with()

    def test_unset_falls_back_to_free_port(self):
        """No override at all picks a free ephemeral port."""
        self.assertNotIn(PORT_OVERRIDE, os.environ)

        self.assertEqual(conftest._resolved_local_port(), FAKE_FREE_PORT)
        self.pick_free_port.assert_called_once_with()

    def test_valid_positive_port_is_honored_verbatim(self):
        """A valid positive override is used exactly as given."""
        os.environ[PORT_OVERRIDE] = "8123"

        self.assertEqual(conftest._resolved_local_port(), 8123)
        self.pick_free_port.assert_not_called()

    def test_resolved_port_is_memoized(self):
        """Once resolved, the same port is returned even if the env var changes."""
        os.environ[PORT_OVERRIDE] = "8123"
        first = conftest._resolved_local_port()
        os.environ[PORT_OVERRIDE] = "9999"

        self.assertEqual(first, 8123)
        self.assertEqual(conftest._resolved_local_port(), first)
        self.pick_free_port.assert_not_called()
