from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def is_linux() -> bool:
    """Return True if running on Linux."""
    return sys.platform.startswith("linux")


def is_cgroup2() -> bool:
    """Return True if the host appears to have cgroup v2 mounted."""
    if not is_linux():
        return False
    return Path("/sys/fs/cgroup/cgroup.controllers").exists()


def default_base() -> Path:
    """Default base directory for hotdesk cgroups."""
    env = os.environ.get("HOTDESK_CGROUP_BASE")
    if env:
        return Path(env)
    return Path("/sys/fs/cgroup/hotdesk")


@dataclass(frozen=True)
class CGroup:
    method: str  # currently only "cgroupfs"
    path: Path


def can_manage(base: Path) -> bool:
    """Best-effort check: can we create and write to sub-cgroups under base?"""
    if not is_cgroup2():
        return False
    try:
        base.mkdir(parents=True, exist_ok=True)
        test = base / f".hotdesk_test_{os.getpid()}"
        test.mkdir()
        # Try writing to cgroup.procs in test
        (test / "cgroup.procs").write_text(str(os.getpid()) + "\n")
        # Move ourselves back to parent to allow cleanup (best-effort)
        (base / "cgroup.procs").write_text(str(os.getpid()) + "\n")
        test.rmdir()
        return True
    except Exception:
        return False


def create(name: str, base: Path | None = None) -> CGroup:
    base = base or default_base()
    if not is_cgroup2():
        raise RuntimeError("cgroup v2 not detected (missing /sys/fs/cgroup/cgroup.controllers)")
    path = base / name
    path.mkdir(parents=True, exist_ok=True)
    return CGroup(method="cgroupfs", path=path)


def add_pid(cg: CGroup, pid: int) -> None:
    """Move PID into cgroup."""
    procs_file = cg.path / "cgroup.procs"
    try:
        procs_file.write_text(str(pid) + "\n")
    except PermissionError as e:
        raise PermissionError(
            f"Cannot write to {procs_file}. "
            f"Check: ls -la {procs_file}"
        ) from e
    except OSError as e:
        # Common cgroup v2 errors:
        # - EBUSY: process is in a threaded cgroup
        # - ENOENT: cgroup doesn't exist
        # - EINVAL: invalid operation
        raise OSError(
            f"Cannot move PID {pid} to cgroup {cg.path}: {e}. "
            f"Try: cat /proc/{pid}/cgroup"
        ) from e


def add_self(cg: CGroup) -> None:
    add_pid(cg, os.getpid())


def check_cgroup_writable(cg: CGroup) -> tuple[bool, str]:
    """Check if we can write to the cgroup. Returns (success, error_message)."""
    procs_file = cg.path / "cgroup.procs"
    
    if not cg.path.exists():
        return False, f"cgroup path does not exist: {cg.path}"
    
    if not procs_file.exists():
        return False, f"cgroup.procs not found: {procs_file}"
    
    if not os.access(procs_file, os.W_OK):
        return False, f"cgroup.procs not writable: {procs_file}"
    
    return True, ""


def list_pids(cg: CGroup) -> list[int]:
    try:
        txt = (cg.path / "cgroup.procs").read_text().strip()
        if not txt:
            return []
        return [int(x) for x in txt.splitlines() if x.strip().isdigit()]
    except Exception:
        return []


def kill(cg: CGroup, sig: int, *, exclude: set[int] | None = None) -> int:
    """Best-effort: signal all PIDs in the cgroup."""
    exclude = exclude or set()
    count = 0
    for pid in list_pids(cg):
        if pid in exclude:
            continue
        try:
            os.kill(pid, sig)
            count += 1
        except Exception:
            pass
    return count


def try_remove(cg: CGroup) -> bool:
    try:
        cg.path.rmdir()
        return True
    except Exception:
        return False
