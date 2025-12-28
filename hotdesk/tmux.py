from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .util import run, which

@dataclass
class PaneInfo:
    session_name: str
    window_index: int
    pane_index: int
    pane_pid: int
    current_command: str
    title: str

def require_tmux() -> None:
    if which("tmux") is None:
        raise RuntimeError("tmux not found. Please install tmux first.")

def has_session(server: str, session: str) -> bool:
    require_tmux()
    res = run(["tmux", "-L", server, "has-session", "-t", session], check=False)
    return res.returncode == 0

def list_panes(server: str, session: str | None = None) -> list[PaneInfo]:
    require_tmux()
    target = session or ""
    fmt = "#{session_name}\t#{window_index}\t#{pane_index}\t#{pane_pid}\t#{pane_current_command}\t#{pane_title}"
    argv = ["tmux", "-L", server, "list-panes", "-a", "-F", fmt]
    if target:
        argv = ["tmux", "-L", server, "list-panes", "-t", target, "-a", "-F", fmt]
    res = run(argv, check=False)
    if res.returncode != 0:
        return []
    out: list[PaneInfo] = []
    for line in res.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 6:
            continue
        try:
            out.append(
                PaneInfo(
                    session_name=parts[0],
                    window_index=int(parts[1]),
                    pane_index=int(parts[2]),
                    pane_pid=int(parts[3]),
                    current_command=parts[4],
                    title=parts[5],
                )
            )
        except Exception:
            continue
    return out

def new_or_attach(server: str, session: str, workdir: str | None = None) -> None:
    require_tmux()
    argv = ["tmux", "-L", server, "new", "-A", "-s", session]
    if workdir:
        argv += ["-c", workdir]
    import os
    os.execvp(argv[0], argv)  # replace current process

def kill_session(server: str, session: str) -> bool:
    require_tmux()
    res = run(["tmux", "-L", server, "kill-session", "-t", session], check=False)
    return res.returncode == 0
