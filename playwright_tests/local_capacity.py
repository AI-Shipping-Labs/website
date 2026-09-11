"""Crash-safe aggregate capacity admission for local Playwright runs.

The coordinator is repository-scoped: every worktree resolves the same common
Git directory, while unrelated repositories use different lock directories.
One controller owns all requested slots or none of them.  The kernel owns the
actual leases through ``flock``; JSON in the slot files is diagnostic metadata
only and can never keep a dead process in the queue.
"""

from __future__ import annotations

import atexit
import fcntl
import json
import os
import platform
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from playwright_tests.worktree_guard import (
    _current_command,
    _process_start_id,
    _sanitize_recorded_command,
    current_git_worktree_root,
)

DEFAULT_CAPACITY = 4
DEFAULT_WAIT_SECONDS = 7_200.0
HEADROOM_SAMPLE_SECONDS = 5.0
HEADROOM_REQUIRED_SAMPLES = 3
MAX_CAPACITY = 64
MAX_WAIT_SECONDS = 86_400.0
CAPACITY_ENV_VAR = "PLAYWRIGHT_LOCAL_CAPACITY"
WAIT_ENV_VAR = "PLAYWRIGHT_LOCAL_CAPACITY_WAIT_SECONDS"
COORDINATOR_RELATIVE_PATH = Path("playwright-local-capacity")
GIB = 1024**3


class CapacityConfigurationError(ValueError):
    """Raised when a local capacity environment value is invalid."""


class CapacityAdmissionError(RuntimeError):
    """Raised when the complete requested width cannot be admitted in time."""


@dataclass(frozen=True)
class HostHeadroom:
    """One best-effort Linux host-pressure sample."""

    platform: str
    logical_cpus: int | None
    load_average_1m: float | None
    load_average_5m: float | None
    load_average_15m: float | None
    available_memory_bytes: int | None
    swap_total_bytes: int | None
    swap_used_bytes: int | None
    cpu_pressure: str | None
    memory_pressure: str | None
    memory_full_avg60: float | None

    def diagnostic(self) -> dict:
        return {key: "unavailable" if value is None else value for key, value in asdict(self).items()}

    def admits(self, requested_slots: int) -> tuple[bool, list[str]]:
        """Return whether every available Linux admission metric passes."""
        if self.platform != "Linux":
            return True, ["Linux headroom metrics unavailable on this platform"]

        failures = []
        if self.logical_cpus is not None and self.load_average_1m is not None:
            if self.load_average_1m + requested_slots > self.logical_cpus:
                failures.append(
                    f"load1 {self.load_average_1m:.2f} + request {requested_slots} > CPUs {self.logical_cpus}"
                )
        if self.available_memory_bytes is not None:
            required = 4 * GIB + requested_slots * GIB
            if self.available_memory_bytes < required:
                failures.append(f"available memory {self.available_memory_bytes} < required {required} bytes")
        if self.memory_full_avg60 is not None and self.memory_full_avg60 > 10.0:
            failures.append(f"memory full avg60 {self.memory_full_avg60:.2f}% > 10.00%")
        return not failures, failures


@dataclass(frozen=True)
class CapacityGrant:
    requested_slots: int
    capacity: int
    wait_seconds: float
    common_git_dir: str
    headroom: HostHeadroom


def _parse_bounded_int(name: str, raw: str | None, default: int) -> int:
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise CapacityConfigurationError(f"{name} must be an integer from 1 to {MAX_CAPACITY}; got {raw!r}") from exc
    if not 1 <= value <= MAX_CAPACITY:
        raise CapacityConfigurationError(f"{name} must be an integer from 1 to {MAX_CAPACITY}; got {raw!r}")
    return value


def configured_capacity(environ=None) -> int:
    environ = os.environ if environ is None else environ
    return _parse_bounded_int(CAPACITY_ENV_VAR, environ.get(CAPACITY_ENV_VAR), DEFAULT_CAPACITY)


def configured_wait_seconds(environ=None) -> float:
    environ = os.environ if environ is None else environ
    raw = environ.get(WAIT_ENV_VAR)
    if raw is None or not raw.strip():
        return DEFAULT_WAIT_SECONDS
    try:
        value = float(raw)
    except ValueError as exc:
        raise CapacityConfigurationError(
            f"{WAIT_ENV_VAR} must be greater than zero and at most {MAX_WAIT_SECONDS:g}; got {raw!r}"
        ) from exc
    if not 0 < value <= MAX_WAIT_SECONDS:
        raise CapacityConfigurationError(
            f"{WAIT_ENV_VAR} must be greater than zero and at most {MAX_WAIT_SECONDS:g}; got {raw!r}"
        )
    return value


def common_git_directory(cwd=None) -> Path:
    """Resolve the common Git directory shared by all repository worktrees."""
    root = current_git_worktree_root(cwd=cwd)
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=root,
            capture_output=True,
            check=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CapacityConfigurationError(
            f"Cannot resolve the repository common Git directory from {root}: {exc}"
        ) from exc
    value = result.stdout.strip()
    if not value:
        raise CapacityConfigurationError(f"Git returned an empty common directory for {root}")
    return Path(value).resolve()


def _read_pressure(path: Path) -> tuple[str | None, float | None]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None, None
    rendered = "; ".join(line.strip() for line in lines if line.strip()) or None
    full_avg60 = None
    for line in lines:
        fields = line.split()
        if fields and fields[0] == "full":
            for field in fields[1:]:
                key, separator, value = field.partition("=")
                if key == "avg60" and separator:
                    try:
                        full_avg60 = float(value)
                    except ValueError:
                        pass
    return rendered, full_avg60


def _read_linux_memory(path=Path("/proc/meminfo")) -> tuple[int | None, int | None, int | None]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None, None, None
    values = {}
    for line in lines:
        key, separator, remainder = line.partition(":")
        if not separator:
            continue
        parts = remainder.split()
        if not parts:
            continue
        try:
            values[key] = int(parts[0]) * 1024
        except ValueError:
            continue
    total = values.get("SwapTotal")
    free = values.get("SwapFree")
    used = total - free if total is not None and free is not None else None
    return values.get("MemAvailable"), total, used


def sample_host_headroom() -> HostHeadroom:
    """Collect one host sample, using ``None`` for unavailable evidence."""
    system = platform.system()
    logical_cpus = os.cpu_count()
    try:
        load1, load5, load15 = os.getloadavg()
    except (AttributeError, OSError):
        load1 = load5 = load15 = None

    available = swap_total = swap_used = None
    cpu_pressure = memory_pressure = None
    memory_full_avg60 = None
    if system == "Linux":
        available, swap_total, swap_used = _read_linux_memory()
        cpu_pressure, _unused = _read_pressure(Path("/proc/pressure/cpu"))
        memory_pressure, memory_full_avg60 = _read_pressure(Path("/proc/pressure/memory"))

    return HostHeadroom(
        platform=system,
        logical_cpus=logical_cpus,
        load_average_1m=load1,
        load_average_5m=load5,
        load_average_15m=load15,
        available_memory_bytes=available,
        swap_total_bytes=swap_total,
        swap_used_bytes=swap_used,
        cpu_pressure=cpu_pressure,
        memory_pressure=memory_pressure,
        memory_full_avg60=memory_full_avg60,
    )


def _metadata_from_file(file_obj) -> dict:
    try:
        file_obj.seek(0)
        value = json.load(file_obj)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _validated_holder_metadata(metadata: dict) -> dict | None:
    """Return sanitized live-holder metadata after PID start validation."""
    try:
        pid = int(metadata.get("pid"))
    except (TypeError, ValueError):
        return None
    recorded_start = metadata.get("process_start_id")
    live_start = _process_start_id(pid)
    if not recorded_start or live_start != str(recorded_start):
        return None
    claimed_at = str(metadata.get("claimed_at") or "")
    try:
        claimed_at = datetime.fromisoformat(claimed_at).isoformat()
    except ValueError:
        claimed_at = "unavailable"
    try:
        requested_slots = int(metadata.get("requested_slots"))
        capacity = int(metadata.get("capacity"))
    except (TypeError, ValueError):
        requested_slots = capacity = "unavailable"
    return {
        "pid": pid,
        "process_start_id": str(recorded_start),
        "command": _sanitize_recorded_command(metadata.get("command", "")) or "unavailable",
        "worktree": _sanitize_recorded_command(metadata.get("worktree", "")) or "unavailable",
        "claimed_at": claimed_at,
        "requested_slots": requested_slots,
        "capacity": capacity,
    }


class LocalPlaywrightCapacity:
    """Atomically lease a complete worker width from a repository budget."""

    def __init__(
        self,
        *,
        common_dir: Path,
        worktree_root: Path,
        requested_slots: int,
        capacity: int = DEFAULT_CAPACITY,
        max_wait_seconds: float = DEFAULT_WAIT_SECONDS,
        sample_interval: float = HEADROOM_SAMPLE_SECONDS,
        required_samples: int = HEADROOM_REQUIRED_SAMPLES,
        headroom_sampler=sample_host_headroom,
        monotonic=time.monotonic,
        sleeper=time.sleep,
        output=None,
    ):
        self.common_dir = Path(common_dir).resolve()
        self.worktree_root = Path(worktree_root).resolve()
        self.requested_slots = max(1, int(requested_slots))
        self.capacity = int(capacity)
        self.max_wait_seconds = float(max_wait_seconds)
        self.sample_interval = float(sample_interval)
        self.required_samples = max(1, int(required_samples))
        self.headroom_sampler = headroom_sampler
        self.monotonic = monotonic
        self.sleeper = sleeper
        self.output = output or sys.stderr
        self.directory = self.common_dir / COORDINATOR_RELATIVE_PATH
        self.token = uuid.uuid4().hex
        self._slot_files = []
        self.grant = None

    @classmethod
    def for_current_worktree(cls, requested_slots: int, *, environ=None, **kwargs):
        environ = os.environ if environ is None else environ
        worktree = current_git_worktree_root()
        return cls(
            common_dir=common_git_directory(worktree),
            worktree_root=worktree,
            requested_slots=requested_slots,
            capacity=configured_capacity(environ),
            max_wait_seconds=configured_wait_seconds(environ),
            **kwargs,
        )

    def acquire(self) -> CapacityGrant:
        if self.grant is not None:
            return self.grant
        if self.requested_slots > self.capacity:
            raise CapacityAdmissionError(
                f"Playwright local capacity admission rejected before test collection: requested width "
                f"{self.requested_slots} exceeds repository capacity {self.capacity}. Reduce -n or raise "
                f"{CAPACITY_ENV_VAR} consistently for every repository claimant. Zero tests started."
            )

        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        started = self.monotonic()
        consecutive = 0
        last_sample = None
        holders = []
        reasons = []
        while True:
            elapsed = max(0.0, self.monotonic() - started)
            last_sample = self.headroom_sampler()
            headroom_ok, reasons = last_sample.admits(self.requested_slots)
            consecutive = consecutive + 1 if headroom_ok else 0
            required_samples = self.required_samples if last_sample.platform == "Linux" else 1

            if consecutive >= required_samples:
                acquired, holders = self._try_acquire_slots()
                if acquired:
                    waited = max(0.0, self.monotonic() - started)
                    self.grant = CapacityGrant(
                        requested_slots=self.requested_slots,
                        capacity=self.capacity,
                        wait_seconds=waited,
                        common_git_dir=str(self.common_dir),
                        headroom=last_sample,
                    )
                    atexit.register(self.release)
                    print(
                        "Playwright local capacity granted: "
                        f"requested={self.requested_slots} capacity={self.capacity} waited={waited:.1f}s",
                        file=self.output,
                        flush=True,
                    )
                    return self.grant

            if not holders:
                holders = self._inspect_holders()

            if elapsed >= self.max_wait_seconds:
                holder_text = json.dumps(holders, sort_keys=True) if holders else "unavailable"
                reason_text = "; ".join(reasons) if reasons else "repository slots unavailable"
                raise CapacityAdmissionError(
                    "Playwright local capacity admission expired before test collection: "
                    f"requested={self.requested_slots} capacity={self.capacity} waited={elapsed:.1f}s; "
                    f"headroom={reason_text}; holders={holder_text}. Wait for the listed run to finish or "
                    "rerun with an explicitly reduced -n width. Zero tests started."
                )

            holder_text = json.dumps(holders, sort_keys=True) if holders else "none"
            reason_text = "; ".join(reasons) if reasons else (f"headroom window {consecutive}/{required_samples}")
            print(
                "Playwright local capacity waiting: "
                f"requested={self.requested_slots} capacity={self.capacity} elapsed={elapsed:.1f}s "
                f"reason={reason_text} holders={holder_text}",
                file=self.output,
                flush=True,
            )
            remaining = self.max_wait_seconds - elapsed
            self.sleeper(min(self.sample_interval, max(0.0, remaining)))

    def _try_acquire_slots(self) -> tuple[bool, list[dict]]:
        mutex_path = self.directory / "coordinator.lock"
        with mutex_path.open("a+", encoding="utf-8") as mutex:
            fcntl.flock(mutex.fileno(), fcntl.LOCK_EX)
            candidates = []
            holders = []
            try:
                for index in range(self.capacity):
                    slot = (self.directory / f"slot-{index:02d}.lock").open("a+", encoding="utf-8")
                    try:
                        fcntl.flock(slot.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        holder = _validated_holder_metadata(_metadata_from_file(slot))
                        holder_entry = holder or {"slot": index, "holder": "unavailable or stale metadata"}
                        if holder_entry not in holders:
                            holders.append(holder_entry)
                        slot.close()
                        continue
                    except Exception:
                        slot.close()
                        raise
                    candidates.append(slot)
                    if len(candidates) == self.requested_slots:
                        break

                if len(candidates) != self.requested_slots:
                    for slot in candidates:
                        fcntl.flock(slot.fileno(), fcntl.LOCK_UN)
                        slot.close()
                    return False, holders

                metadata = {
                    "capacity": self.capacity,
                    "claimed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "command": _current_command(),
                    "pid": os.getpid(),
                    "process_start_id": _process_start_id(os.getpid()),
                    "requested_slots": self.requested_slots,
                    "token": self.token,
                    "worktree": str(self.worktree_root),
                }
                for slot in candidates:
                    slot.seek(0)
                    slot.truncate()
                    json.dump(metadata, slot, sort_keys=True)
                    slot.write("\n")
                    slot.flush()
                    os.fsync(slot.fileno())
                self._slot_files = candidates
                return True, holders
            except Exception:
                for slot in candidates:
                    try:
                        fcntl.flock(slot.fileno(), fcntl.LOCK_UN)
                    finally:
                        slot.close()
                raise
            finally:
                fcntl.flock(mutex.fileno(), fcntl.LOCK_UN)

    def _inspect_holders(self) -> list[dict]:
        """Return only metadata backed by a currently held kernel lock."""
        holders = []
        mutex_path = self.directory / "coordinator.lock"
        with mutex_path.open("a+", encoding="utf-8") as mutex:
            fcntl.flock(mutex.fileno(), fcntl.LOCK_EX)
            try:
                for index in range(self.capacity):
                    with (self.directory / f"slot-{index:02d}.lock").open("a+", encoding="utf-8") as slot:
                        try:
                            fcntl.flock(slot.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            holder = _validated_holder_metadata(_metadata_from_file(slot))
                            holder_entry = holder or {"slot": index, "holder": "unavailable or stale metadata"}
                            if holder_entry not in holders:
                                holders.append(holder_entry)
                        else:
                            fcntl.flock(slot.fileno(), fcntl.LOCK_UN)
            finally:
                fcntl.flock(mutex.fileno(), fcntl.LOCK_UN)
        return holders

    def release(self):
        if not self._slot_files:
            self.grant = None
            return
        files, self._slot_files = self._slot_files, []
        self.grant = None
        errors = []
        for slot in files:
            try:
                fcntl.flock(slot.fileno(), fcntl.LOCK_UN)
            except OSError as exc:
                errors.append(f"{Path(slot.name).name}: {type(exc).__name__}")
            finally:
                try:
                    slot.close()
                except OSError as exc:
                    errors.append(f"{Path(slot.name).name} close: {type(exc).__name__}")
        if errors:
            print(
                "Playwright local capacity release warning: " + ", ".join(errors),
                file=self.output,
                flush=True,
            )

    def diagnostic_state(self) -> dict:
        if self.grant is None:
            return {
                "requested_slots": self.requested_slots,
                "capacity": self.capacity,
                "granted": False,
                "wait_seconds": None,
            }
        value = asdict(self.grant)
        value["headroom"] = self.grant.headroom.diagnostic()
        value["granted"] = True
        return value
