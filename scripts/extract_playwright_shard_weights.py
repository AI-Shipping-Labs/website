#!/usr/bin/env python3
"""Rebuild the committed Playwright file weights from pinned Actions logs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

try:
    from scripts.playwright_shard_plan import canonical_weights_digest
except ModuleNotFoundError:  # Direct `python scripts/...` execution.
    from playwright_shard_plan import canonical_weights_digest

LOG_LINE = re.compile(r"\t\ufeff?(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z) (.*)$")
NODE_RESULT = re.compile(
    r"^(playwright_tests/test_[^:]+\.py)::.+\s+"
    r"(PASSED|SKIPPED|XFAIL|XPASS)(?:\s|\[)"
)


def run_text(arguments: list[str]) -> str:
    return subprocess.check_output(arguments, text=True)


def canonical_inventory_digest(inventory: list[str]) -> str:
    return hashlib.sha256("".join(f"{path}\n" for path in inventory).encode()).hexdigest()


def inventory_at_sha(sha: str) -> list[str]:
    output = run_text(
        ["git", "ls-tree", "-r", "--name-only", sha, "--", "playwright_tests"]
    )
    return sorted(
        path
        for path in output.splitlines()
        if path.startswith("playwright_tests/test_") and path.endswith(".py")
    )


def parse_job_log(log: str) -> tuple[dict[str, int], int]:
    collection_finished = None
    events: list[tuple[datetime, str, int]] = []

    for raw_line in log.splitlines():
        match = LOG_LINE.search(raw_line)
        if not match:
            continue
        timestamp = datetime.fromisoformat(match.group(1).replace("Z", "+00:00"))
        message = match.group(2)
        if "collecting ... collected " in message:
            collection_finished = timestamp
            continue

        node = NODE_RESULT.match(message)
        if node and collection_finished is not None:
            previous = events[-1][0] if events else collection_finished
            elapsed_ms = max(1, round((timestamp - previous).total_seconds() * 1000))
            events.append((timestamp, node.group(1), elapsed_ms))

    if not events:
        raise ValueError("job log contains no completed Playwright nodes")

    later_durations = [elapsed_ms for _, _, elapsed_ms in events[1:]]
    shard_median = round(statistics.median(later_durations))
    later_by_file: dict[str, list[int]] = {}
    for _, filename, elapsed_ms in events[1:]:
        later_by_file.setdefault(filename, []).append(elapsed_ms)

    first_filename = events[0][1]
    first_duration = round(
        statistics.median(later_by_file.get(first_filename, [shard_median]))
    )

    weights: dict[str, int] = {}
    for position, (_, filename, elapsed_ms) in enumerate(events):
        duration = first_duration if position == 0 else elapsed_ms
        weights[filename] = weights.get(filename, 0) + duration
    return weights, len(events)


def fetch_job_log(item: tuple[int, int]) -> tuple[int, int, str]:
    run_id, job_id = item
    log = run_text(
        [
            "gh",
            "run",
            "view",
            str(run_id),
            "--repo",
            "AI-Shipping-Labs/website",
            "--job",
            str(job_id),
            "--log",
        ]
    )
    return run_id, job_id, log


def validate_run(source: dict) -> None:
    metadata = json.loads(
        run_text(
            [
                "gh",
                "run",
                "view",
                str(source["run_id"]),
                "--repo",
                "AI-Shipping-Labs/website",
                "--json",
                "headSha,conclusion,jobs",
            ]
        )
    )
    if metadata["headSha"] != source["head_sha"]:
        raise ValueError(f"run {source['run_id']} SHA does not match pinned evidence")
    if metadata["conclusion"] != "success":
        raise ValueError(f"run {source['run_id']} is not green")

    jobs = {job["databaseId"]: job for job in metadata["jobs"]}
    expected_jobs = source["job_ids"]
    if any(job_id not in jobs for job_id in expected_jobs):
        raise ValueError(f"run {source['run_id']} is missing a pinned shard job")
    if any(jobs[job_id]["conclusion"] != "success" for job_id in expected_jobs):
        raise ValueError(f"run {source['run_id']} has a non-green pinned shard job")


def rebuild_manifest(seed: dict) -> dict:
    sources = seed["source_runs"]
    if len(sources) < 3:
        raise ValueError("at least three pinned source runs are required")
    for source in sources:
        validate_run(source)

    inventories = [inventory_at_sha(source["head_sha"]) for source in sources]
    if any(inventory != inventories[0] for inventory in inventories[1:]):
        raise ValueError("source runs do not share an exact Playwright file inventory")
    inventory = inventories[0]

    jobs = [
        (source["run_id"], job_id)
        for source in sources
        for job_id in source["job_ids"]
    ]
    with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as pool:
        logs = list(pool.map(fetch_job_log, jobs))

    weights_by_run = {source["run_id"]: {} for source in sources}
    nodes_by_run = {source["run_id"]: 0 for source in sources}
    for run_id, _, log in logs:
        job_weights, node_count = parse_job_log(log)
        nodes_by_run[run_id] += node_count
        for filename, weight_ms in job_weights.items():
            weights_by_run[run_id][filename] = (
                weights_by_run[run_id].get(filename, 0) + weight_ms
            )

    for source in sources:
        run_id = source["run_id"]
        if nodes_by_run[run_id] != source["expected_selected_nodes"]:
            raise ValueError(
                f"run {run_id} yielded {nodes_by_run[run_id]} nodes, expected "
                f"{source['expected_selected_nodes']}"
            )
        complete_weights = {
            filename: weights_by_run[run_id].get(filename, 0) for filename in inventory
        }
        source["observed_selected_nodes"] = nodes_by_run[run_id]
        source["file_weights_sha256"] = canonical_weights_digest(complete_weights)

    output = dict(seed)
    output["inventory_sha256"] = canonical_inventory_digest(inventory)
    output["file_weights_ms"] = {
        filename: [
            weights_by_run[source["run_id"]].get(filename, 0) for source in sources
        ]
        for filename in inventory
    }
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    seed = json.loads(args.manifest.read_text())
    rebuilt = rebuild_manifest(seed)
    args.output.write_text(json.dumps(rebuilt, indent=2, sort_keys=True) + "\n")
    print(
        f"Wrote {len(rebuilt['file_weights_ms'])} file weights from "
        f"{len(rebuilt['source_runs'])} pinned green runs to {args.output}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
