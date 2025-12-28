# hotdesk

**hotdesk** turns a single Linux/macOS account into a small **co-working space**.

- Each person picks a **desk name** (e.g. `alice`, `bob`).
- A desk is backed by a **dedicated tmux server/session** (`tmux -L <name> ...`).
- Process tracking is done via tmux pane PIDs and their descendants.

> This is **coordination**, not security isolation. If everyone shares one account (same UID), true isolation is not possible.

---

## Commands (intentionally minimal)

### Desk management
- `hotdesk prepare <name>` – check the board, reserve your desk name
- `hotdesk start <name>` – check in: enter your tmux desk
- `hotdesk resume <name>` – re-attach to an existing tmux session
- `hotdesk status` – show who is active and what they're running
- `hotdesk save <name>` – save a snapshot + leave a short note
- `hotdesk stop <name>` – check out: **auto-saves** if you forgot to save, then stops that desk

### Shared message board
- `hotdesk msg <name> <text>` – post a message to the shared board
- `hotdesk reply <name> <msg_id> <text>` – reply to a specific message
- `hotdesk messages` – show the message board (use `-n 50` to show more)

### Typical flow

```bash
hotdesk prepare bob
hotdesk start bob

# detached from tmux? re-attach easily:
hotdesk resume bob

# later (leave a note + snapshot)
hotdesk save bob

# done for the day
hotdesk stop bob
```

### Message board example

```bash
# Post a message
hotdesk msg bob "Starting GPU training, will take ~4 hours"

# Check messages
hotdesk messages

# Reply to a message (use the message ID shown in brackets)
hotdesk reply alice abc123 "OK, I'll wait. Ping me when done!"
```

---

## Install (using uv)

### 1) Install `uv`

```bash
# Recommended installer
curl -LsSf https://astral.sh/uv/install.sh | sh
```

(Or install via your package manager if you prefer.)

### 2) Create a venv + install

From the repo root:

```bash
uv venv
source .venv/bin/activate
uv pip install -e .
```

Now you should have the `hotdesk` command.

---

## Notes and conventions

### Leaving a note without extra flags

`hotdesk save <name>` reads a note in a flag-less way:

- If you run it interactively, it will prompt: `Note (optional)`
- If you pipe text, it uses that text as the note:

```bash
echo "training running; please ping me before using GPU" | hotdesk save bob
```

### State location

By default hotdesk stores state in one of:

- `$XDG_STATE_HOME/hotdesk` (preferred)
- `/var/tmp/hotdesk`
- `~/.hotdesk`

You can override with:

- `HOTDESK_STATE_DIR=/some/path`

---

## License

MIT
