from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def is_cgroup2() -> bool:
    """Return True if the host appears to have cgroup v2 mounted."""
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
    (cg.path / "cgroup.procs").write_text(str(pid) + "\n")


def add_self(cg: CGroup) -> None:
    add_pid(cg, os.getpid())


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
