"""Deterministic self-tests for explicit Playwright journey ownership."""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import types
from pathlib import Path

from django.test import SimpleTestCase

import scripts.browser_journey_policy as policy_module
from scripts.browser_journey_policy import (
    BrowserJourneyPlugin,
    browser_journey,
    is_browser_journey,
)

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT / ".tmp" / "browser-journey-policy-tests"


class BrowserJourneyIdentityTests(SimpleTestCase):
    def test_process_authority_exposes_no_config_record_or_public_verifier(self):
        self.assertFalse(hasattr(policy_module, "_CanonicalInstallationRecord"))
        self.assertFalse(hasattr(policy_module, "_installation_records"))
        self.assertFalse(hasattr(policy_module, "assert_browser_journey_policy_integrity"))
        self.assertIsNone(policy_module.register_browser_journey_policy.__closure__)

    def test_public_policy_constructor_cannot_accept_remote_authority(self):
        with self.assertRaises(TypeError):
            BrowserJourneyPlugin(remote_selection_reason="forged")

    def test_decorator_returns_same_callable_and_only_final_identity_owns_declaration(self):
        def plain():
            return None

        self.assertIs(browser_journey(plain), plain)
        self.assertTrue(is_browser_journey(plain))

        @functools.wraps(plain)
        def copied_wrapper():
            return plain()

        self.assertFalse(is_browser_journey(copied_wrapper))

        copied_dict = lambda: None
        copied_dict.__dict__.update(plain.__dict__)
        self.assertFalse(is_browser_journey(copied_dict))

        arbitrary_func = lambda: None
        arbitrary_func.__func__ = plain
        self.assertFalse(is_browser_journey(arbitrary_func))

        forged_method = types.MethodType(plain, object())
        self.assertFalse(is_browser_journey(forged_method))

        class RealOwner:
            method = plain

        self.assertFalse(is_browser_journey(RealOwner().method))

    def test_decorator_order_requires_the_final_wrapper_to_opt_in(self):
        def wrapping(decorated):
            @functools.wraps(decorated)
            def wrapper():
                return decorated()

            return wrapper

        @wrapping
        @browser_journey
        def replaced_after_declaration():
            return None

        @browser_journey
        @wrapping
        def final_wrapper_declared():
            return None

        self.assertFalse(is_browser_journey(replaced_after_declaration))
        self.assertTrue(is_browser_journey(final_wrapper_declared))


class BrowserJourneyRuntimeTests(SimpleTestCase):
    maxDiff = None

    def setUp(self):
        super().setUp()
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="case-", dir=SCRATCH))
        (self.root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        (self.root / "conftest.py").write_text(
            textwrap.dedent(
                """
                import pytest

                from scripts.browser_journey_policy import BrowserJourneyPlugin


                class RequestClient:
                    def get(self, url):
                        return url


                class ExactPage:
                    def __init__(self):
                        self.request = RequestClient()

                    def goto(self, url):
                        if url == "fail":
                            raise RuntimeError("navigation failed")
                        return url

                    def set_content(self, content):
                        if content == "fail":
                            raise RuntimeError("content failed")
                        return content


                def pytest_configure(config):
                    config.pluginmanager.register(
                        BrowserJourneyPlugin(
                            page_type=ExactPage,
                            enforce_collection=False,
                        ),
                        "synthetic-browser-journey-policy",
                    )


                @pytest.fixture
                def page():
                    return ExactPage()
                """
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        shutil.rmtree(self.root)
        super().tearDown()

    def run_pytest(self, source: str) -> subprocess.CompletedProcess[str]:
        (self.root / "test_policy.py").write_text(textwrap.dedent(source), encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join([str(ROOT), *(filter(None, [env.get("PYTHONPATH")]))])
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "test_policy.py"],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_supported_direct_helper_retry_setup_and_set_content_paths_pass(self):
        result = self.run_pytest(
            """
            import pytest

            from scripts.browser_journey_policy import browser_journey


            def helper(page):
                return page.goto("helper")


            def goto_with_retry(page, url):
                return page.goto(url)


            @pytest.fixture
            def navigated_page(page):
                page.goto("setup")
                return page


            @browser_journey
            def test_direct(page):
                page.goto("direct")


            @browser_journey
            def test_helper(page):
                helper(page)


            @browser_journey
            def test_retry_helper(page):
                goto_with_retry(page, "retry")


            @browser_journey
            def test_function_setup_fixture(navigated_page):
                assert navigated_page is not None


            @browser_journey
            def test_set_content(page):
                page.set_content("<main>ready</main>")
            """
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("5 passed", result.stdout)

    def test_unsupported_paths_fail_with_exact_item_local_nodeids(self):
        result = self.run_pytest(
            """
            import pytest

            from scripts.browser_journey_policy import browser_journey


            class FakeWorkflow:
                def goto(self, url):
                    return url

                def set_content(self, content):
                    return content


            @pytest.fixture
            def teardown_navigation(page):
                yield page
                page.goto("too-late")


            @browser_journey
            def test_fake_collision():
                FakeWorkflow().goto("fake")
                FakeWorkflow().set_content("fake")


            @browser_journey
            def test_request_only(page):
                page.request.get("request")


            @browser_journey
            def test_unreachable(page):
                if False:
                    page.goto("unreachable")


            @browser_journey
            def test_teardown_only(teardown_navigation):
                assert teardown_navigation is not None


            @browser_journey
            def test_failed_navigation(page):
                with pytest.raises(RuntimeError):
                    page.goto("fail")


            @browser_journey
            def test_cross_item_source(page):
                page.goto("belongs-here")


            @browser_journey
            def test_cross_item_target():
                return None


            @pytest.mark.parametrize("navigate", [True, False], ids=["navigates", "no-journey"])
            @browser_journey
            def test_each_parameter(page, navigate):
                if navigate:
                    page.goto("parameter")
            """
        )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        for nodeid in (
            "test_policy.py::test_fake_collision",
            "test_policy.py::test_request_only",
            "test_policy.py::test_unreachable",
            "test_policy.py::test_teardown_only",
            "test_policy.py::test_failed_navigation",
            "test_policy.py::test_cross_item_target",
            "test_policy.py::test_each_parameter[no-journey]",
        ):
            self.assertIn(nodeid, output)
        self.assertNotIn("test_policy.py::test_cross_item_source FAILED", output)
        self.assertNotIn("test_policy.py::test_each_parameter[navigates] FAILED", output)
        self.assertIn("Page.goto or Page.set_content", output)
        self.assertIn("7 failed, 2 passed", output)

    def test_runtime_skip_and_xfail_cannot_bypass_required_execution(self):
        result = self.run_pytest(
            """
            import pytest

            from scripts.browser_journey_policy import browser_journey


            @pytest.fixture
            def skipped_setup():
                pytest.skip("setup tried to escape")


            @pytest.fixture
            def xfailed_teardown(page):
                yield page
                pytest.xfail("teardown tried to escape")


            @pytest.fixture
            def dynamic_xfail_marker(request):
                request.node.add_marker(pytest.mark.xfail(reason="late marker tried to escape"))


            @browser_journey
            def test_runtime_skip():
                pytest.skip("body tried to escape")


            @browser_journey
            def test_runtime_xfail():
                pytest.xfail("body tried to escape")


            @browser_journey
            def test_setup_fixture_skip(skipped_setup):
                return None


            @browser_journey
            def test_teardown_fixture_xfail(xfailed_teardown):
                xfailed_teardown.goto("call-proved-before-teardown")


            @browser_journey
            def test_dynamic_xpass(page, dynamic_xfail_marker):
                page.goto("proof-cannot-make-a-late-xfail-acceptable")
            """
        )

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        for nodeid in (
            "test_policy.py::test_runtime_skip",
            "test_policy.py::test_runtime_xfail",
            "test_policy.py::test_setup_fixture_skip",
            "test_policy.py::test_teardown_fixture_xfail",
            "test_policy.py::test_dynamic_xpass",
        ):
            self.assertIn(nodeid, output)
        self.assertIn("attempted a runtime skip", output)
        self.assertIn("attempted a runtime xfail", output)
        self.assertIn("runtime skip and xfail outcomes are not accepted", output)


class BrowserJourneyCollectionTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="collection-", dir=SCRATCH))
        (self.root / "pytest.ini").write_text(
            "[pytest]\nmarkers =\n    manual_visual\n    slow_platform\n    local_only\n    creates_data\n",
            encoding="utf-8",
        )
        (self.root / "tests").mkdir()
        (self.root / "tests" / "playwright_owner_inventory_live.json").write_text(
            '{"LEGACY_DECLARED_BROWSER": [], "LEGACY_NON_BROWSER": {}}\n',
            encoding="utf-8",
        )
        (self.root / "playwright_tests").mkdir()
        (self.root / "playwright_tests" / "__init__.py").write_text("", encoding="utf-8")
        (self.root / "playwright_tests" / "conftest.py").write_text(
            textwrap.dedent(
                """
                import pytest

                from scripts.browser_journey_policy import register_browser_journey_policy


                class ExactPage:
                    def goto(self, url):
                        return url

                    def set_content(self, content):
                        return content


                def pytest_configure(config):
                    register_browser_journey_policy(config, page_type=ExactPage)


                @pytest.hookimpl(hookwrapper=True, tryfirst=True)
                def pytest_runtest_makereport(item, call):
                    yield
                    import sys as runtime_sys

                    authority_dispatch = runtime_sys.audit
                    if not (
                        authority_dispatch.__class__
                        is [].append.__class__
                        and authority_dispatch.__self__ is runtime_sys
                        and authority_dispatch.__module__ == "sys"
                        and authority_dispatch.__name__ == "audit"
                    ):
                        raise pytest.UsageError(
                            "Playwright browser journey policy integrity failed: "
                            "the process authority dispatch identity changed."
                        )
                    authority_dispatch(
                        "asl.browser_journey_policy.verify.v1",
                        item.config,
                    )


                @pytest.fixture
                def page():
                    return ExactPage()
                """
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        shutil.rmtree(self.root)
        super().tearDown()

    def collect(
        self,
        source: str,
        *,
        root_conftest: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        (self.root / "playwright_tests" / "test_contract.py").write_text(
            textwrap.dedent(source),
            encoding="utf-8",
        )
        root_conftest_path = self.root / "conftest.py"
        if root_conftest is None:
            root_conftest_path.unlink(missing_ok=True)
        else:
            root_conftest_path.write_text(
                textwrap.dedent(root_conftest),
                encoding="utf-8",
            )
        env = os.environ.copy()
        env.pop("PLAYWRIGHT_BASE_URL", None)
        env["PYTHONPATH"] = os.pathsep.join([str(ROOT), *(filter(None, [env.get("PYTHONPATH")]))])
        return subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", "playwright_tests"],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def execute(
        self,
        source: str,
        *,
        root_conftest: str | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        (self.root / "playwright_tests" / "test_contract.py").write_text(
            textwrap.dedent(source),
            encoding="utf-8",
        )
        root_conftest_path = self.root / "conftest.py"
        if root_conftest is None:
            root_conftest_path.unlink(missing_ok=True)
        else:
            root_conftest_path.write_text(textwrap.dedent(root_conftest), encoding="utf-8")
        env = os.environ.copy()
        env.pop("PLAYWRIGHT_BASE_URL", None)
        env["PYTHONPATH"] = os.pathsep.join([str(ROOT), *(filter(None, [env.get("PYTHONPATH")]))])
        env.update(extra_env or {})
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "playwright_tests"],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_exact_final_declaration_is_the_only_new_owner_growth_path(self):
        accepted = self.collect(
            """
            from scripts.browser_journey_policy import browser_journey


            @browser_journey
            def test_declared_new_owner():
                return None
            """
        )
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)

        rejected = self.collect(
            """
            def test_undeclared_new_owner():
                return None
            """
        )
        output = rejected.stdout + rejected.stderr
        self.assertNotEqual(rejected.returncode, 0, output)
        self.assertIn(
            "new selected owner `playwright_tests/test_contract.py::test_undeclared_new_owner`",
            output,
        )
        self.assertIn("exact final callable", output)

    def test_canonical_instance_and_class_methods_declare_in_exact_owner_context(self):
        result = self.execute(
            """
            from scripts.browser_journey_policy import browser_journey


            class TestCanonicalOwners:
                @browser_journey
                def test_instance_method(self, page):
                    page.goto("canonical-instance")

                @classmethod
                @browser_journey
                def test_class_method(cls, page):
                    page.goto("canonical-classmethod")
            """
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("2 passed", result.stdout)

    def test_bound_donor_descriptor_rebinding_and_inheritance_never_transfer_owner(self):
        cases = {
            "module-carrier": """
                import types

                from scripts.browser_journey_policy import browser_journey


                @browser_journey
                def declared_donor(self, page):
                    page.goto("donor")


                class Carrier:
                    descriptor_alias = declared_donor


                test_forged_method_owner = types.MethodType(declared_donor, Carrier())
            """,
            "class-rebind": """
                from scripts.browser_journey_policy import browser_journey


                @browser_journey
                def declared_donor(self, page):
                    page.goto("donor")


                class TestReboundOwner:
                    test_rebound_owner = declared_donor
            """,
            "metadata-rebind": """
                from scripts.browser_journey_policy import browser_journey


                @browser_journey
                def declared_donor(self, page):
                    page.goto("donor")


                declared_donor.__name__ = "test_rebound_owner"
                declared_donor.__qualname__ = (
                    "TestMetadataReboundOwner.test_rebound_owner"
                )


                class TestMetadataReboundOwner:
                    test_rebound_owner = declared_donor
            """,
            "inherited": """
                from scripts.browser_journey_policy import browser_journey


                class DonorBase:
                    @browser_journey
                    def test_inherited_owner(self, page):
                        page.goto("base")


                class TestInheritedOwner(DonorBase):
                    pass
            """,
            "same-class-alias": """
                from scripts.browser_journey_policy import browser_journey


                class TestAliasedOwner:
                    @browser_journey
                    def test_original_owner(self, page):
                        page.goto("original")

                    test_alias_owner = test_original_owner
            """,
        }

        for name, source in cases.items():
            with self.subTest(name=name):
                result = self.execute(source)
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 4, output)
                self.assertTrue(
                    "not tied to its exact module/class owner context" in output
                    or "exact final callable is not decorated with @browser_journey" in output,
                    output,
                )

    def test_post_yield_collection_replacement_is_validated_after_all_hooks(self):
        rejected = self.collect(
            """
            from scripts.browser_journey_policy import browser_journey


            @browser_journey
            def test_replaced_plain():
                return None


            @browser_journey
            def test_replaced_by_copied_wrapper():
                return None
            """,
            root_conftest="""
            import functools
            import pytest


            def plain_replacement():
                return None


            @pytest.hookimpl(hookwrapper=True, tryfirst=True)
            def pytest_collection_modifyitems(items):
                yield
                items[0]._obj = plain_replacement
                original = items[1].obj

                @functools.wraps(original)
                def copied_wrapper():
                    return original()

                items[1]._obj = copied_wrapper
            """,
        )

        output = rejected.stdout + rejected.stderr
        self.assertNotEqual(rejected.returncode, 0, output)
        self.assertIn(
            "new selected owner `playwright_tests/test_contract.py::test_replaced_plain`",
            output,
        )
        self.assertIn(
            "new selected owner `playwright_tests/test_contract.py::test_replaced_by_copied_wrapper`",
            output,
        )
        self.assertIn("exact final callable", output)

    def test_post_yield_final_replacement_may_explicitly_declare_its_own_identity(self):
        accepted = self.collect(
            """
            from scripts.browser_journey_policy import browser_journey


            @browser_journey
            def test_replaced_with_explicit_final_declaration():
                return None
            """,
            root_conftest="""
            import pytest

            from scripts.browser_journey_policy import browser_journey


            def final_replacement():
                return None


            @pytest.hookimpl(hookwrapper=True, tryfirst=True)
            def pytest_collection_modifyitems(items):
                yield
                items[0]._obj = browser_journey(final_replacement)
            """,
        )

        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)

    def test_post_collection_identity_drift_is_enforced_during_real_execution(self):
        result = self.execute(
            """
            from scripts.browser_journey_policy import browser_journey


            @browser_journey
            def test_plain_replacement(page):
                page.goto("original-plain")


            @browser_journey
            def test_copied_replacement(page):
                page.goto("original-copied")


            @browser_journey
            def test_explicit_final_replacement(page):
                page.goto("original-explicit")
            """,
            root_conftest="""
            import functools
            import pytest

            from scripts.browser_journey_policy import browser_journey


            def plain_replacement(page):
                page.goto("replacement-plain")


            @pytest.hookimpl(hookwrapper=True, tryfirst=True)
            def pytest_collection_finish(session):
                yield
                by_name = {item.name: item for item in session.items}
                copied_original = by_name["test_copied_replacement"].obj

                @functools.wraps(copied_original)
                def copied_replacement(page):
                    page.goto("replacement-copied")

                def explicit_replacement(page):
                    page.goto("replacement-explicit")

                by_name["test_plain_replacement"]._obj = plain_replacement
                by_name["test_copied_replacement"]._obj = copied_replacement
                by_name["test_explicit_final_replacement"]._obj = browser_journey(
                    explicit_replacement
                )
            """,
        )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("test_plain_replacement - Declared", output)
        self.assertIn("test_copied_replacement - Declared", output)
        self.assertNotIn("test_explicit_final_replacement - Declared", output)
        self.assertIn("1 passed", output)
        self.assertIn("4 errors", output)
        self.assertIn("changed callable identity after completed collection", output)
        self.assertIn("copied metadata is not accepted", output)

    def test_arbitrary_func_attribute_cannot_transfer_declaration(self):
        result = self.execute(
            """
            import pytest

            from scripts.browser_journey_policy import browser_journey


            @browser_journey
            def declared_donor(page):
                page.goto("donor")


            @pytest.fixture(autouse=True)
            def setup_navigation(page):
                page.goto("setup-proof-cannot-authorize-owner")


            def test_spoofed_owner(page):
                page.goto("body-proof-cannot-authorize-owner")


            test_spoofed_owner.__func__ = declared_donor
            """
        )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 4, output)
        self.assertIn(
            "new selected owner `playwright_tests/test_contract.py::test_spoofed_owner`",
            output,
        )
        self.assertIn("exact final callable", output)

    def test_transient_call_replacements_fail_at_actual_invocation_boundary(self):
        result = self.execute(
            """
            from scripts.browser_journey_policy import browser_journey


            @browser_journey
            def test_transient_plain(page):
                page.goto("original-plain")


            @browser_journey
            def test_transient_copied(page):
                page.goto("original-copied")


            @browser_journey
            def test_transient_pyfunc_plain(page):
                page.goto("original-pyfunc")
            """,
            root_conftest="""
            import functools
            import pytest


            def plain_replacement(page):
                page.goto("replacement-plain")
                raise AssertionError("undecorated transient replacement executed")


            @pytest.hookimpl(hookwrapper=True, tryfirst=True)
            def pytest_runtest_call(item):
                original = item.obj
                if item.name == "test_transient_plain":
                    item._obj = plain_replacement
                elif item.name == "test_transient_copied":
                    @functools.wraps(original)
                    def copied_replacement(page):
                        page.goto("replacement-copied")
                        raise AssertionError("copied transient replacement executed")

                    item._obj = copied_replacement
                try:
                    yield
                finally:
                    item._obj = original


            @pytest.hookimpl(hookwrapper=True, trylast=True)
            def pytest_pyfunc_call(pyfuncitem):
                original = pyfuncitem.obj
                if pyfuncitem.name == "test_transient_pyfunc_plain":
                    pyfuncitem._obj = plain_replacement
                try:
                    yield
                finally:
                    pyfuncitem._obj = original
            """,
        )

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        for nodeid in (
            "playwright_tests/test_contract.py::test_transient_plain",
            "playwright_tests/test_contract.py::test_transient_copied",
            "playwright_tests/test_contract.py::test_transient_pyfunc_plain",
        ):
            self.assertIn(nodeid, output)
        self.assertIn("changed callable identity after completed collection", output)
        self.assertIn("arbitrary __func__ or copied metadata is not accepted", output)
        self.assertNotIn("transient replacement executed", output)

    def test_persistent_and_transient_nodeid_drift_cannot_disable_runtime_proof(self):
        result = self.execute(
            """
            from scripts.browser_journey_policy import browser_journey


            @browser_journey
            def test_persistent_nodeid_drift():
                return None


            @browser_journey
            def test_transient_nodeid_drift(page):
                page.goto("drift-must-fail-before-proof")


            @browser_journey
            def test_pyfunc_nodeid_drift(page):
                page.goto("pyfunc-drift-must-fail-before-proof")
            """,
            root_conftest="""
            import pytest


            @pytest.hookimpl(hookwrapper=True, tryfirst=True)
            def pytest_collection_finish(session):
                yield
                by_name = {item.name: item for item in session.items}
                by_name["test_persistent_nodeid_drift"]._nodeid = "spoofed::persistent"


            @pytest.hookimpl(hookwrapper=True, tryfirst=True)
            def pytest_runtest_call(item):
                original_nodeid = item.nodeid
                if item.name == "test_transient_nodeid_drift":
                    item._nodeid = "spoofed::transient"
                try:
                    yield
                finally:
                    item._nodeid = original_nodeid


            @pytest.hookimpl(hookwrapper=True, trylast=True)
            def pytest_pyfunc_call(pyfuncitem):
                original_nodeid = pyfuncitem.nodeid
                if pyfuncitem.name == "test_pyfunc_nodeid_drift":
                    pyfuncitem._nodeid = "spoofed::pyfunc"
                try:
                    yield
                finally:
                    pyfuncitem._nodeid = original_nodeid
            """,
        )

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        for original, spoofed in (
            (
                "playwright_tests/test_contract.py::test_persistent_nodeid_drift",
                "spoofed::persistent",
            ),
            (
                "playwright_tests/test_contract.py::test_transient_nodeid_drift",
                "spoofed::transient",
            ),
            (
                "playwright_tests/test_contract.py::test_pyfunc_nodeid_drift",
                "spoofed::pyfunc",
            ),
        ):
            self.assertIn(original, output)
            self.assertIn(spoofed, output)
        self.assertIn("policy state is bound to the stable pytest item identity", output)

    def test_inactive_conditional_markers_execute_and_prove_the_local_lane(self):
        result = self.execute(
            """
            import pytest

            from scripts.browser_journey_policy import browser_journey


            @pytest.mark.skipif(False, reason="condition is inactive")
            @browser_journey
            def test_inactive_skipif(page):
                page.goto("skipif-false")


            @pytest.mark.xfail(False, reason="condition is inactive")
            @browser_journey
            def test_inactive_xfail(page):
                page.goto("xfail-false")


            @pytest.mark.local_only
            @browser_journey
            def test_required_local_lane(page):
                page.goto("required-local")
            """
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("3 passed", result.stdout)

    def test_canonical_remote_selection_skip_is_allowed_only_off_local_lane(self):
        source = """
            import pytest

            from scripts.browser_journey_policy import browser_journey


            @pytest.mark.local_only
            @browser_journey
            def test_local_database_journey(page):
                page.goto("required-when-local")


            @pytest.mark.creates_data
            @browser_journey
            def test_creates_data_journey(page):
                page.goto("required-when-local-too")
        """
        local = self.execute(source)
        self.assertEqual(local.returncode, 0, local.stdout + local.stderr)
        self.assertIn("2 passed", local.stdout)

        remote = self.execute(
            source,
            extra_env={"PLAYWRIGHT_BASE_URL": "https://synthetic.example"},
        )
        self.assertEqual(remote.returncode, 0, remote.stdout + remote.stderr)
        self.assertIn("2 skipped", remote.stdout)

    def test_local_plugins_cannot_authorize_remote_selection_with_marker_or_stash(self):
        result = self.execute(
            """
            import pytest

            from scripts.browser_journey_policy import browser_journey


            @pytest.mark.local_only
            @browser_journey
            def test_required_local_journey():
                raise AssertionError("required local journey was improperly skipped")
            """,
            root_conftest="""
            import pytest

            import scripts.browser_journey_policy as policy


            fake_key = pytest.StashKey()


            @pytest.hookimpl(tryfirst=True)
            def pytest_collection_modifyitems(items):
                item = items[0]
                item.stash[fake_key] = object()
                removed_helper = getattr(policy, "apply_remote_selection_skip", None)
                if removed_helper is None:
                    item.add_marker(pytest.mark.skip(reason="spoofed local remote selection"))
                else:
                    removed_helper(item, reason="spoofed local remote selection")
            """,
        )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 4, output)
        self.assertIn("uses forbidden permanent outcome marker(s): skip", output)

    def test_local_plugin_cannot_mutate_and_replay_registered_policy_selector(self):
        result = self.execute(
            """
            import pytest

            from scripts.browser_journey_policy import browser_journey


            @pytest.mark.local_only
            @browser_journey
            def test_local_only_executes(page):
                page.goto("required-local-only")


            @pytest.mark.creates_data
            @browser_journey
            def test_creates_data_executes(page):
                page.goto("required-creates-data")
            """,
            root_conftest="""
            import pytest


            fake_key = pytest.StashKey()


            def replay_with_mutated_attributes(config, items, phase):
                policy = config.pluginmanager.get_plugin(
                    "asl-browser-journey-policy"
                )
                policy._remote_selection_reason = f"forged-{phase}"
                policy._remote_selection_key = fake_key
                policy._selection_consumed = False
                policy.pytest_collection_modifyitems(items)


            @pytest.hookimpl(hookwrapper=True, tryfirst=True)
            def pytest_collection_modifyitems(config, items):
                replay_with_mutated_attributes(config, items, "before")
                yield
                for item in items:
                    item.stash[fake_key] = object()
                replay_with_mutated_attributes(config, items, "after")
            """,
        )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("2 passed", output)
        self.assertNotIn("skipped", output)

    def test_local_selector_has_no_introspectable_remote_capability_to_mutate(self):
        result = self.execute(
            """
            import pytest

            from scripts.browser_journey_policy import browser_journey


            @pytest.mark.local_only
            @browser_journey
            def test_local_only_executes(page):
                page.goto("required-local-only")


            @pytest.mark.creates_data
            @browser_journey
            def test_creates_data_executes(page):
                page.goto("required-creates-data")
            """,
            root_conftest="""
            import pytest

            import scripts.browser_journey_policy as policy_module


            def mutate_every_old_authority_surface(config, items):
                canonical = config.pluginmanager.get_plugin(
                    "asl-browser-journey-policy"
                )
                policy_module._PLUGIN_STATES = {canonical: object()}
                policy_module._RemoteStartupMode = lambda **kwargs: kwargs
                policy_module.BrowserJourneyPlugin = object
                policy_module.register_browser_journey_policy = lambda *args, **kwargs: None
                canonical._remote_selection_reason = "forged by introspection"
                canonical._selection_consumed = False
                canonical._remote_selection_key = pytest.StashKey()

                selector = type(canonical).pytest_collection_modifyitems
                closure_values = [
                    cell.cell_contents for cell in (selector.__closure__ or ())
                ]
                assert "forged by introspection" not in closure_values
                assert not any(isinstance(value, pytest.StashKey) for value in closure_values)
                canonical.pytest_collection_modifyitems(items)


            @pytest.hookimpl(hookwrapper=True, tryfirst=True)
            def pytest_collection_modifyitems(config, items):
                mutate_every_old_authority_surface(config, items)
                yield
                mutate_every_old_authority_surface(config, items)
            """,
        )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("2 passed", output)
        self.assertNotIn("skipped", output)

    def test_registrar_closure_cannot_be_replayed_after_config_and_environment_mutation(self):
        result = self.execute(
            """
            import pytest

            from scripts.browser_journey_policy import browser_journey


            @pytest.mark.local_only
            @browser_journey
            def test_required_local_journey():
                raise AssertionError("required local journey must not be skipped")
            """,
            root_conftest="""
            import os

            import pytest
            import scripts.browser_journey_policy as policy_module


            @pytest.hookimpl(hookwrapper=True, tryfirst=True)
            def pytest_collection_modifyitems(config, items):
                yield
                pluginmanager = config.pluginmanager
                canonical = pluginmanager.get_plugin(
                    "asl-browser-journey-policy"
                )
                guard = pluginmanager.get_plugin(
                    "asl-browser-journey-policy-integrity"
                )
                pluginmanager.unregister(canonical)
                pluginmanager.unregister(guard)
                os.environ["PLAYWRIGHT_BASE_URL"] = "https://forged.example"
                config.option.collectonly = False

                registrar = policy_module.register_browser_journey_policy
                assert registrar.__closure__ is None
                registrar(config)
            """,
        )

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("Playwright browser journey policy integrity failed", output)
        self.assertIn("installation was attempted more than once", output)

    def test_full_named_installation_removal_cannot_reinstall_remote_authority(self):
        result = self.execute(
            """
            import pytest

            from scripts.browser_journey_policy import browser_journey


            @pytest.mark.local_only
            @browser_journey
            def test_required_local_journey():
                raise AssertionError("required local journey must not be skipped")
            """,
            root_conftest="""
            import os

            import pytest
            import scripts.browser_journey_policy as policy_module


            @pytest.hookimpl(hookwrapper=True, tryfirst=True)
            def pytest_collection_modifyitems(config, items):
                yield
                pluginmanager = config.pluginmanager
                for name in (
                    "asl-browser-journey-policy",
                    "asl-browser-journey-policy-integrity",
                    "asl-browser-journey-policy-installation-seal",
                ):
                    pluginmanager.unregister(pluginmanager.get_plugin(name))
                os.environ["PLAYWRIGHT_BASE_URL"] = "https://forged.example"
                config.option.collectonly = False

                registrar = policy_module.register_browser_journey_policy
                assert registrar.__closure__ is None
                registrar(config)
                pluginmanager.get_plugin(
                    "asl-browser-journey-policy"
                ).pytest_collection_modifyitems(items)
            """,
        )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 4, output)
        self.assertIn("Playwright browser journey policy integrity failed", output)
        self.assertIn("installation was attempted more than once", output)

    def test_config_storage_record_forgery_cannot_disable_full_removal_guard(self):
        result = self.execute(
            """
            from scripts.browser_journey_policy import browser_journey


            @browser_journey
            def test_required_local_journey():
                raise AssertionError("required local journey must execute")
            """,
            root_conftest="""
            import pytest

            import scripts.browser_journey_policy as policy_module


            @pytest.hookimpl(trylast=True)
            def pytest_runtest_setup(item):
                config = item.config
                pluginmanager = config.pluginmanager
                storage = config.stash._storage
                record_type = getattr(
                    policy_module,
                    "_CanonicalInstallationRecord",
                    None,
                )
                if record_type is not None:
                    forged = record_type(
                        config_identity=id(config),
                        pluginmanager_identity=id(pluginmanager),
                        verifier=lambda: None,
                    )
                    for key, value in list(storage.items()):
                        if type(value) is record_type:
                            storage[key] = forged
                else:
                    forged_type = type("_CanonicalInstallationRecord", (), {})
                    storage[pytest.StashKey()] = forged_type()

                for name in (
                    "asl-browser-journey-policy",
                    "asl-browser-journey-policy-integrity",
                    "asl-browser-journey-policy-installation-seal",
                ):
                    pluginmanager.unregister(pluginmanager.get_plugin(name))
                pytest.skip("forged storage record tried to escape")
            """,
        )

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("Playwright browser journey policy integrity failed", output)
        self.assertIn("exact originally registered canonical plugin", output)

    def test_config_storage_deletion_cannot_replay_registrar_or_remote_selector(self):
        result = self.execute(
            """
            import pytest

            from scripts.browser_journey_policy import browser_journey


            @pytest.mark.local_only
            @browser_journey
            def test_required_local_journey():
                raise AssertionError("required local journey must not be skipped")
            """,
            root_conftest="""
            import os

            import pytest
            import scripts.browser_journey_policy as policy_module


            @pytest.hookimpl(hookwrapper=True, tryfirst=True)
            def pytest_collection_modifyitems(config, items):
                yield
                pluginmanager = config.pluginmanager
                record_type = getattr(
                    policy_module,
                    "_CanonicalInstallationRecord",
                    None,
                )
                for key, value in list(config.stash._storage.items()):
                    if record_type is not None and type(value) is record_type:
                        del config.stash._storage[key]
                for name in (
                    "asl-browser-journey-policy",
                    "asl-browser-journey-policy-integrity",
                    "asl-browser-journey-policy-installation-seal",
                ):
                    pluginmanager.unregister(pluginmanager.get_plugin(name))
                os.environ["PLAYWRIGHT_BASE_URL"] = "https://forged.example"
                config.option.collectonly = False

                registrar = policy_module.register_browser_journey_policy
                assert registrar.__closure__ is None
                registrar(config)
                pluginmanager.get_plugin(
                    "asl-browser-journey-policy"
                ).pytest_collection_modifyitems(items)
            """,
        )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 4, output)
        self.assertIn("Playwright browser journey policy integrity failed", output)
        self.assertIn("installation was attempted more than once", output)

    def test_public_audit_dispatch_replacement_cannot_replay_remote_selector(self):
        result = self.execute(
            """
            import pytest

            from scripts.browser_journey_policy import browser_journey


            @pytest.mark.local_only
            @browser_journey
            def test_required_local_journey():
                raise AssertionError("required local journey must not be skipped")
            """,
            root_conftest="""
            import os
            import sys

            import pytest
            import scripts.browser_journey_policy as policy_module


            @pytest.hookimpl(hookwrapper=True, tryfirst=True)
            def pytest_collection_modifyitems(config, items):
                yield
                pluginmanager = config.pluginmanager
                for name in (
                    "asl-browser-journey-policy",
                    "asl-browser-journey-policy-integrity",
                    "asl-browser-journey-policy-installation-seal",
                ):
                    pluginmanager.unregister(pluginmanager.get_plugin(name))
                os.environ["PLAYWRIGHT_BASE_URL"] = "https://forged.example"
                config.option.collectonly = False
                sys.audit = lambda *args: None

                registrar = policy_module.register_browser_journey_policy
                assert registrar.__closure__ is None
                registrar(config)
                pluginmanager.get_plugin(
                    "asl-browser-journey-policy"
                ).pytest_collection_modifyitems(items)
            """,
        )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 4, output)
        self.assertIn("Playwright browser journey policy integrity failed", output)
        self.assertIn("process authority dispatch identity changed", output)

    def test_mutable_builtin_function_alias_cannot_forge_authority_dispatch(self):
        result = self.execute(
            """
            import pytest

            from scripts.browser_journey_policy import browser_journey


            @pytest.mark.local_only
            @browser_journey
            def test_required_local_journey():
                raise AssertionError("required local journey must not be skipped")
            """,
            root_conftest="""
            import os
            import sys
            import types

            import pytest
            import scripts.browser_journey_policy as policy_module


            @pytest.hookimpl(hookwrapper=True, tryfirst=True)
            def pytest_collection_modifyitems(config, items):
                yield
                pluginmanager = config.pluginmanager
                for name in (
                    "asl-browser-journey-policy",
                    "asl-browser-journey-policy-integrity",
                    "asl-browser-journey-policy-installation-seal",
                ):
                    pluginmanager.unregister(pluginmanager.get_plugin(name))
                os.environ["PLAYWRIGHT_BASE_URL"] = "https://forged.example"
                config.option.collectonly = False
                types.BuiltinFunctionType = types.FunctionType

                def forged_audit(*args):
                    return None

                forged_audit.__self__ = sys
                forged_audit.__module__ = "sys"
                forged_audit.__name__ = "audit"
                sys.audit = forged_audit

                registrar = policy_module.register_browser_journey_policy
                assert registrar.__closure__ is None
                registrar(config)
                pluginmanager.get_plugin(
                    "asl-browser-journey-policy"
                ).pytest_collection_modifyitems(items)
            """,
        )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 4, output)
        self.assertIn("Playwright browser journey policy integrity failed", output)
        self.assertIn("process authority dispatch identity changed", output)

    def test_canonical_policy_unregister_replace_duplicate_and_reregister_fail_closed(self):
        source = """
            import pytest

            from scripts.browser_journey_policy import browser_journey


            @pytest.mark.local_only
            @browser_journey
            def test_required_local_journey():
                raise AssertionError("required local journey must never be skipped")
        """
        cases = {
            "unregister": """
                import pytest


                @pytest.hookimpl(hookwrapper=True, tryfirst=True)
                def pytest_collection_modifyitems(config, items):
                    yield
                    canonical = config.pluginmanager.get_plugin(
                        "asl-browser-journey-policy"
                    )
                    config.pluginmanager.unregister(canonical)
            """,
            "replace": """
                import pytest

                from scripts.browser_journey_policy import BrowserJourneyPlugin


                class ForgedRemotePolicy(BrowserJourneyPlugin):
                    @pytest.hookimpl(trylast=True)
                    def pytest_collection_modifyitems(self, items):
                        for item in items:
                            item.add_marker(pytest.mark.skip(reason="forged remote"))


                @pytest.hookimpl(hookwrapper=True, tryfirst=True)
                def pytest_collection_modifyitems(config, items):
                    yield
                    canonical = config.pluginmanager.get_plugin(
                        "asl-browser-journey-policy"
                    )
                    page_type = canonical._page_type
                    config.pluginmanager.unregister(canonical)
                    forged = ForgedRemotePolicy(page_type=page_type)
                    config.pluginmanager.register(
                        forged,
                        "asl-browser-journey-policy",
                    )
                    forged.pytest_collection_modifyitems(items)
            """,
            "duplicate": """
                import pytest

                from scripts.browser_journey_policy import BrowserJourneyPlugin


                @pytest.hookimpl(hookwrapper=True, tryfirst=True)
                def pytest_collection_modifyitems(config, items):
                    yield
                    canonical = config.pluginmanager.get_plugin(
                        "asl-browser-journey-policy"
                    )
                    config.pluginmanager.register(
                        BrowserJourneyPlugin(page_type=canonical._page_type),
                        "forged-duplicate-browser-journey-policy",
                    )
            """,
            "reregister": """
                import pytest


                @pytest.hookimpl(hookwrapper=True, tryfirst=True)
                def pytest_collection_modifyitems(config, items):
                    yield
                    canonical = config.pluginmanager.get_plugin(
                        "asl-browser-journey-policy"
                    )
                    config.pluginmanager.unregister(canonical)
                    config.pluginmanager.register(
                        canonical,
                        "asl-browser-journey-policy",
                    )
            """,
            "authorization-shadow": """
                import pytest


                @pytest.hookimpl(hookwrapper=True, tryfirst=True)
                def pytest_collection_modifyitems(config, items):
                    yield
                    canonical = config.pluginmanager.get_plugin(
                        "asl-browser-journey-policy"
                    )
                    canonical._is_trusted_remote_selection = lambda item: True
                    items[0].add_marker(pytest.mark.skip(reason="forged shadow"))
            """,
            "remove-all-reinstall": """
                import os

                import pytest

                from scripts.browser_journey_policy import (
                    register_browser_journey_policy,
                )


                @pytest.hookimpl(hookwrapper=True, tryfirst=True)
                def pytest_collection_modifyitems(config, items):
                    yield
                    pluginmanager = config.pluginmanager
                    canonical = pluginmanager.get_plugin(
                        "asl-browser-journey-policy"
                    )
                    guard = pluginmanager.get_plugin(
                        "asl-browser-journey-policy-integrity"
                    )
                    pluginmanager.unregister(canonical)
                    pluginmanager.unregister(guard)
                    os.environ["PLAYWRIGHT_BASE_URL"] = "https://forged.example"
                    register_browser_journey_policy(config)
            """,
        }

        for name, root_conftest in cases.items():
            with self.subTest(name=name):
                result = self.execute(source, root_conftest=root_conftest)
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 4, output)
                self.assertIn(
                    "Playwright browser journey policy integrity failed",
                    output,
                )

    def test_canonical_policy_removal_after_collection_fails_at_execution(self):
        result = self.execute(
            """
            from scripts.browser_journey_policy import browser_journey


            @browser_journey
            def test_declared_owner(page):
                page.goto("must-not-run-without-policy")
            """,
            root_conftest="""
            import pytest


            @pytest.hookimpl(hookwrapper=True, tryfirst=True)
            def pytest_collection_finish(session):
                yield
                canonical = session.config.pluginmanager.get_plugin(
                    "asl-browser-journey-policy"
                )
                session.config.pluginmanager.unregister(canonical)
            """,
        )

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("Playwright browser journey policy integrity failed", output)
        self.assertIn("exact originally registered canonical plugin", output)

    def test_late_setup_removal_or_authorization_shadow_cannot_escape_report_guard(self):
        source = """
            from scripts.browser_journey_policy import browser_journey


            @browser_journey
            def test_required_local_journey():
                raise AssertionError("required local journey must execute")
        """
        cases = {
            "late-unregister": """
                import pytest


                @pytest.hookimpl(trylast=True)
                def pytest_runtest_setup(item):
                    pluginmanager = item.config.pluginmanager
                    canonical = pluginmanager.get_plugin(
                        "asl-browser-journey-policy"
                    )
                    pluginmanager.unregister(canonical)
                    pytest.skip("late removal tried to escape")
            """,
            "late-unregister-policy-and-guard": """
                import pytest


                @pytest.hookimpl(trylast=True)
                def pytest_runtest_setup(item):
                    pluginmanager = item.config.pluginmanager
                    canonical = pluginmanager.get_plugin(
                        "asl-browser-journey-policy"
                    )
                    guard = pluginmanager.get_plugin(
                        "asl-browser-journey-policy-integrity"
                    )
                    pluginmanager.unregister(canonical)
                    pluginmanager.unregister(guard)
                    pytest.skip("combined late removal tried to escape")
            """,
            "late-unregister-policy-guard-and-seal": """
                import pytest


                @pytest.hookimpl(trylast=True)
                def pytest_runtest_setup(item):
                    pluginmanager = item.config.pluginmanager
                    for name in (
                        "asl-browser-journey-policy",
                        "asl-browser-journey-policy-integrity",
                        "asl-browser-journey-policy-installation-seal",
                    ):
                        pluginmanager.unregister(pluginmanager.get_plugin(name))
                    pytest.skip("full late removal tried to escape")
            """,
            "late-authorization-shadow": """
                import pytest


                @pytest.hookimpl(trylast=True)
                def pytest_runtest_setup(item):
                    canonical = item.config.pluginmanager.get_plugin(
                        "asl-browser-journey-policy"
                    )
                    canonical._is_trusted_remote_selection = lambda candidate: True
                    pytest.skip("late shadow tried to escape")
            """,
        }

        for name, root_conftest in cases.items():
            with self.subTest(name=name):
                result = self.execute(source, root_conftest=root_conftest)
                output = result.stdout + result.stderr
                self.assertNotEqual(result.returncode, 0, output)
                self.assertIn(
                    "Playwright browser journey policy integrity failed",
                    output,
                )

    def test_remote_selector_replay_fails_closed_instead_of_reminting_authority(self):
        result = self.execute(
            """
            import pytest

            from scripts.browser_journey_policy import browser_journey


            @pytest.mark.local_only
            @browser_journey
            def test_remote_selected_owner():
                raise AssertionError("remote-selected owner should be skipped once")
            """,
            root_conftest="""
            import pytest


            @pytest.hookimpl(hookwrapper=True, tryfirst=True)
            def pytest_collection_modifyitems(config, items):
                yield
                canonical = config.pluginmanager.get_plugin(
                    "asl-browser-journey-policy"
                )
                canonical.pytest_collection_modifyitems(items)
            """,
            extra_env={"PLAYWRIGHT_BASE_URL": "https://synthetic.example"},
        )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 4, output)
        self.assertIn("uses forbidden permanent outcome marker(s): skip", output)

    def test_remote_marker_mutation_or_extra_skip_invalidates_authorization(self):
        result = self.execute(
            """
            import pytest

            from scripts.browser_journey_policy import browser_journey


            @pytest.mark.local_only
            @browser_journey
            def test_mutated_remote_marker(page):
                page.goto("local-only")


            @pytest.mark.creates_data
            @browser_journey
            def test_extra_remote_marker(page):
                page.goto("creates-data")
            """,
            root_conftest="""
            import pytest


            @pytest.hookimpl(hookwrapper=True, tryfirst=True)
            def pytest_collection_modifyitems(items):
                yield
                by_name = {item.name: item for item in items}
                mutated = by_name["test_mutated_remote_marker"]
                mutated.get_closest_marker("skip").kwargs["reason"] = "mutated"
                by_name["test_extra_remote_marker"].add_marker(
                    pytest.mark.skip(reason="extra spoofed skip")
                )
            """,
            extra_env={"PLAYWRIGHT_BASE_URL": "https://synthetic.example"},
        )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 4, output)
        self.assertIn("test_mutated_remote_marker", output)
        self.assertIn("test_extra_remote_marker", output)
        self.assertIn("uses forbidden permanent outcome marker(s): skip", output)

    def test_active_conditional_outcomes_cannot_bypass_required_local_execution(self):
        result = self.execute(
            """
            import pytest

            from scripts.browser_journey_policy import browser_journey


            @pytest.mark.skipif(True, reason="active skip attempted")
            @browser_journey
            def test_active_skipif(page):
                page.goto("unreachable")


            @pytest.mark.xfail(True, reason="active xfail attempted")
            @browser_journey
            def test_active_xfail(page):
                page.goto("proof-does-not-authorize-xfail")
            """
        )

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("ERROR at setup of test_active_skipif", output)
        self.assertIn("test_active_xfail - Declared", output)
        self.assertIn("attempted a runtime skip", output)
        self.assertIn("attempted a runtime xfail", output)

    def test_declared_owner_must_belong_to_required_local_execution_lane(self):
        rejected = self.collect(
            """
            import pytest

            from scripts.browser_journey_policy import browser_journey


            @pytest.mark.skip(reason="permanent")
            @browser_journey
            def test_skip():
                return None


            @pytest.mark.manual_visual
            @browser_journey
            def test_manual_visual():
                return None


            @pytest.mark.slow_platform
            @browser_journey
            def test_slow_platform():
                return None
            """
        )
        output = rejected.stdout + rejected.stderr
        self.assertNotEqual(rejected.returncode, 0, output)
        for marker in ("skip", "manual_visual", "slow_platform"):
            self.assertIn(marker, output)
        self.assertIn("required scheduled local execution", output)
