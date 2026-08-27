#!/usr/bin/env python3
"""Build deterministic Playwright file shards from committed measurements."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ShardPlan:
    files: tuple[tuple[str, ...], ...]
    loads_ms: tuple[int, ...]
    unknown_files: tuple[str, ...]
    unknown_weight_ms: int


def canonical_weights_digest(weights: dict[str, int]) -> str:
    payload = json.dumps(weights, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def measured_weights_from_manifest(manifest: dict) -> dict[str, int]:
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported Playwright shard-weight schema")

    sources = manifest.get("source_runs")
    if not isinstance(sources, list) or len(sources) < 3:
        raise ValueError("at least three source runs are required")

    raw_weights = manifest.get("file_weights_ms")
    if not isinstance(raw_weights, dict) or not raw_weights:
        raise ValueError("file_weights_ms must be a non-empty mapping")

    source_count = len(sources)
    measured: dict[str, int] = {}
    for filename, samples in raw_weights.items():
        if not isinstance(filename, str) or not filename.startswith("playwright_tests/test_"):
            raise ValueError(f"invalid Playwright test path: {filename!r}")
        if not isinstance(samples, list) or len(samples) != source_count:
            raise ValueError(f"{filename} must have one weight per source run")
        if any(not isinstance(sample, int) or sample < 0 for sample in samples):
            raise ValueError(f"{filename} weights must be non-negative integer milliseconds")
        measured[filename] = sorted(samples)[source_count // 2]

    return measured


def load_measured_weights(path: Path) -> tuple[dict[str, int], dict]:
    manifest = json.loads(path.read_text())
    return measured_weights_from_manifest(manifest), manifest


def round_robin_loads(
    inventory: list[str],
    measured: dict[str, int],
    shard_count: int,
    unknown_weight_ms: int,
) -> tuple[int, ...]:
    loads = [0] * shard_count
    for position, filename in enumerate(sorted(inventory)):
        loads[position % shard_count] += measured.get(filename, unknown_weight_ms)
    return tuple(loads)


def build_shard_plan(inventory: list[str], measured: dict[str, int], shard_count: int) -> ShardPlan:
    if shard_count < 1:
        raise ValueError("shard_count must be positive")
    normalized_inventory = sorted(inventory)
    if len(normalized_inventory) != len(set(normalized_inventory)):
        raise ValueError("Playwright inventory contains duplicate paths")
    if not measured:
        raise ValueError("measured weights cannot be empty")
    missing_measured = sorted(set(measured) - set(normalized_inventory))
    if missing_measured:
        raise ValueError("measured Playwright paths missing from current inventory: " + ", ".join(missing_measured))

    unknown_weight_ms = max(measured.values())
    unknown_files = tuple(path for path in normalized_inventory if path not in measured)
    ordered = sorted(
        normalized_inventory,
        key=lambda path: (-measured.get(path, unknown_weight_ms), path),
    )

    shards: list[list[str]] = [[] for _ in range(shard_count)]
    loads = [0] * shard_count
    for filename in ordered:
        shard_index = min(
            range(shard_count),
            key=lambda index: (loads[index], len(shards[index]), index),
        )
        shards[shard_index].append(filename)
        loads[shard_index] += measured.get(filename, unknown_weight_ms)

    assigned = [filename for shard in shards for filename in shard]
    if sorted(assigned) != normalized_inventory or len(assigned) != len(set(assigned)):
        raise RuntimeError("generated shards do not form an exact inventory partition")

    return ShardPlan(
        files=tuple(tuple(sorted(shard)) for shard in shards),
        loads_ms=tuple(loads),
        unknown_files=unknown_files,
        unknown_weight_ms=unknown_weight_ms,
    )


def inventory_paths(directory: Path) -> list[str]:
    return sorted(path.as_posix() for path in directory.glob("test_*.py") if path.is_file())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, default=Path("playwright_tests"))
    parser.add_argument("--shards", type=int, required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if not 0 <= args.shard_index < args.shards:
        parser.error("--shard-index must be in [0, --shards)")

    measured, manifest = load_measured_weights(args.weights)
    inventory = inventory_paths(args.inventory)
    plan = build_shard_plan(inventory, measured, args.shards)
    args.output.write_text("".join(f"{filename}\n" for filename in plan.files[args.shard_index]))

    baseline = round_robin_loads(inventory, measured, args.shards, plan.unknown_weight_ms)
    print(
        "Playwright measured shard plan: "
        f"inventory={len(inventory)} weighted={len(inventory) - len(plan.unknown_files)} "
        f"unknown={len(plan.unknown_files)} sources={len(manifest['source_runs'])}"
    )
    print(f"Predicted measured loads (ms): {list(plan.loads_ms)}")
    print(f"Index round-robin loads (ms): {list(baseline)}")
    if plan.unknown_files:
        print(
            "Unknown Playwright files are included with conservative fallback "
            f"weight {plan.unknown_weight_ms}ms; the committed manifest remains "
            f"unchanged: {list(plan.unknown_files)}"
        )
    print(f"Selected {len(plan.files[args.shard_index])} files for shard {args.shard_index + 1}/{args.shards}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
