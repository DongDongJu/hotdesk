from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import fcntl

# --- storage locations ---

DEFAULT_STATE_DIR_CANDIDATES = [
    Path(os.environ.get("HOTDESK_STATE_DIR", "")) if os.environ.get("HOTDESK_STATE_DIR") else None,
    Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "hotdesk",
    Path("/var/tmp/hotdesk"),
    Path.home() / ".hotdesk",
]
DEFAULT_STATE_DIR = next(p for p in DEFAULT_STATE_DIR_CANDIDATES if p is not None)

BOARD_FILE = "board.json"
LOCK_FILE = "board.lock"


def _now_iso() -> str:
    # ISO 8601 with timezone offset if available
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _iso_gt(a: str, b: str) -> bool:
    """String compare for ISO timestamps is usually ok; fallback to length checks."""
    if not a:
        return False
    if not b:
        return True
    return a > b


@dataclass
class DeskState:
    """A 'desk' is a virtual user slot (co-working style) owned by a NAME."""

    name: str
    created_at: str
    updated_at: str

    status: str = "prepared"  # prepared|running|stopped

    prepared_at: str = ""
    started_at: str = ""
    saved_at: str = ""
    stopped_at: str = ""

    note: str = ""

    tmux_server: str = ""
    tmux_session: str = ""
    workdir: str = ""

    cgroup_method: str = ""
    cgroup_path: str = ""

    def is_saved_since_start(self) -> bool:
        return _iso_gt(self.saved_at, self.started_at)


class Board:
    """Shared registry of desks."""

    def __init__(self, state_dir: Path | None = None) -> None:
        self.state_dir = state_dir or DEFAULT_STATE_DIR
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.state_dir / LOCK_FILE
        self.board_path = self.state_dir / BOARD_FILE

    def _load_unlocked(self) -> Dict[str, Any]:
        if not self.board_path.exists():
            return {"version": 1, "desks": {}}
        try:
            return json.loads(self.board_path.read_text(encoding="utf-8"))
        except Exception:
            # if corrupted, don't crash; keep a backup
            bak = self.state_dir / f"board.corrupt.{int(time.time())}.json"
            bak.write_text(self.board_path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
            return {"version": 1, "desks": {}}

    def _save_unlocked(self, data: Dict[str, Any]) -> None:
        tmp = self.state_dir / f".board.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.board_path)

    def upsert(
        self,
        name: str,
        *,
        status: str | None = None,
        note: str | None = None,
        prepared_at: str | None = None,
        started_at: str | None = None,
        saved_at: str | None = None,
        stopped_at: str | None = None,
        tmux_server: str | None = None,
        tmux_session: str | None = None,
        workdir: str | None = None,
        cgroup_method: str | None = None,
        cgroup_path: str | None = None,
    ) -> DeskState:
        self.lock_path.touch(exist_ok=True)
        with open(self.lock_path, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            data = self._load_unlocked()
            desks = data.setdefault("desks", {})
            cur = desks.get(name) or {}

            created_at = cur.get("created_at") or _now_iso()
            updated_at = _now_iso()

            if status is not None:
                cur["status"] = status
            if note is not None:
                cur["note"] = note

            if prepared_at is not None:
                cur["prepared_at"] = prepared_at
            if started_at is not None:
                cur["started_at"] = started_at
            if saved_at is not None:
                cur["saved_at"] = saved_at
            if stopped_at is not None:
                cur["stopped_at"] = stopped_at

            if tmux_server is not None:
                cur["tmux_server"] = tmux_server
            if tmux_session is not None:
                cur["tmux_session"] = tmux_session
            if workdir is not None:
                cur["workdir"] = workdir

            if cgroup_method is not None:
                cur["cgroup_method"] = cgroup_method
            if cgroup_path is not None:
                cur["cgroup_path"] = cgroup_path

            cur["created_at"] = created_at
            cur["updated_at"] = updated_at

            desks[name] = cur
            self._save_unlocked(data)
            fcntl.flock(f, fcntl.LOCK_UN)

        return self.get(name) or DeskState(name=name, created_at=created_at, updated_at=updated_at)

    def get(self, name: str) -> DeskState | None:
        return self.get_all().get(name)

    def get_all(self) -> Dict[str, DeskState]:
        self.lock_path.touch(exist_ok=True)
        with open(self.lock_path, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            data = self._load_unlocked()
            desks = data.get("desks") or {}
            out: Dict[str, DeskState] = {}
            for name, cur in desks.items():
                out[name] = DeskState(
                    name=name,
                    created_at=str(cur.get("created_at", "")),
                    updated_at=str(cur.get("updated_at", "")),
                    status=str(cur.get("status", "prepared")),
                    prepared_at=str(cur.get("prepared_at", "")),
                    started_at=str(cur.get("started_at", "")),
                    saved_at=str(cur.get("saved_at", "")),
                    stopped_at=str(cur.get("stopped_at", "")),
                    note=str(cur.get("note", "")),
                    tmux_server=str(cur.get("tmux_server", "")),
                    tmux_session=str(cur.get("tmux_session", "")),
                    workdir=str(cur.get("workdir", "")),
                    cgroup_method=str(cur.get("cgroup_method", "")),
                    cgroup_path=str(cur.get("cgroup_path", "")),
                )
            fcntl.flock(f, fcntl.LOCK_UN)
        return out

    def remove(self, name: str) -> bool:
        self.lock_path.touch(exist_ok=True)
        with open(self.lock_path, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            data = self._load_unlocked()
            desks = data.get("desks") or {}
            existed = name in desks
            if existed:
                desks.pop(name, None)
                data["desks"] = desks
                self._save_unlocked(data)
            fcntl.flock(f, fcntl.LOCK_UN)
        return existed


class SaveStore:
    """Stores 'save' snapshots on disk."""

    def __init__(self, state_dir: Path | None = None) -> None:
        self.state_dir = state_dir or DEFAULT_STATE_DIR
        self.save_dir = self.state_dir / "saves"
        self.save_dir.mkdir(parents=True, exist_ok=True)

    def save_path(self, name: str, ts: str) -> Path:
        # ts is ISO; make it filesystem friendly
        safe_ts = ts.replace(":", "").replace("+", "_")
        return self.save_dir / f"{name}.{safe_ts}.json"

    def write(self, name: str, ts: str, payload: dict[str, Any]) -> Path:
        path = self.save_path(name, ts)
        tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        return path


@dataclass
class Message:
    """A message on the shared message board."""

    id: str
    author: str
    text: str
    created_at: str
    reply_to: str = ""  # id of parent message, empty if top-level


MESSAGES_FILE = "messages.json"
MESSAGES_LOCK = "messages.lock"


class MessageBoard:
    """Shared message board for all users."""

    def __init__(self, state_dir: Path | None = None) -> None:
        self.state_dir = state_dir or DEFAULT_STATE_DIR
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.state_dir / MESSAGES_LOCK
        self.messages_path = self.state_dir / MESSAGES_FILE

    def _load_unlocked(self) -> Dict[str, Any]:
        if not self.messages_path.exists():
            return {"version": 1, "messages": []}
        try:
            return json.loads(self.messages_path.read_text(encoding="utf-8"))
        except Exception:
            bak = self.state_dir / f"messages.corrupt.{int(time.time())}.json"
            bak.write_text(self.messages_path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
            return {"version": 1, "messages": []}

    def _save_unlocked(self, data: Dict[str, Any]) -> None:
        tmp = self.state_dir / f".messages.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.messages_path)

    def _generate_id(self) -> str:
        import random
        import string
        return "".join(random.choices(string.ascii_lowercase + string.digits, k=6))

    def post(self, author: str, text: str, reply_to: str = "") -> Message:
        """Post a new message or reply."""
        self.lock_path.touch(exist_ok=True)
        with open(self.lock_path, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            data = self._load_unlocked()
            messages = data.setdefault("messages", [])

            msg_id = self._generate_id()
            created_at = _now_iso()

            msg_data = {
                "id": msg_id,
                "author": author,
                "text": text,
                "created_at": created_at,
                "reply_to": reply_to,
            }
            messages.append(msg_data)

            # Keep only last 100 messages
            if len(messages) > 100:
                messages = messages[-100:]
                data["messages"] = messages

            self._save_unlocked(data)
            fcntl.flock(f, fcntl.LOCK_UN)

        return Message(
            id=msg_id,
            author=author,
            text=text,
            created_at=created_at,
            reply_to=reply_to,
        )

    def get_all(self) -> list[Message]:
        """Get all messages."""
        self.lock_path.touch(exist_ok=True)
        with open(self.lock_path, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            data = self._load_unlocked()
            messages = data.get("messages") or []
            out: list[Message] = []
            for m in messages:
                out.append(Message(
                    id=str(m.get("id", "")),
                    author=str(m.get("author", "")),
                    text=str(m.get("text", "")),
                    created_at=str(m.get("created_at", "")),
                    reply_to=str(m.get("reply_to", "")),
                ))
            fcntl.flock(f, fcntl.LOCK_UN)
        return out

    def get_by_id(self, msg_id: str) -> Message | None:
        """Get a message by ID."""
        for m in self.get_all():
            if m.id == msg_id:
                return m
        return None

    def clear_old(self, keep_last: int = 50) -> int:
        """Remove old messages, keeping the last N."""
        self.lock_path.touch(exist_ok=True)
        with open(self.lock_path, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            data = self._load_unlocked()
            messages = data.get("messages") or []
            original_count = len(messages)
            if len(messages) > keep_last:
                messages = messages[-keep_last:]
                data["messages"] = messages
                self._save_unlocked(data)
            fcntl.flock(f, fcntl.LOCK_UN)
        return original_count - len(messages)
