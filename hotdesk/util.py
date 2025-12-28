from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from typing import Sequence

@dataclass
class CmdResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str

def shquote(argv: Sequence[str]) -> str:
    return " ".join(shlex.quote(a) for a in argv)

def run(argv: Sequence[str], *, check: bool = False, capture: bool = True, text: bool = True, env: dict[str,str] | None = None) -> CmdResult:
    proc = subprocess.run(
        list(argv),
        check=False,
        capture_output=capture,
        text=text,
        env={**os.environ, **(env or {})},
    )
    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, list(argv), output=proc.stdout, stderr=proc.stderr)
    return CmdResult(list(argv), proc.returncode, proc.stdout or "", proc.stderr or "")

def which(cmd: str) -> str | None:
    from shutil import which as _which
    return _which(cmd)
