from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .proc import build_ppid_map, descendants, summarize_pids
from .state import Board, MessageBoard, SaveStore
from . import tmux as tmuxlib

app = typer.Typer(
    add_completion=False,
    help="hotdesk: a co-working style desk/session coordinator for shared machines (tmux-based).",
)
console = Console()


# -----------------------------
# Helpers
# -----------------------------


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def auto_workdir(name: str) -> str:
    """Pick a reasonable per-name workspace directory."""
    for base in [Path("/srv/work"), Path.home() / "work"]:
        try:
            p = base / name
            p.mkdir(parents=True, exist_ok=True)
            return str(p)
        except Exception:
            continue
    return str(Path.cwd())


def read_note_flagless() -> str:
    """Read a note without adding CLI flags.

    - If stdin is piped, consume it as the note.
    - Otherwise, prompt for a one-line note (optional).
    """
    try:
        if not sys.stdin.isatty():
            msg = sys.stdin.read().strip()
            return msg
    except Exception:
        pass

    try:
        return typer.prompt("Note (optional)", default="", show_default=False)
    except (EOFError, KeyboardInterrupt):
        return ""


def is_tmux_active(name: str) -> bool:
    return tmuxlib.has_session(name, name)


def ensure_desk(name: str) -> None:
    """Ensure a desk exists on the board (prepared state)."""
    board = Board()
    d = board.get(name)
    if d:
        return

    workdir = auto_workdir(name)

    board.upsert(
        name,
        status="prepared",
        prepared_at=now_iso(),
        tmux_server=name,
        tmux_session=name,
        workdir=workdir,
    )


def desk_pids(name: str) -> set[int]:
    """Get current PIDs associated with a desk (from tmux)."""
    if is_tmux_active(name):
        ppid_map, _infos = build_ppid_map()
        panes = tmuxlib.list_panes(name, name)
        roots = [p.pane_pid for p in panes]
        return descendants(roots, ppid_map)
    return set()


def show_active_desks(exclude: str | None = None) -> None:
    """Show currently active desks (tmux or processes)."""
    board = Board()
    desks = board.get_all()

    ppid_map, infos = build_ppid_map()

    rows: list[tuple[str, str, str, str]] = []

    for name, d in sorted(desks.items(), key=lambda kv: kv[0]):
        if exclude and name == exclude:
            continue

        active_tmux = is_tmux_active(name) if d.tmux_server else False

        pids: set[int] = set()
        if active_tmux:
            panes = tmuxlib.list_panes(name, name)
            roots = [p.pane_pid for p in panes]
            pids = descendants(roots, ppid_map)

        is_active = bool(pids) or active_tmux
        if not is_active:
            continue

        top = ", ".join(summarize_pids(pids, infos, max_items=6)) if pids else ""
        rows.append((name, d.note or "", str(len(pids)) if pids else "", top))

    if not rows:
        console.print("No active desks right now.")
        return

    table = Table(title="Active desks (coordinate offline)")
    table.add_column("name", style="bold")
    table.add_column("note")
    table.add_column("procs", justify="right")
    table.add_column("top commands")

    for r in rows:
        table.add_row(*r)

    console.print(table)


def save_snapshot(name: str, *, note: str, auto: bool) -> Path:
    """Write a snapshot to disk and update the board saved_at + note."""
    board = Board()
    saves = SaveStore()

    d = board.get(name)
    if not d:
        raise typer.Exit(code=1)

    ts = now_iso()

    ppid_map, infos = build_ppid_map()

    active_tmux = is_tmux_active(name)
    pids: set[int] = set()

    if active_tmux:
        panes = tmuxlib.list_panes(name, name)
        roots = [p.pane_pid for p in panes]
        pids = descendants(roots, ppid_map)

    top = summarize_pids(pids, infos, max_items=20) if pids else []

    panes_payload: list[dict[str, Any]] = []
    if active_tmux:
        for p in tmuxlib.list_panes(name, name):
            panes_payload.append(
                {
                    "pane": f"{p.session_name}:{p.window_index}.{p.pane_index}",
                    "pane_pid": p.pane_pid,
                    "command": p.current_command,
                    "title": p.title,
                }
            )

    # Keep snapshot bounded
    proc_sample = []
    for pid in sorted(pids)[:200]:
        info = infos.get(pid)
        if not info:
            continue
        proc_sample.append({"pid": pid, "ppid": info.ppid, "cmdline": info.cmdline})

    payload = {
        "tool": "hotdesk",
        "version": __version__,
        "name": name,
        "time": ts,
        "auto": auto,
        "note": note,
        "status": d.status,
        "workdir": d.workdir,
        "tmux_active": active_tmux,
        "pid_mode": "tmux",
        "pids_count": len(pids),
        "top": top,
        "panes": panes_payload,
        "process_sample": proc_sample,
    }

    path = saves.write(name, ts, payload)

    board.upsert(name, saved_at=ts, note=note)
    return path


# -----------------------------
# Commands
# -----------------------------


@app.command()
def prepare(name: str) -> None:
    """Look at the board and reserve your desk name."""
    show_active_desks(exclude=name)

    board = Board()
    workdir = auto_workdir(name)

    board.upsert(
        name,
        status="prepared",
        prepared_at=now_iso(),
        tmux_server=name,
        tmux_session=name,
        workdir=workdir,
    )

    console.print(f"\nReserved desk: [bold]{name}[/bold]")
    console.print(f"Workdir: {workdir}")
    console.print(f"Next: hotdesk start {name}")


@app.command()
def start(name: str) -> None:
    """Check in to your desk: enter tmux session."""
    ensure_desk(name)

    board = Board()
    d = board.get(name)
    assert d is not None

    # New start => clear saved_at so stop() can auto-save if needed.
    board.upsert(name, status="running", started_at=now_iso(), saved_at="")

    workdir = d.workdir or auto_workdir(name)
    board.upsert(name, workdir=workdir, tmux_server=name, tmux_session=name)

    tmuxlib.new_or_attach(server=name, session=name, workdir=workdir)


@app.command()
def resume(name: str) -> None:
    """Re-attach to an existing tmux desk session."""
    board = Board()
    d = board.get(name)

    if not d:
        console.print(f"[red]Error:[/red] desk '{name}' not found. Use 'hotdesk start {name}' first.")
        raise typer.Exit(code=1)

    if not is_tmux_active(name):
        console.print(f"[red]Error:[/red] no active tmux session for '{name}'. Use 'hotdesk start {name}' instead.")
        raise typer.Exit(code=1)

    workdir = d.workdir or auto_workdir(name)
    tmuxlib.new_or_attach(server=name, session=name, workdir=workdir)


@app.command()
def save(name: str) -> None:
    """Save a snapshot and (optionally) leave a short note."""
    ensure_desk(name)

    note = read_note_flagless()
    path = save_snapshot(name, note=note, auto=False)

    console.print(f"Saved: {path}")


@app.command()
def stop(name: str) -> None:
    """Soft stop: mark desk as stopped but keep tmux session alive (preserves history)."""
    ensure_desk(name)

    board = Board()
    d = board.get(name)
    assert d is not None

    # Auto-save if user never saved since last start.
    if d.started_at and not d.is_saved_since_start():
        try:
            note = d.note or "(auto-save on stop)"
            path = save_snapshot(name, note=note, auto=True)
            console.print(f"Auto-saved: {path}")
        except Exception as e:
            console.print(f"[yellow]Warning:[/yellow] auto-save failed: {e}")

    board.upsert(name, status="stopped", stopped_at=now_iso())

    active = is_tmux_active(name)
    pids = desk_pids(name) if active else set()

    console.print(f"[yellow]⏸ Stopped[/yellow] desk '{name}' (tmux session preserved).")
    if pids:
        console.print(f"[dim]  {len(pids)} process(es) still running. Use 'hotdesk freeze {name}' to pause them.[/dim]")
    console.print(f"[dim]  To resume: hotdesk start {name}[/dim]")
    console.print(f"[dim]  To terminate completely: hotdesk kill {name}[/dim]")

    # If running inside the tmux session, detach the client
    if tmuxlib.is_inside_session(name, name):
        console.print(f"\n[dim]Detaching from tmux session...[/dim]")
        import time as _time
        _time.sleep(0.5)  # Give user time to see the message
        tmuxlib.detach_client(name, name)


@app.command(name="kill")
def kill_desk(name: str, force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation")) -> None:
    """Hard stop: kill all processes and terminate tmux session (destroys history)."""
    board = Board()
    d = board.get(name)

    if not d:
        console.print(f"[red]Error:[/red] desk '{name}' not found.")
        raise typer.Exit(code=1)

    active = is_tmux_active(name)
    pids = desk_pids(name) if active else set()

    if not force and (active or pids):
        console.print(f"[yellow]Warning:[/yellow] This will terminate tmux session and kill {len(pids)} process(es).")
        console.print("[yellow]All scrollback history will be lost![/yellow]")
        confirm = typer.confirm("Are you sure?")
        if not confirm:
            console.print("Cancelled.")
            raise typer.Exit(code=0)

    # Auto-save before killing
    if d.started_at and not d.is_saved_since_start():
        try:
            note = d.note or "(auto-save on kill)"
            path = save_snapshot(name, note=note, auto=True)
            console.print(f"Auto-saved: {path}")
        except Exception as e:
            console.print(f"[yellow]Warning:[/yellow] auto-save failed: {e}")

    killed = 0
    exclude = {os.getpid()}

    # Kill processes from tmux-derived process tree first
    for pid in pids:
        if pid in exclude:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            killed += 1
        except Exception:
            pass

    # Then kill tmux session
    try:
        tmuxlib.kill_session(name, name)
    except Exception:
        pass

    board.upsert(name, status="killed", stopped_at=now_iso())

    console.print(f"[red]✗ Killed[/red] desk '{name}'. Terminated {killed} process(es) and tmux session.")


@app.command()
def freeze(name: str, detach: bool = typer.Option(True, "--detach/--no-detach", help="Detach from tmux after freezing")) -> None:
    """Freeze (pause) all processes in a desk session using SIGSTOP."""
    board = Board()
    d = board.get(name)

    if not d:
        console.print(f"[red]Error:[/red] desk '{name}' not found.")
        raise typer.Exit(code=1)

    if not is_tmux_active(name):
        console.print(f"[red]Error:[/red] no active tmux session for '{name}'.")
        raise typer.Exit(code=1)

    if d.status == "frozen":
        console.print(f"[yellow]Warning:[/yellow] desk '{name}' is already frozen.")
        raise typer.Exit(code=0)

    pids = desk_pids(name)
    exclude = {os.getpid()}
    frozen_count = 0

    for pid in pids:
        if pid in exclude:
            continue
        try:
            os.kill(pid, signal.SIGSTOP)
            frozen_count += 1
        except ProcessLookupError:
            pass
        except PermissionError:
            pass
        except Exception:
            pass

    board.upsert(name, status="frozen")

    console.print(f"[cyan]❄ Frozen[/cyan] desk '{name}'. Paused {frozen_count} process(es).")
    console.print(f"[dim]To resume: hotdesk unfreeze {name}[/dim]")

    # If running inside the tmux session, detach the client
    if detach and tmuxlib.is_inside_session(name, name):
        console.print(f"\n[dim]Detaching from tmux session...[/dim]")
        import time as _time
        _time.sleep(0.5)
        tmuxlib.detach_client(name, name)


@app.command()
def unfreeze(name: str) -> None:
    """Unfreeze (resume) all processes in a desk session using SIGCONT."""
    board = Board()
    d = board.get(name)

    if not d:
        console.print(f"[red]Error:[/red] desk '{name}' not found.")
        raise typer.Exit(code=1)

    if not is_tmux_active(name):
        console.print(f"[red]Error:[/red] no active tmux session for '{name}'.")
        raise typer.Exit(code=1)

    pids = desk_pids(name)
    exclude = {os.getpid()}
    resumed_count = 0

    for pid in pids:
        if pid in exclude:
            continue
        try:
            os.kill(pid, signal.SIGCONT)
            resumed_count += 1
        except ProcessLookupError:
            pass
        except PermissionError:
            pass
        except Exception:
            pass

    board.upsert(name, status="running")

    console.print(f"[green]▶ Resumed[/green] desk '{name}'. Continued {resumed_count} process(es).")


@app.command()
def status() -> None:
    """Show the board: who is active, and what they are running."""
    board = Board()
    desks = board.get_all()

    if not desks:
        console.print("Board is empty. Start with: hotdesk prepare <name>")
        raise typer.Exit(code=0)

    ppid_map, infos = build_ppid_map()

    table = Table(title="hotdesk status")
    table.add_column("name", style="bold")
    table.add_column("state")
    table.add_column("active")
    table.add_column("saved")
    table.add_column("note")
    table.add_column("procs", justify="right")
    table.add_column("top commands")

    for name, d in sorted(desks.items(), key=lambda kv: kv[0]):
        active_tmux = is_tmux_active(name) if d.tmux_server else False

        pids: set[int] = set()
        if active_tmux:
            panes = tmuxlib.list_panes(name, name)
            roots = [p.pane_pid for p in panes]
            pids = descendants(roots, ppid_map)

        is_active = bool(pids) or active_tmux

        top = ", ".join(summarize_pids(pids, infos, max_items=6)) if pids else ""

        saved = "yes" if d.is_saved_since_start() else ("-" if not d.started_at else "no")

        # Style frozen status differently
        status_display = d.status
        if d.status == "frozen":
            status_display = "[cyan]❄ frozen[/cyan]"

        table.add_row(
            name,
            status_display,
            "yes" if is_active else "no",
            saved,
            d.note or "",
            str(len(pids)) if pids else "",
            top,
        )

    console.print(table)


# -----------------------------
# Message Board Commands
# -----------------------------


def read_message_text() -> str:
    """Read message text from stdin or prompt."""
    try:
        if not sys.stdin.isatty():
            return sys.stdin.read().strip()
    except Exception:
        pass

    try:
        return typer.prompt("Message", default="", show_default=False)
    except (EOFError, KeyboardInterrupt):
        return ""


def format_time_short(iso_time: str) -> str:
    """Format ISO time to a shorter display format."""
    try:
        # Parse and reformat: 2025-12-28T10:30:00+0900 -> 12/28 10:30
        if len(iso_time) >= 16:
            return f"{iso_time[5:7]}/{iso_time[8:10]} {iso_time[11:16]}"
    except Exception:
        pass
    return iso_time[:16] if len(iso_time) > 16 else iso_time


@app.command()
def msg(name: str, text: str = typer.Argument(None)) -> None:
    """Post a message to the shared board. Usage: hotdesk msg <name> [text]"""
    if not text:
        text = read_message_text()

    if not text.strip():
        console.print("[red]Error:[/red] message cannot be empty.")
        raise typer.Exit(code=1)

    board = MessageBoard()
    m = board.post(author=name, text=text.strip())

    console.print(f"[green]Posted[/green] \\[{m.id}] {name}: {text.strip()}")


@app.command()
def reply(name: str, msg_id: str, text: str = typer.Argument(None)) -> None:
    """Reply to a message. Usage: hotdesk reply <name> <msg_id> [text]"""
    board = MessageBoard()

    parent = board.get_by_id(msg_id)
    if not parent:
        console.print(f"[red]Error:[/red] message '{msg_id}' not found.")
        raise typer.Exit(code=1)

    if not text:
        console.print(f"[dim]Replying to {parent.author}: {parent.text[:50]}...[/dim]")
        text = read_message_text()

    if not text.strip():
        console.print("[red]Error:[/red] reply cannot be empty.")
        raise typer.Exit(code=1)

    m = board.post(author=name, text=text.strip(), reply_to=msg_id)

    console.print(f"[green]Replied[/green] \\[{m.id}] {name} → {parent.author}: {text.strip()}")


@app.command()
def messages(limit: int = typer.Option(20, "--limit", "-n", help="Number of messages to show")) -> None:
    """Show the shared message board (latest first, auto-removes after 7 days)."""
    board = MessageBoard()
    all_msgs = board.get_all(latest_first=True)

    if not all_msgs:
        console.print("No messages yet. Post one with: hotdesk msg <name> <text>")
        raise typer.Exit(code=0)

    # Build a lookup for replies
    msg_by_id = {m.id: m for m in all_msgs}

    # Get first N messages (already sorted latest-first)
    recent = all_msgs[:limit]

    console.print(f"\n[bold]📋 Message Board[/bold] (showing {len(recent)} of {len(all_msgs)}, 7-day retention)\n")

    for m in recent:
        time_str = format_time_short(m.created_at)
        msg_id = m.id if m.id else "???"

        if m.reply_to and m.reply_to in msg_by_id:
            parent = msg_by_id[m.reply_to]
            # Truncate parent message for display
            parent_preview = parent.text[:40] + "..." if len(parent.text) > 40 else parent.text
            console.print(
                f"  [dim]{time_str}[/dim] [cyan]\\[{msg_id}][/cyan] [bold]{m.author}[/bold] "
                f"→ [dim]{parent.author}: \"{parent_preview}\"[/dim]"
            )
            console.print(f"    ↳ {m.text}")
        else:
            console.print(
                f"  [dim]{time_str}[/dim] [cyan]\\[{msg_id}][/cyan] [bold]{m.author}[/bold]: {m.text}"
            )

    console.print(
        "\n[dim]Commands: hotdesk msg <name> <text> | hotdesk reply <name> <id> <text>[/dim]"
    )
