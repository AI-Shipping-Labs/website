"""Collect and ratchet exact Playwright pytest owner IDs.

The collector is a pytest plugin as well as a CLI. It intentionally derives
owners only from final ``pytest.Function`` items; it never reads Python source
or infers what a test does.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

try:
    from scripts.browser_journey_policy import (
        collected_item_callable,
        is_browser_journey_item,
    )
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from browser_journey_policy import (  # type: ignore[no-redef]
        collected_item_callable,
        is_browser_journey_item,
    )

try:
    from scripts.playwright_owner_inventory_ceilings import (
        LEGACY_DECLARED_BROWSER_CEILING,
        LEGACY_NON_BROWSER_CEILING,
    )
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from playwright_owner_inventory_ceilings import (  # type: ignore[no-redef]
        LEGACY_DECLARED_BROWSER_CEILING,
        LEGACY_NON_BROWSER_CEILING,
    )

ROOT = Path(__file__).resolve().parents[1]
PLAYWRIGHT_DIR = ROOT / "playwright_tests"
LIVE_MANIFEST = ROOT / "tests" / "playwright_owner_inventory_live.json"
REPORT_OPTION = "--playwright-owner-report"
SCHEMA_VERSION = 1
EXPECTED_CEILING_COUNTS = {
    "LEGACY_DECLARED_BROWSER": 2258,
    "LEGACY_NON_BROWSER": 81,
}
EXPECTED_CEILING_DIGESTS = {
    "LEGACY_DECLARED_BROWSER": "c34f27c1d9a593344b2b39190943bc9733f499c70dc2ca3dc682f1b15fbc3f70",
    "LEGACY_NON_BROWSER": "e403a9947e394a0fecb9e89ce057fd4e70143017bd8395a52753867bd5fe86f0",
}


@dataclass(frozen=True)
class CollectedOwner:
    owner_id: str
    callable_identity: int
    nodeid: str
    parametrized: bool
    parameter_id: str | None
    declared: bool


@dataclass(frozen=True)
class CollectedInventory:
    owners: list[str]
    declared_owners: set[str]
    item_count: int


class InventoryError(ValueError):
    """A collected item or live inventory violates the finite contract."""


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("playwright-owner-inventory")
    group.addoption(REPORT_OPTION, action="store", default=None)


@pytest.hookimpl(trylast=True)
def pytest_collection_finish(session: pytest.Session) -> None:
    report_path = session.config.getoption(REPORT_OPTION)
    if not report_path:
        return

    try:
        owners = normalize_items(session.items, Path(str(session.config.rootpath)))
        payload = {
            "schema_version": SCHEMA_VERSION,
            "owners": [owner.owner_id for owner in owners],
            "collected_item_count": len(session.items),
            "declared_owners": [owner.owner_id for owner in owners if owner.declared],
        }
    except InventoryError as exc:
        payload = {"schema_version": SCHEMA_VERSION, "error": str(exc)}

    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def normalize_items(items: list[pytest.Item], root: Path) -> list[CollectedOwner]:
    """Normalize final pytest items to exact path/class/function owners."""
    grouped: dict[str, list[CollectedOwner]] = defaultdict(list)

    for item in items:
        normalized = normalize_item(item, root)
        grouped[normalized.owner_id].append(normalized)

    for owner_id, variants in sorted(grouped.items()):
        identities = {variant.callable_identity for variant in variants}
        if len(identities) != 1:
            nodeids = ", ".join(sorted(variant.nodeid for variant in variants))
            raise InventoryError(
                f"duplicate owner identity `{owner_id}` came from distinct final callables: "
                f"{nodeids}. Give every callable a unique pytest owner ID."
            )
        if len(variants) == 1:
            continue
        if not all(variant.parametrized for variant in variants):
            nodeids = ", ".join(sorted(variant.nodeid for variant in variants))
            raise InventoryError(
                f"duplicate owner identity `{owner_id}` is not one parametrized callable: "
                f"{nodeids}. Give every callable a unique pytest owner ID."
            )
        parameter_ids = [variant.parameter_id for variant in variants]
        if len(set(parameter_ids)) != len(parameter_ids):
            raise InventoryError(
                f"ambiguous parametrization for `{owner_id}` has duplicate final parameter IDs. "
                "Give every collected variant a unique pytest parameter ID."
            )

    return [grouped[owner_id][0] for owner_id in sorted(grouped)]


def normalize_item(item: pytest.Item, root: Path) -> CollectedOwner:
    if not isinstance(item, pytest.Function):
        raise InventoryError(
            f"unsupported collected item `{item.nodeid}` ({type(item).__name__}); "
            "Playwright inventory owners must be pytest.Function items."
        )

    try:
        relative_path = Path(item.path).resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise InventoryError(
            f"collected item `{item.nodeid}` is outside the inventory root `{root}`; "
            "move it under playwright_tests/test_*.py."
        ) from exc
    path = Path(relative_path)
    if path.parent.as_posix() != "playwright_tests" or not (path.name.startswith("test_") and path.suffix == ".py"):
        raise InventoryError(
            f"unsupported collected item `{item.nodeid}`; expected an exact owner under playwright_tests/test_*.py."
        )

    original_name = getattr(item, "originalname", None)
    if not isinstance(original_name, str) or not original_name.startswith("test_"):
        raise InventoryError(
            f"ambiguous owner normalization for collected item `{item.nodeid}`: "
            f"pytest originalname is {original_name!r}. Use a test_* pytest.Function owner."
        )

    class_names = [node.name for node in item.listchain() if isinstance(node, pytest.Class)]
    if any(not name or "::" in name or "[" in name for name in class_names):
        raise InventoryError(
            f"unsupported class owner schema for collected item `{item.nodeid}`; use ordinary named pytest classes."
        )

    owner_id = "::".join([relative_path, *class_names, original_name])
    if item.nodeid != owner_id and not item.nodeid.startswith(f"{owner_id}["):
        raise InventoryError(
            f"ambiguous owner normalization for collected item `{item.nodeid}`; "
            f"runtime metadata resolves to `{owner_id}`. Rename the collected item explicitly."
        )

    callable_owner = collected_item_callable(item)
    code = getattr(callable_owner, "__code__", None)
    if code is None:
        raise InventoryError(
            f"unsupported callable for collected item `{item.nodeid}`; "
            "the final pytest function is not tied to its exact module/class owner context."
        )
    callable_identity = id(callable_owner)
    callspec = getattr(item, "callspec", None)
    return CollectedOwner(
        owner_id=owner_id,
        callable_identity=callable_identity,
        nodeid=item.nodeid,
        parametrized=callspec is not None,
        parameter_id=str(callspec.id) if callspec is not None else None,
        declared=is_browser_journey_item(item),
    )


def _ceiling_digest(owners: set[str] | frozenset[str]) -> str:
    return hashlib.sha256("\n".join(sorted(owners)).encode()).hexdigest()


def collect_owner_ids(
    root: Path = ROOT,
    playwright_dir: Path | None = None,
    *,
    extra_env: dict[str, str] | None = None,
) -> tuple[list[str], int]:
    """Run normal pytest collection in a subprocess and return final owners."""
    inventory = collect_inventory(root, playwright_dir, extra_env=extra_env)
    return inventory.owners, inventory.item_count


def collect_inventory(
    root: Path = ROOT,
    playwright_dir: Path | None = None,
    *,
    extra_env: dict[str, str] | None = None,
) -> CollectedInventory:
    """Run normal collection and return owners plus exact declarations."""
    root = root.resolve()
    test_dir = (playwright_dir or root / "playwright_tests").resolve()
    paths = sorted(test_dir.glob("test_*.py"))
    if not paths:
        raise InventoryError(f"no Playwright test modules found under `{test_dir}`")

    scratch = root / ".tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    report = scratch / f"playwright-owner-report-{os.getpid()}-{uuid.uuid4().hex}.json"
    command = [
        sys.executable,
        "-m",
        "pytest",
        "--collect-only",
        "-q",
        "-p",
        "scripts.playwright_owner_inventory",
        REPORT_OPTION,
        str(report),
        *(str(path.relative_to(root)) for path in paths),
    ]
    env = os.environ.copy()
    pythonpath = [str(ROOT)]
    if env.get("PYTHONPATH"):
        pythonpath.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath)
    if extra_env:
        env.update(extra_env)

    try:
        result = subprocess.run(
            command,
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if not report.exists():
            output = (result.stdout + result.stderr).strip()
            raise InventoryError(
                f"pytest collection did not produce an owner report. Exit {result.returncode}.\n{output}"
            )
        payload = json.loads(report.read_text(encoding="utf-8"))
    finally:
        report.unlink(missing_ok=True)

    if "error" in payload:
        raise InventoryError(str(payload["error"]))
    if result.returncode != 0:
        output = (result.stdout + result.stderr).strip()
        raise InventoryError(f"pytest collection failed with exit {result.returncode}.\n{output}")

    owners = payload.get("owners")
    if not isinstance(owners, list) or not all(isinstance(owner, str) for owner in owners):
        raise InventoryError("pytest owner report has an invalid `owners` payload")
    collected_paths = {owner.split("::", 1)[0] for owner in owners}
    source_paths = {path.relative_to(root).as_posix() for path in paths}
    missing_modules = sorted(source_paths - collected_paths)
    extra_modules = sorted(collected_paths - source_paths)
    if missing_modules or extra_modules:
        details = []
        if missing_modules:
            details.append("uncollected modules: " + ", ".join(missing_modules))
        if extra_modules:
            details.append("unexpected modules: " + ", ".join(extra_modules))
        raise InventoryError(
            "Playwright source/collection drift: " + "; ".join(details) + ". "
            "Restore normal pytest collection or update the exact source owner."
        )
    declared_owners = payload.get("declared_owners")
    if not isinstance(declared_owners, list) or not all(isinstance(owner, str) for owner in declared_owners):
        raise InventoryError("pytest owner report has an invalid `declared_owners` payload")
    if not set(declared_owners).issubset(owners):
        raise InventoryError("pytest owner report declares an owner absent from `owners`")
    return CollectedInventory(
        owners=owners,
        declared_owners=set(declared_owners),
        item_count=int(payload.get("collected_item_count", 0)),
    )


def load_live_manifest(path: Path = LIVE_MANIFEST) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise InventoryError(f"unsupported live inventory schema in `{path}`; expected {SCHEMA_VERSION}")
    return payload


def validate_inventory(
    collected: set[str],
    live_manifest: dict[str, Any],
    *,
    declared_ceiling: set[str] | frozenset[str] = LEGACY_DECLARED_BROWSER_CEILING,
    non_browser_ceiling: set[str] | frozenset[str] = LEGACY_NON_BROWSER_CEILING,
    expected_ceiling_counts: dict[str, int] = EXPECTED_CEILING_COUNTS,
    expected_ceiling_digests: dict[str, str] = EXPECTED_CEILING_DIGESTS,
    declared_owners: set[str] | frozenset[str] = frozenset(),
) -> list[str]:
    """Return every exact ratchet violation without hiding replacement drift."""
    errors: list[str] = []
    declared_raw = live_manifest.get("LEGACY_DECLARED_BROWSER", [])
    non_browser_raw = live_manifest.get("LEGACY_NON_BROWSER", {})
    if not isinstance(declared_raw, list) or not all(isinstance(v, str) for v in declared_raw):
        return ["LEGACY_DECLARED_BROWSER must be a list of exact owner IDs."]
    if not isinstance(non_browser_raw, dict) or not all(
        isinstance(owner, str) and isinstance(details, dict) for owner, details in non_browser_raw.items()
    ):
        return ["LEGACY_NON_BROWSER must map exact owner IDs to review metadata."]

    if len(declared_raw) != len(set(declared_raw)):
        errors.append("LEGACY_DECLARED_BROWSER contains duplicate owner IDs; keep one exact entry.")
    declared = set(declared_raw)
    non_browser = set(non_browser_raw)
    overlap = sorted(declared & non_browser)
    for owner in overlap:
        errors.append(f"overlap: `{owner}` is in both live manifests. Keep exactly one classification.")

    for owner, details in sorted(non_browser_raw.items()):
        for field in ("category", "reason", "relocation"):
            if not isinstance(details.get(field), str) or not details[field].strip():
                errors.append(f"missing {field}: `{owner}` needs a non-empty `{field}` and relocation action.")

    live = declared | non_browser
    explicit = set(declared_owners)
    for owner in sorted(explicit - collected):
        errors.append(
            f"stale declaration: `{owner}` is declared but not collected. Inspect final pytest "
            "callable identity and remove stale policy data."
        )
    for owner in sorted(explicit & live):
        errors.append(
            f"declared live owner: `{owner}` carries @browser_journey but remains in the legacy "
            "live manifest. Remove only its live entry; leave the immutable ceiling unchanged."
        )
    for owner in sorted(collected - live - explicit):
        errors.append(
            f"new owner: `{owner}` is not in the exact baseline. Do not add it to a legacy "
            "ceiling; decorate the exact final callable with @browser_journey."
        )
    for owner in sorted(live - collected):
        errors.append(
            f"stale live owner: `{owner}` is no longer collected. Remove only its live entry; "
            "leave the immutable ceiling unchanged."
        )

    for label, live_set, ceiling in (
        ("LEGACY_DECLARED_BROWSER", declared, set(declared_ceiling)),
        ("LEGACY_NON_BROWSER", non_browser, set(non_browser_ceiling)),
    ):
        expected_count = expected_ceiling_counts[label]
        expected_digest = expected_ceiling_digests[label]
        actual_digest = _ceiling_digest(set(ceiling))
        if actual_digest != expected_digest:
            errors.append(
                f"immutable ceiling changed: {label} content digest differs from the accepted "
                "baseline. Restore every retired ID and remove every replacement; only live "
                "manifests shrink."
            )
        if len(ceiling) > expected_count:
            additions = sorted(ceiling - live_set)
            exact = ", ".join(f"`{owner}`" for owner in additions) or "an unknown owner"
            errors.append(
                f"ceiling growth: {label} has {len(ceiling)} entries, above immutable "
                f"{expected_count}; added {exact}. Revert the ceiling change."
            )
        elif len(ceiling) < expected_count:
            errors.append(
                f"immutable ceiling changed: {label} has {len(ceiling)} entries, expected "
                f"{expected_count}. Restore retired IDs; only live manifests shrink."
            )
        for owner in sorted(live_set - ceiling):
            errors.append(
                f"outside ceiling: `{owner}` was added to {label}. Revert the legacy growth; "
                "new owners require #1451's explicit declaration."
            )
    return errors


def check_current_inventory() -> tuple[int, int]:
    inventory = collect_inventory()
    errors = validate_inventory(
        set(inventory.owners),
        load_live_manifest(),
        declared_owners=inventory.declared_owners,
    )
    if errors:
        raise InventoryError("Playwright owner inventory failed:\n- " + "\n- ".join(errors))
    return len(inventory.owners), inventory.item_count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "collect"), nargs="?", default="check")
    args = parser.parse_args(argv)
    try:
        inventory = collect_inventory()
        owners = inventory.owners
        item_count = inventory.item_count
        if args.command == "collect":
            print("\n".join(owners))
            print(f"Collected {item_count} items / {len(owners)} owners.", file=sys.stderr)
            return 0
        errors = validate_inventory(
            set(owners),
            load_live_manifest(),
            declared_owners=inventory.declared_owners,
        )
        if errors:
            raise InventoryError("Playwright owner inventory failed:\n- " + "\n- ".join(errors))
    except InventoryError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Playwright owner inventory: {item_count} items / {len(owners)} owners; exact baseline OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
