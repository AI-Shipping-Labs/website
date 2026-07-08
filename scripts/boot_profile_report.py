#!/usr/bin/env python3
"""Parse and pretty-print ``BOOT_TIMING`` output from the boot-profiling harness.

Pure Python: NO Docker, NO Django, NO third-party imports. Every function here
is a deterministic transform over captured container stdout, so the parse /
min-median / Logfire-delta logic is unit-testable in CI without Docker (see
``tests/test_boot_profile_report.py``). ``scripts/boot_profile.sh`` captures
each constrained boot's stdout to a file and hands the files to this script.

Input lines look exactly like the ones emitted by ``scripts/entrypoint_init.py``::

    BOOT_TIMING phase=django_setup seconds=1.234
    BOOT_TIMING phase=migrate seconds=0.456
    ...
    BOOT_TIMING phase=total seconds=2.100

Any line that is not a well-formed ``BOOT_TIMING`` line is ignored, so ordinary
boot log noise (``Apply database migrations``, gunicorn banners, tracebacks)
does not break parsing.
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
from collections import OrderedDict
from pathlib import Path

# Matches a single instrumented boot line. ``seconds`` is emitted as
# ``{:.3f}`` by entrypoint_init, but we accept any non-negative float so the
# parser stays robust to formatting changes in the (unmodified) boot path.
_BOOT_TIMING_RE = re.compile(
    r"^BOOT_TIMING\s+phase=(?P<phase>\S+)\s+seconds=(?P<seconds>\d+(?:\.\d+)?)\s*$"
)

# The faithfulness caveat, echoed by the harness and documented in
# _docs/boot-profiling.md. Kept here as the single source of truth so the
# printed banner and the doc cannot drift.
CAVEAT_LINES = (
    "Local Postgres latency << cross-AZ RDS: migrate/check read optimistically low.",
    "Faithful for CPU-bound levers (Logfire import, app import, worker count) and",
    "RELATIVE before/after; NOT a substitute for the RDS-bound migrate lever (2A) —",
    "confirm 2A on real dev (#1142). ECR pull + Fargate scheduling are not reproduced;",
    "--cpus approximates but is not identical to Fargate vCPU. Trust RELATIVE numbers.",
)


def parse_boot_timing(text: str) -> "OrderedDict[str, float]":
    """Parse one boot's stdout blob into an ordered ``{phase: seconds}`` map.

    Order of first appearance is preserved (django_setup, migrate, check,
    setup_schedules, total). Malformed or non-``BOOT_TIMING`` lines are
    silently skipped. If a phase appears twice, the last value wins.
    """
    phases: "OrderedDict[str, float]" = OrderedDict()
    for line in text.splitlines():
        match = _BOOT_TIMING_RE.match(line.strip())
        if match is None:
            continue
        phases[match.group("phase")] = float(match.group("seconds"))
    return phases


def aggregate_phases(captures: list[dict]) -> "OrderedDict[str, dict]":
    """Aggregate a list of per-boot ``{phase: seconds}`` dicts.

    Returns ``{phase: {"min": float, "median": float, "n": int}}`` preserving
    the order in which phases first appear across the captures. Phases missing
    from some captures are aggregated over the captures that do have them.
    """
    order: list[str] = []
    values: dict[str, list[float]] = {}
    for capture in captures:
        for phase, seconds in capture.items():
            if phase not in values:
                values[phase] = []
                order.append(phase)
            values[phase].append(seconds)

    result: "OrderedDict[str, dict]" = OrderedDict()
    for phase in order:
        vals = values[phase]
        result[phase] = {
            "min": min(vals),
            "median": statistics.median(vals),
            "n": len(vals),
        }
    return result


def compute_logfire_delta(
    off_captures: list[dict],
    on_captures: list[dict],
    phase: str = "django_setup",
) -> dict:
    """Compute the Logfire off-vs-on median delta for a single phase.

    Returns ``{"phase", "off_median", "on_median", "delta"}``. A median is
    ``None`` when no capture on that side recorded the phase, and ``delta`` is
    ``None`` unless both medians are present. A positive delta is the Logfire
    import/configure tax paid inside ``django_setup`` (settles #1141 Phase 2B).
    """
    off_vals = [c[phase] for c in off_captures if phase in c]
    on_vals = [c[phase] for c in on_captures if phase in c]
    off_median = statistics.median(off_vals) if off_vals else None
    on_median = statistics.median(on_vals) if on_vals else None
    delta = None
    if off_median is not None and on_median is not None:
        delta = on_median - off_median
    return {
        "phase": phase,
        "off_median": off_median,
        "on_median": on_median,
        "delta": delta,
    }


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def render_single_capture_table(phases: dict, title: str) -> str:
    """Render one boot's ``phase | seconds`` table."""
    lines = [title, "-" * len(title), f"{'phase':<18} {'seconds':>10}"]
    for phase, seconds in phases.items():
        lines.append(f"{phase:<18} {seconds:>10.3f}")
    return "\n".join(lines)


def render_aggregate_table(aggregate: dict, title: str) -> str:
    """Render the per-phase ``min | median`` table over N warm iterations."""
    lines = [title, "-" * len(title), f"{'phase':<18} {'min':>10} {'median':>10} {'n':>4}"]
    for phase, stats in aggregate.items():
        lines.append(
            f"{phase:<18} {stats['min']:>10.3f} {stats['median']:>10.3f} {stats['n']:>4}"
        )
    return "\n".join(lines)


def render_logfire_delta_table(delta: dict) -> str:
    """Render the Logfire off-vs-on ``django_setup`` comparison table."""
    title = "Logfire off vs on (settles #1141 Phase 2B)"
    lines = [
        title,
        "-" * len(title),
        f"{'phase':<18} {'off median':>12} {'on median':>12} {'delta':>10}",
        f"{delta['phase']:<18} {_fmt(delta['off_median']):>12} "
        f"{_fmt(delta['on_median']):>12} {_fmt(delta['delta']):>10}",
    ]
    if delta["delta"] is not None:
        sign = "tax" if delta["delta"] > 0 else "no tax / faster"
        lines.append(f"=> Logfire {sign}: {_fmt(delta['delta'])}s added to django_setup")
    return "\n".join(lines)


def render_caveats() -> str:
    """Render the faithfulness caveat banner echoed alongside the report."""
    title = "Faithfulness caveats"
    return "\n".join([title, "-" * len(title), *CAVEAT_LINES])


def _read_captures(paths: list[str]) -> list[dict]:
    return [parse_boot_timing(Path(p).read_text(encoding="utf-8")) for p in paths]


def build_report(
    warm_off: list[dict],
    warm_on: list[dict],
    cold_off: dict | None = None,
    cold_on: dict | None = None,
    delta_phase: str = "django_setup",
) -> str:
    """Assemble the full text report from parsed captures. Pure function."""
    sections: list[str] = []

    if cold_off:
        sections.append(render_single_capture_table(cold_off, "Phase A: cold first-migrate boot (Logfire off)"))
    if cold_on:
        sections.append(render_single_capture_table(cold_on, "Phase A: cold first-migrate boot (Logfire on)"))

    if warm_off:
        sections.append(
            render_aggregate_table(
                aggregate_phases(warm_off),
                f"Phase B: warm boot x{len(warm_off)} (Logfire off) — min/median",
            )
        )
    if warm_on:
        sections.append(
            render_aggregate_table(
                aggregate_phases(warm_on),
                f"Phase B: warm boot x{len(warm_on)} (Logfire on) — min/median",
            )
        )

    if warm_off and warm_on:
        sections.append(
            render_logfire_delta_table(compute_logfire_delta(warm_off, warm_on, delta_phase))
        )

    sections.append(render_caveats())
    return "\n\n".join(sections)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pretty-print BOOT_TIMING captures.")
    parser.add_argument("--warm-off", nargs="*", default=[], help="Logfire-off warm boot capture files")
    parser.add_argument("--warm-on", nargs="*", default=[], help="Logfire-on warm boot capture files")
    parser.add_argument("--cold-off", default=None, help="Optional Phase A cold-boot capture (Logfire off)")
    parser.add_argument("--cold-on", default=None, help="Optional Phase A cold-boot capture (Logfire on)")
    args = parser.parse_args(argv)

    if not args.warm_off and not args.warm_on and not args.cold_off and not args.cold_on:
        parser.error("no capture files given")

    report = build_report(
        warm_off=_read_captures(args.warm_off),
        warm_on=_read_captures(args.warm_on),
        cold_off=parse_boot_timing(Path(args.cold_off).read_text(encoding="utf-8")) if args.cold_off else None,
        cold_on=parse_boot_timing(Path(args.cold_on).read_text(encoding="utf-8")) if args.cold_on else None,
    )
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
