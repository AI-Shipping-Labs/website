"""Explicit Playwright journey declaration and per-item runtime evidence.

The decorator is intentionally identity-based.  Pytest marks, copied function
attributes, wrapper metadata, source spelling, and fixture names are not
declarations.  Runtime evidence is likewise narrow: only successful calls to
the configured synchronous Playwright ``Page`` methods count, and only while
the declared item's setup or call phase is active.
"""

from __future__ import annotations

import functools
import inspect
import json
import os
import types
import warnings
import weakref
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypeVar
from urllib.parse import urlparse

import pytest

_CallableT = TypeVar("_CallableT", bound=Callable[..., Any])
_DECLARATION_ATTRIBUTE = "__asl_browser_journey_declaration__"


@dataclass(frozen=True)
class _BrowserJourneyDeclaration:
    """Typed sentinel whose exact central instance is the declaration token."""

    policy: str = "item-local-playwright-journey-v1"


_DECLARATION = _BrowserJourneyDeclaration()
_DECLARED_CALLABLES: weakref.WeakSet[Callable[..., Any]] = weakref.WeakSet()


@dataclass(frozen=True)
class _DeclarationContext:
    """Callable metadata frozen at the exact decorator invocation."""

    module: str | None
    name: str | None
    qualname: str | None


_DECLARATION_CONTEXTS: weakref.WeakKeyDictionary[Callable[..., Any], _DeclarationContext] = weakref.WeakKeyDictionary()


@dataclass(frozen=True)
class _DeclaredItemState:
    """Collection snapshot keyed by stable process-local item identity."""

    nodeid: str
    callable_identity: Callable[..., Any]


def browser_journey(function: _CallableT) -> _CallableT:
    """Declare this exact final callable as a browser journey.

    The callable is returned unchanged.  The weak identity registry prevents
    ``functools.wraps`` or a copied ``__dict__`` from transferring ownership to
    a different final callable accidentally.
    """

    if not callable(function):
        raise TypeError("@browser_journey can decorate only a callable")
    setattr(function, _DECLARATION_ATTRIBUTE, _DECLARATION)
    _DECLARED_CALLABLES.add(function)
    _DECLARATION_CONTEXTS[function] = _DeclarationContext(
        module=getattr(function, "__module__", None),
        name=getattr(function, "__name__", None),
        qualname=getattr(function, "__qualname__", None),
    )
    return function


def is_browser_journey(candidate: Any) -> bool:
    """Return whether ``candidate`` is the exact centrally declared callable."""

    current_context = _DeclarationContext(
        module=getattr(candidate, "__module__", None),
        name=getattr(candidate, "__name__", None),
        qualname=getattr(candidate, "__qualname__", None),
    )
    return (
        callable(candidate)
        and not isinstance(candidate, types.MethodType)
        and getattr(candidate, _DECLARATION_ATTRIBUTE, None) is _DECLARATION
        and candidate in _DECLARED_CALLABLES
        and _DECLARATION_CONTEXTS.get(candidate) == current_context
    )


def collected_item_callable(item: pytest.Item) -> Callable[..., Any] | None:
    """Return the exact callable tied to this pytest owner context."""

    if not isinstance(item, pytest.Function):
        return None
    candidate = item.obj
    owner_class = item.cls
    if owner_class is None:
        if isinstance(candidate, types.FunctionType):
            return candidate
        return None
    if not isinstance(candidate, types.MethodType):
        return None
    original_name = item.originalname
    if not original_name:
        return None
    descriptor = vars(owner_class).get(original_name)
    function = candidate.__func__
    if descriptor is function:
        if candidate.__self__ is not item.instance:
            return None
    elif isinstance(descriptor, classmethod) and descriptor.__func__ is function:
        if candidate.__self__ is not owner_class:
            return None
    else:
        return None
    expected_qualname = f"{owner_class.__qualname__}.{original_name}"
    if function.__name__ != original_name or function.__qualname__ != expected_qualname:
        return None
    return function


def _declared_item_callable(item: pytest.Item) -> Callable[..., Any] | None:
    """Return the declaration bound to this exact pytest owner context."""

    function = collected_item_callable(item)
    if function is not None and is_browser_journey(function):
        return function
    return None


def is_browser_journey_item(item: pytest.Item) -> bool:
    """Return whether the exact collected pytest owner is centrally declared."""

    return _declared_item_callable(item) is not None


class BrowserJourneyPlugin:
    """Pytest plugin enforcing successful synchronous Page evidence per item."""

    SUPPORTED_METHODS = ("goto", "set_content")

    def __init__(
        self,
        *,
        page_type: type[Any] | None = None,
        enforce_collection: bool = True,
    ):
        self._page_type = page_type
        self._enforce_collection = enforce_collection
        self._original_methods: dict[str, Callable[..., Any]] = {}
        self._evidence: dict[int, set[str]] = {}
        self._declared_items: dict[int, _DeclaredItemState] = {}
        self._invocation_identities: dict[int, Callable[..., Any]] = {}
        self._active_item: ContextVar[pytest.Item | None] = ContextVar(
            "browser_journey_active_item",
            default=None,
        )

    def _resolve_page_type(self) -> type[Any]:
        if self._page_type is None:
            from playwright.sync_api import Page

            self._page_type = Page
        return self._page_type

    def _install_observer(self) -> None:
        if self._original_methods:
            return
        page_type = self._resolve_page_type()
        for method_name in self.SUPPORTED_METHODS:
            original = getattr(page_type, method_name)
            self._original_methods[method_name] = original

            @functools.wraps(original)
            def observed(page, *args, __name=method_name, __original=original, **kwargs):
                result = __original(page, *args, **kwargs)
                item = self._active_item.get()
                if item is not None and self._runtime_identity_error(item) is None:
                    self._evidence.setdefault(id(item), set()).add(__name)
                return result

            setattr(page_type, method_name, observed)

    def _restore_observer(self) -> None:
        if not self._original_methods:
            return
        page_type = self._resolve_page_type()
        for method_name, original in self._original_methods.items():
            setattr(page_type, method_name, original)
        self._original_methods.clear()

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        if not session.config.option.collectonly:
            self._install_observer()

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        self._restore_observer()

    def _is_trusted_remote_selection(self, item: pytest.Item) -> bool:
        """Publicly constructible policy instances never authorize a skip."""

        return False

    @pytest.hookimpl(trylast=True)
    def pytest_collection_finish(self, session: pytest.Session) -> None:
        """Validate identities after every collection-modification hook completes."""
        items = list(session.items)
        if not self._enforce_collection:
            self._remember_declared_items(items)
            return
        from scripts.playwright_owner_inventory import (
            InventoryError,
            normalize_item,
            normalize_items,
        )

        root = Path(str(session.config.rootpath)).resolve()
        live_path = root / "tests" / "playwright_owner_inventory_live.json"
        payload = json.loads(live_path.read_text(encoding="utf-8"))
        legacy = set(payload.get("LEGACY_DECLARED_BROWSER", [])) | set(payload.get("LEGACY_NON_BROWSER", {}))
        playwright_items = []
        for item in items:
            try:
                relative = Path(item.path).resolve().relative_to(root).as_posix()
            except (AttributeError, ValueError):
                continue
            if relative.startswith("playwright_tests/test_") and relative.endswith(".py"):
                playwright_items.append(item)
        try:
            owners = normalize_items(playwright_items, root)
            normalized_items = [normalize_item(item, root) for item in playwright_items]
        except InventoryError as exc:
            raise pytest.UsageError(f"Playwright browser journey policy failed:\n- {exc}") from exc
        errors: list[str] = []
        declared_owner_ids: set[str] = set()
        normalized_by_nodeid = {normalized.nodeid: normalized for normalized in normalized_items}
        for item in playwright_items:
            normalized = normalized_by_nodeid[item.nodeid]
            owner_id = normalized.owner_id
            if normalized.declared:
                declared_owner_ids.add(owner_id)
                untrusted_skips = [
                    marker for marker in item.iter_markers(name="skip") if not self._is_trusted_remote_selection(item)
                ]
                if untrusted_skips:
                    errors.append(
                        f"declared owner `{owner_id}` uses forbidden permanent outcome marker(s): "
                        "skip. A new declaration must execute normally in its required local lane."
                    )
                excluded_lanes = [
                    marker
                    for marker in ("manual_visual", "slow_platform")
                    if item.get_closest_marker(marker) is not None
                ]
                if excluded_lanes:
                    errors.append(
                        f"declared owner `{owner_id}` is excluded from required scheduled local "
                        f"execution by marker(s): {', '.join(excluded_lanes)}."
                    )

        selected_owner_ids = {owner.owner_id for owner in owners}
        for owner_id in sorted(selected_owner_ids - legacy - declared_owner_ids):
            errors.append(
                f"new selected owner `{owner_id}` has no accepted legacy entry and its exact "
                "final callable is not decorated with @browser_journey."
            )
        for owner_id in sorted(declared_owner_ids & legacy):
            errors.append(
                f"declared live owner `{owner_id}` must be removed only from the legacy live "
                "manifest; its immutable ceiling stays unchanged."
            )
        if errors:
            raise pytest.UsageError("Playwright browser journey policy failed:\n- " + "\n- ".join(sorted(set(errors))))
        self._remember_declared_items(playwright_items)

    def _remember_declared_items(self, items: list[pytest.Item]) -> None:
        self._declared_items = {}
        for item in items:
            callable_identity = _declared_item_callable(item)
            if callable_identity is not None:
                self._declared_items[id(item)] = _DeclaredItemState(
                    nodeid=item.nodeid,
                    callable_identity=callable_identity,
                )

    def _runtime_identity_error(self, item: pytest.Item) -> str | None:
        state = self._declared_items.get(id(item))
        if state is None:
            return None
        if item.nodeid != state.nodeid:
            return (
                f"Declared browser journey `{state.nodeid}` changed node ID after completed "
                f"collection to `{item.nodeid}`. Runtime policy state is bound to the stable "
                "pytest item identity and the collected node ID must not drift."
            )
        current_identity = _declared_item_callable(item)
        if current_identity is state.callable_identity or current_identity is not None:
            return None
        return (
            f"Declared browser journey `{state.nodeid}` changed callable identity after "
            "completed collection. Any final execution replacement must explicitly carry "
            "its own @browser_journey declaration; arbitrary __func__ or copied metadata "
            "is not accepted."
        )

    def _assert_runtime_identity(self, item: pytest.Item) -> None:
        error = self._runtime_identity_error(item)
        if error is not None:
            pytest.fail(error, pytrace=False)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_setup(self, item: pytest.Item):
        self._evidence[id(item)] = set()
        token = self._active_item.set(item)
        try:
            yield
        finally:
            self._active_item.reset(token)

    @pytest.hookimpl(hookwrapper=True, trylast=True)
    def pytest_runtest_call(self, item: pytest.Item):
        self._assert_runtime_identity(item)
        token = self._active_item.set(item)
        try:
            yield
        finally:
            self._active_item.reset(token)

    @pytest.hookimpl(tryfirst=True)
    def pytest_pyfunc_call(self, pyfuncitem: pytest.Function) -> bool | None:
        """Own and pin the exact declared callable's real invocation boundary."""

        item_key = id(pyfuncitem)
        state = self._declared_items.get(item_key)
        if state is None:
            return None
        self._assert_runtime_identity(pyfuncitem)
        testfunction = pyfuncitem.obj
        callable_identity = _declared_item_callable(pyfuncitem)
        if callable_identity is None:
            pytest.fail(
                f"Declared browser journey `{state.nodeid}` lost its exact owner declaration.",
                pytrace=False,
            )
        self._invocation_identities[item_key] = callable_identity
        if inspect.iscoroutinefunction(testfunction) or inspect.isasyncgenfunction(testfunction):
            pytest.fail(
                f"Declared browser journey `{state.nodeid}` must be synchronous.",
                pytrace=False,
            )
        funcargs = pyfuncitem.funcargs
        testargs = {arg: funcargs[arg] for arg in pyfuncitem._fixtureinfo.argnames}
        result = testfunction(**testargs)
        if inspect.isawaitable(result) or hasattr(result, "__aiter__"):
            pytest.fail(
                f"Declared browser journey `{state.nodeid}` returned an async result.",
                pytrace=False,
            )
        if result is not None:
            warnings.warn(
                pytest.PytestReturnNotNoneWarning(
                    f"Test functions should return None, but {state.nodeid} returned {type(result)!r}."
                ),
                stacklevel=2,
            )
        self._assert_runtime_identity(pyfuncitem)
        return True

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item: pytest.Item, call: pytest.CallInfo[Any]):
        outcome = yield
        report = outcome.get_result()
        item_key = id(item)
        state = self._declared_items.get(item_key)
        if state is None:
            return
        identity_error = self._runtime_identity_error(item)
        if identity_error is not None:
            report.outcome = "failed"
            report.longrepr = identity_error
            return
        if report.skipped or hasattr(report, "wasxfail"):
            if report.skipped and not hasattr(report, "wasxfail") and self._is_trusted_remote_selection(item):
                return
            outcome_name = "xfail" if hasattr(report, "wasxfail") else "skip"
            report.outcome = "failed"
            report.longrepr = (
                f"Declared browser journey `{state.nodeid}` attempted a runtime {outcome_name}. "
                "New declarations must execute in a required lane and prove a successful real "
                "synchronous Playwright Page.goto or Page.set_content journey; runtime skip "
                "and xfail outcomes are not accepted."
            )
            return
        if report.when == "call" and report.passed:
            invocation_identity = self._invocation_identities.get(item_key)
            if invocation_identity is None:
                report.outcome = "failed"
                report.longrepr = (
                    f"Declared browser journey `{state.nodeid}` passed without its exact callable "
                    "being validated at the pytest invocation boundary."
                )
                return
            if self._evidence.get(item_key):
                return
            report.outcome = "failed"
            report.longrepr = (
                f"Declared browser journey `{state.nodeid}` passed without a successful real "
                "synchronous Playwright Page journey during setup or call. Supported evidence: "
                "Page.goto or Page.set_content called directly, through goto_with_retry or "
                "another helper, or from a function-scoped setup fixture. Fake method-name "
                "collisions, page.request, unreachable, failed, teardown-only, or another "
                "item's calls do not count."
            )

    def pytest_runtest_teardown(self, item: pytest.Item) -> None:
        # Teardown runs with no active item, so a late Page call cannot satisfy
        # the proof.  Retain no evidence beyond this item's lifecycle.
        item_key = id(item)
        self._evidence.pop(item_key, None)
        self._invocation_identities.pop(item_key, None)


def register_browser_journey_policy(
    config: pytest.Config,
    *,
    page_type: type[Any] | None = None,
) -> None:
    """Install the one canonical policy from immutable pytest-startup input.

    This is intentionally the only remote-capable installation path. It does
    not accept a mode or reason: the effective ``PLAYWRIGHT_BASE_URL`` is read
    once while pytest configures the canonical Playwright conftest. Local and
    remote startup produce structurally different closure-defined plugins, so
    a local policy has no remote key, token, reason, or selector branch that
    introspection or replay can turn on later.
    """

    import sys as runtime_sys

    pluginmanager = config.pluginmanager
    canonical_name = "asl-browser-journey-policy"
    guard_name = "asl-browser-journey-policy-integrity"
    seal_name = "asl-browser-journey-policy-installation-seal"
    authority_dispatch = runtime_sys.audit
    if not (
        authority_dispatch.__class__ is [].append.__class__
        and authority_dispatch.__self__ is runtime_sys
        and authority_dispatch.__module__ == "sys"
        and authority_dispatch.__name__ == "audit"
    ):
        raise pytest.UsageError(
            "Playwright browser journey policy integrity failed: the process authority dispatch identity changed."
        )
    authority_dispatch(
        "asl.browser_journey_policy.register.v1",
        config,
        pluginmanager,
    )
    if any(pluginmanager.get_plugin(name) is not None for name in (canonical_name, guard_name, seal_name)):
        raise pytest.UsageError(
            "Playwright browser journey policy integrity failed: canonical policy "
            "installation was attempted more than once."
        )

    configured_url = os.environ.get("PLAYWRIGHT_BASE_URL", "").strip()
    configured_host = ""
    if configured_url:
        try:
            configured_host = (urlparse(configured_url).hostname or "").lower()
        except (AttributeError, ValueError):
            configured_host = ""
    local_hosts = frozenset({"127.0.0.1", "localhost", "0.0.0.0", "::1"})
    remote_reason = None
    if not config.option.collectonly and configured_host and configured_host not in local_hosts:
        remote_reason = (
            f"Skipped: requires local Django runserver (PLAYWRIGHT_BASE_URL={configured_url!r} is non-local)."
        )

    canonical_plugin: BrowserJourneyPlugin
    canonical_type: type[BrowserJourneyPlugin]
    policy_base_type = BrowserJourneyPlugin
    integrity_guard: object
    installation_seal: object
    installation_seal_type: type[Any]
    expected_trust_function: Callable[..., Any]
    expected_policy_hooks: tuple[tuple[str, Callable[..., Any]], ...] = ()
    expected_guard_hooks: tuple[tuple[str, Callable[..., Any]], ...] = ()
    expected_seal_hooks: tuple[tuple[str, Callable[..., Any]], ...] = ()
    policy_hook_names = (
        "pytest_collection_finish",
        "pytest_collection_modifyitems",
        "pytest_pyfunc_call",
        "pytest_runtest_call",
        "pytest_runtest_makereport",
        "pytest_runtest_setup",
        "pytest_runtest_teardown",
        "pytest_sessionfinish",
        "pytest_sessionstart",
    )
    guard_hook_names = (
        "pytest_collection_finish",
        "pytest_runtest_call",
        "pytest_runtest_makereport",
        "pytest_runtest_setup",
        "pytest_sessionstart",
    )
    seal_hook_names = ("pytest_runtest_makereport",)

    def registered_hooks(
        plugin: object,
        hook_names: tuple[str, ...],
    ) -> tuple[tuple[str, Callable[..., Any]], ...]:
        found: list[tuple[str, Callable[..., Any]]] = []
        for hook_name in hook_names:
            hook = getattr(config.hook, hook_name)
            for implementation in hook.get_hookimpls():
                if implementation.plugin is plugin:
                    found.append((hook_name, implementation.function))
        return tuple(found)

    def hooks_match(
        plugin: object,
        expected: tuple[tuple[str, Callable[..., Any]], ...],
        hook_names: tuple[str, ...],
    ) -> bool:
        current = registered_hooks(plugin, hook_names)
        if len(current) != len(expected):
            return False
        return all(
            current_name == expected_name and current_function is expected_function
            for (current_name, current_function), (expected_name, expected_function) in zip(
                current,
                expected,
                strict=True,
            )
        )

    def assert_canonical_integrity() -> None:
        problems: list[str] = []
        if runtime_sys.audit is not authority_dispatch:
            problems.append("the process authority dispatch identity changed")
        if pluginmanager.get_plugin(canonical_name) is not canonical_plugin:
            problems.append("the exact originally registered canonical plugin is missing or replaced")
        if pluginmanager.get_plugin(guard_name) is not integrity_guard:
            problems.append("the exact canonical integrity guard is missing or replaced")
        if pluginmanager.get_plugin(seal_name) is not installation_seal:
            problems.append("the exact canonical installation seal is missing or replaced")
        if type(installation_seal) is not installation_seal_type:
            problems.append("the canonical installation seal class identity changed")
        if type(canonical_plugin) is not canonical_type:
            problems.append("the canonical policy class identity changed")
        if canonical_type.__dict__.get(
            "_is_trusted_remote_selection"
        ) is not expected_trust_function or "_is_trusted_remote_selection" in vars(canonical_plugin):
            problems.append("the canonical remote-authorization hook identity changed")
        policy_instances = [plugin for plugin in pluginmanager.get_plugins() if isinstance(plugin, policy_base_type)]
        if len(policy_instances) != 1 or policy_instances[0] is not canonical_plugin:
            problems.append("a duplicate or forged browser journey policy is registered")
        if expected_policy_hooks and not hooks_match(
            canonical_plugin,
            expected_policy_hooks,
            policy_hook_names,
        ):
            problems.append("the canonical policy hook identity changed")
        if expected_guard_hooks and not hooks_match(
            integrity_guard,
            expected_guard_hooks,
            guard_hook_names,
        ):
            problems.append("the canonical integrity hook identity changed")
        if expected_seal_hooks and not hooks_match(
            installation_seal,
            expected_seal_hooks,
            seal_hook_names,
        ):
            problems.append("the canonical installation seal hook identity changed")
        if problems:
            raise pytest.UsageError(
                "Playwright browser journey policy integrity failed:\n- " + "\n- ".join(sorted(set(problems)))
            )

    if remote_reason is None:

        class CanonicalBrowserJourneyPlugin(BrowserJourneyPlugin):
            @pytest.hookimpl(trylast=True)
            def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None:
                assert_canonical_integrity()

            def _is_trusted_remote_selection(self, item: pytest.Item) -> bool:
                return False

    else:
        remote_selection_key = pytest.StashKey()
        remote_authorization_token = object()

        class CanonicalBrowserJourneyPlugin(BrowserJourneyPlugin):
            @pytest.hookimpl(trylast=True)
            def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None:
                assert_canonical_integrity()
                for item in items:
                    if not (item.get_closest_marker("local_only") or item.get_closest_marker("creates_data")):
                        continue
                    item.add_marker(pytest.mark.skip(reason=remote_reason))
                    marker = item.own_markers[-1]
                    item.stash[remote_selection_key] = (
                        remote_authorization_token,
                        marker,
                        remote_reason,
                    )

            def _is_trusted_remote_selection(self, item: pytest.Item) -> bool:
                if not (item.get_closest_marker("local_only") or item.get_closest_marker("creates_data")):
                    return False
                authorization = item.stash.get(remote_selection_key, None)
                if not (
                    isinstance(authorization, tuple)
                    and len(authorization) == 3
                    and authorization[0] is remote_authorization_token
                    and authorization[2] == remote_reason
                ):
                    return False
                skip_markers = list(item.iter_markers(name="skip"))
                return (
                    len(skip_markers) == 1
                    and skip_markers[0] is authorization[1]
                    and authorization[1].kwargs == {"reason": remote_reason}
                )

    class CanonicalPolicyIntegrityGuard:
        @pytest.hookimpl(tryfirst=True)
        def pytest_sessionstart(self, session: pytest.Session) -> None:
            assert_canonical_integrity()

        @pytest.hookimpl(tryfirst=True)
        def pytest_collection_finish(self, session: pytest.Session) -> None:
            assert_canonical_integrity()

        @pytest.hookimpl(tryfirst=True)
        def pytest_runtest_setup(self, item: pytest.Item) -> None:
            assert_canonical_integrity()

        @pytest.hookimpl(tryfirst=True)
        def pytest_runtest_call(self, item: pytest.Item) -> None:
            assert_canonical_integrity()

        @pytest.hookimpl(hookwrapper=True, tryfirst=True)
        def pytest_runtest_makereport(
            self,
            item: pytest.Item,
            call: pytest.CallInfo[Any],
        ):
            """Recheck integrity after the actual setup/call outcome boundary."""

            yield
            assert_canonical_integrity()

    class CanonicalInstallationSeal:
        """Persistent outcome guard that survives policy/guard removal."""

        @pytest.hookimpl(hookwrapper=True, tryfirst=True)
        def pytest_runtest_makereport(
            self,
            item: pytest.Item,
            call: pytest.CallInfo[Any],
        ):
            """Recheck canonical authority after every setup/call outcome."""

            yield
            assert_canonical_integrity()

    canonical_plugin = CanonicalBrowserJourneyPlugin(page_type=page_type)
    canonical_type = CanonicalBrowserJourneyPlugin
    expected_trust_function = canonical_type.__dict__["_is_trusted_remote_selection"]
    integrity_guard = CanonicalPolicyIntegrityGuard()
    installation_seal = CanonicalInstallationSeal()
    installation_seal_type = CanonicalInstallationSeal
    try:
        pluginmanager.register(installation_seal, seal_name)
        pluginmanager.register(canonical_plugin, canonical_name)
        pluginmanager.register(integrity_guard, guard_name)
    except BaseException:
        pluginmanager.unregister(canonical_plugin)
        pluginmanager.unregister(integrity_guard)
        pluginmanager.unregister(installation_seal)
        raise
    expected_policy_hooks = registered_hooks(canonical_plugin, policy_hook_names)
    expected_guard_hooks = registered_hooks(integrity_guard, guard_hook_names)
    expected_seal_hooks = registered_hooks(installation_seal, seal_hook_names)

    def permanent_authority(event: str, args: tuple[Any, ...]) -> None:
        """Retain startup authority outside every mutable pytest surface.

        Python exposes no API to enumerate or remove hooks installed with
        ``sys.addaudithook``.  This closure is handed directly to that
        process-lifetime registry and is never retained by this module, the
        pytest config, a stash, or a registered plugin.
        """

        if event == "asl.browser_journey_policy.register.v1":
            attempted_config, attempted_pluginmanager = args
            if attempted_config is config or attempted_pluginmanager is pluginmanager:
                raise pytest.UsageError(
                    "Playwright browser journey policy integrity failed: canonical policy "
                    "installation was attempted more than once."
                )
            return
        if event == "asl.browser_journey_policy.verify.v1":
            (attempted_config,) = args
            if attempted_config is config:
                assert_canonical_integrity()

    runtime_sys.addaudithook(permanent_authority)
    authority_dispatch("asl.browser_journey_policy.verify.v1", config)
