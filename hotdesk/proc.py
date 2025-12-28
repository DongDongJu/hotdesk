from __future__ import annotations

import os
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

@dataclass
class ProcInfo:
    pid: int
    ppid: int
    cmdline: str
    exe: str | None = None
    cwd: str | None = None

def _read_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        raw = raw.replace(b"\x00", b" ").strip()
        return raw.decode(errors="ignore") or "(empty)"
    except Exception:
        return "(unknown)"

def _read_exe(pid: int) -> str | None:
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except Exception:
        return None

def _read_cwd(pid: int) -> str | None:
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except Exception:
        return None

def build_ppid_map() -> tuple[dict[int,int], dict[int,ProcInfo]]:
    ppid: dict[int,int] = {}
    infos: dict[int,ProcInfo] = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        pid = int(d)
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            parts = stat.split()
            parent = int(parts[3])
            ppid[pid] = parent
            infos[pid] = ProcInfo(pid=pid, ppid=parent, cmdline=_read_cmdline(pid), exe=_read_exe(pid), cwd=_read_cwd(pid))
        except Exception:
            continue
    return ppid, infos

def descendants(roots: Iterable[int], ppid_map: dict[int,int]) -> set[int]:
    children: dict[int, list[int]] = defaultdict(list)
    for pid, parent in ppid_map.items():
        children[parent].append(pid)

    seen: set[int] = set()
    q: deque[int] = deque()
    for r in roots:
        if r not in seen:
            seen.add(r)
            q.append(r)

    while q:
        cur = q.popleft()
        for ch in children.get(cur, []):
            if ch not in seen:
                seen.add(ch)
                q.append(ch)
    return seen

def summarize_pids(pids: Iterable[int], infos: dict[int,ProcInfo], max_items: int = 8) -> list[str]:
    # Heuristic: group by first token of cmdline
    buckets: dict[str, int] = defaultdict(int)
    for pid in pids:
        cmd = infos.get(pid).cmdline if pid in infos else ""
        head = cmd.strip().split(" ", 1)[0] if cmd.strip() else "(empty)"
        buckets[head] += 1
    items = sorted(buckets.items(), key=lambda kv: (-kv[1], kv[0]))[:max_items]
    return [f"{k} x{v}" for k,v in items]
