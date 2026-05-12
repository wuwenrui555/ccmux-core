<!-- markdownlint-disable MD024 -->

# ccmux-core bindings tracker — derived live cache of tmux ↔ claude bindings

- **Date**: 2026-05-12
- **Repo**: `ccmux-core`
- **Status**: design draft, awaiting user review
- **Targets**: ccmux-core v0.3.0
- **Builds on**: [2026-05-12-ccmux-core-l2-design.md](2026-05-12-ccmux-core-l2-design.md)

## Context

Downstream consumers of ccmux-core (e.g., the planned `ccmux-core-telegram`
bot) need cheap, real-time lookups of the `tmux_session ↔ pane_id ↔
claude_session_id` triple for arbitrary tmux sessions. Today the only
APIs are:

- `list_live_tmux_bindings()` — synchronous, scans the entire
  `events.jsonl` end-to-end on every call.
- `discover_tmux_sessions()` — async iterator that yields once per
  newly-seen tmux session, not a continuous current-state view.
- `Backend` — owns the current state for one specific tmux session,
  but has no holistic view of all live sessions.

The Telegram bot in particular will hit this on every inbound message
("which Backend should `send_prompt` go to for topic X?"). Re-scanning
events.jsonl on every lookup is wasteful, and growing files make the
scan slower over time. Worse, none of these APIs survive a process
restart: a fresh consumer starting up has no fast path to "where were
things just before I started".

This spec adds a small **bindings tracker** to ccmux-core:

- A persistent JSON file `~/.ccmux-core/bindings.json` that mirrors
  the current `tmux_session → {pane_id, current_session_id,
  session_id_history, ...}` snapshot.
- A live updater (`BindingsTracker` async-context-manager) that
  consumers run alongside their event loop and that tails new events,
  updating the file atomically on every structural change.
- A one-shot CLI (`ccmux-core bindings snapshot`) that rebuilds the
  file by re-scanning the full event log, for first-time setup and
  drift recovery.

Out of scope: removing entries on session_end (we deliberately
preserve history so that "what claude was attached to tmux X before
the server crashed" is queryable post-reboot); a long-running daemon
process separate from consumers.

## Goals

1. Eliminate full event-log scans from the lookup path of long-running
   consumers.
2. Survive consumer restart: bindings.json reflects state as of the
   last write, immediately readable by any process.
3. Survive `session_end` and `/clear`: keep the historical
   `session_id` so users can `claude --resume <sid>` after an abnormal
   server reboot.

## Non-goals

1. **Server-grade durability.** This file is a derived cache; the
   snapshot CLI rebuilds it from events.jsonl. No `fsync` —
   `os.replace` atomicity is enough to keep readers consistent, and a
   power-loss-corrupted cache can be rebuilt manually.
2. **Multi-writer arbitration beyond a simple lock.** A single
   `fcntl.flock` covers the realistic race (consumer's tracker +
   manual `snapshot` CLI). We don't try to support multiple consumers
   all running their own trackers in parallel.
3. **Removing dead sessions automatically.** Entries are
   never deleted; they just have `current_session_id: null` and an
   `ended_at` timestamp. Trimming is a future concern if the file
   grows pathologically.
4. **Tracking pane_id history.** Only `session_id_history` is
   retained. `pane_id` / `window_id` carry only the current value.

## Architecture

```text
                   ┌──────────────────────────────────────────────┐
                   │ Consumer process (e.g. ccmux-core-telegram)  │
                   │                                              │
                   │   async with BindingsTracker() as t:         │
                   │       await app.run_polling()                │
                   │                                              │
                   │   ┌────────────────────────────────────────┐ │
                   │   │ BindingsTracker (background asyncio    │ │
                   │   │ task in the same loop)                 │ │
                   │   │                                        │ │
                   │   │  1. Load existing bindings.json        │ │
                   │   │  2. Seek events.jsonl to EOF           │ │
                   │   │  3. Tail new events; per event:        │ │
                   │   │      apply _step()            │ │
                   │   │      if structural change:             │ │
                   │   │          atomic_write(bindings.json)   │ │
                   │   │  4. On __aexit__: final flush          │ │
                   │   └────────────────────────────────────────┘ │
                   └──────────────────────────────────────────────┘
                                       │
                                       │ reads / writes (with flock)
                                       ▼
                          ~/.ccmux-core/bindings.json
                                       ▲
                                       │ writes (with flock)
                                       │
                          ┌────────────────────────────┐
                          │ ccmux-core bindings        │
                          │ snapshot                   │
                          │  (manual CLI, one-shot)    │
                          │                            │
                          │  Scan full events.jsonl,   │
                          │  build dict from scratch,  │
                          │  atomic write              │
                          └────────────────────────────┘
                                       ▲
                                       │
                          ~/.claude-tap/events.jsonl
                          (append-only, owned by claude-tap)
```

Two entry points, both write the same file, both use the same lock:

- **`BindingsTracker`** (Python class, library API) — runs in-process
  inside a consumer. Cheap incremental updates as events arrive.
- **`ccmux-core bindings snapshot`** (CLI) — runs once, manually,
  whenever the user wants a clean rebuild from events.jsonl.

There is no separate daemon process. The consumer's lifetime owns the
tracker's lifetime.

## File schema

Path: `${CCMUX_CORE_DIR}/bindings.json` (default `~/.ccmux-core/bindings.json`)

```json
{
  "ccmux": {
    "pane_id": "%42",
    "window_id": "@1",
    "current_session_id": "504921bb-1a7e-409e-8145-2fbac808f0b3",
    "session_id_history": [
      "old-session-1",
      "old-session-2",
      "504921bb-1a7e-409e-8145-2fbac808f0b3"
    ],
    "first_seen_at": "2026-05-12T08:00:00Z",
    "last_event_at": "2026-05-12T09:13:54Z",
    "ended_at": null
  },
  "another-session": {
    "pane_id": "%55",
    "window_id": "@3",
    "current_session_id": null,
    "session_id_history": ["abc-12345"],
    "first_seen_at": "2026-05-11T16:42:00Z",
    "last_event_at": "2026-05-12T07:00:00Z",
    "ended_at": "2026-05-12T07:00:00Z"
  }
}
```

Top-level shape is `dict[tmux_session_name, BindingRecord]`. Keyed by
tmux session name for O(1) consumer lookup.

### Field semantics

| Field | Type | Meaning |
| --- | --- | --- |
| `pane_id` | string | tmux pane currently associated with this session's primary claude. Updates when matching events arrive. |
| `window_id` | string | tmux window of the primary pane. |
| `current_session_id` | string \| null | claude session ID of the currently-attached primary, or `null` if no claude is attached right now (after `session_end` or `/clear`). |
| `session_id_history` | list[string] | All claude session IDs ever observed for this tmux session, in **first-seen order**, deduplicated. Resume candidate list. |
| `first_seen_at` | ISO-8601 string | Wall-clock time of the first event we saw for this tmux session. Set on entry creation, never updated. |
| `last_event_at` | ISO-8601 string | Wall-clock time of the most recent event we saw for this tmux session, of any kind. Updated on every event but does NOT itself trigger a disk write. |
| `ended_at` | ISO-8601 string \| null | Wall-clock time of the most recent `current_session_id: value → null` transition, or `null` if currently attached. Reset to `null` on a subsequent attach. |

### Why preserve entries forever

The motivating use case: a server reboots abnormally and kills all
claude processes. Some sessions may have had a clean `session_end`
event written before they died; others did not. We want a single,
consistent answer to "which claude was last attached to tmux session
X?" regardless of whether the death was clean or abrupt. Never
deleting solves this.

The `ended_at` field gives consumers a clear signal of "stale
binding" for cases like a `/start` picker that wants to surface only
live sessions.

## Event fold rules

The existing `discover._step()` function deleted entries on
`session_end` (clean exit). As part of this work we **unify** the
fold into a single `_step()` with **preserve** semantics — entries
are never deleted; instead `current_session_id` flips to `null` and
`ended_at` is set. The existing `list_live_tmux_bindings()` public
behavior is preserved by adding a `current_session_id is not None`
filter at the boundary; consumers see the same "live sessions only"
list they always did. Discussion of why this unifies cleanly rather
than maintaining two parallel folds lives in *Module layout* and
*Decisions log* below.

Updated `_step()` rules:

| Event | `current_session_id` | `session_id_history` | `ended_at` | `pane_id` / `window_id` | `last_event_at` |
| --- | --- | --- | --- | --- | --- |
| `session_start` (new tmux session, no entry exists) | set to event's `sid` | `[sid]` | `null` | from event | from event |
| `session_start` (entry exists, current is `null`) | set to event's `sid` | append `sid` if `sid not in history` | `null` (reset) | from event | from event |
| `session_start` (entry exists, current is set, sid differs) | unchanged | unchanged | unchanged | unchanged | update |
| `session_start` (entry exists, current is set, sid matches) | unchanged | unchanged | unchanged | unchanged | update |
| `session_end` reason=`clear` | `null` | unchanged | event ts | unchanged | update |
| `session_end` reason=`prompt_input_exit` | unchanged | unchanged | unchanged | unchanged | update |
| `session_end` other reason | `null` | unchanged | event ts | unchanged | update |
| Other event (matches current) | unchanged | unchanged | unchanged | from event | update |
| Other event (doesn't match current) | unchanged | unchanged | unchanged | unchanged | update |

Notes:

- **Subagent / non-primary session_starts** keep the existing primary
  intact, matching `_step()`'s rule that subagent sids don't override
  primary.
- **`session_id_history` dedup**: append a new sid only when it is
  not already present in the list (full set semantics, preserving
  first-seen insertion order). Re-attaching to an earlier sid via
  `claude --resume <old-sid>` therefore does not grow the list.
- **`first_seen_at`** is set once when the entry is created (first
  `session_start` for a tmux session) and never updated afterward.

### Write trigger (structural change)

The tracker writes the JSON file only when a "structural" change
happens. `last_event_at`-only updates accumulate in memory and ride
along on the next structural write.

Structural changes:

- New entry created.
- `pane_id` or `window_id` changes.
- `current_session_id` changes (either direction: `null ↔ value` or
  `value → null`).
- `session_id_history` gets a new entry appended.
- `ended_at` changes (always coupled with a `current_session_id`
  change, so the trigger is redundant but harmless to list).

Non-structural changes (`last_event_at`-only) are batched implicitly:
each structural write happens to flush the latest `last_event_at`
too.

## Consumer API

### `BindingsTracker` async context manager

```python
from ccmux_core.bindings import BindingsTracker

async def main() -> None:
    async with BindingsTracker() as _tracker:
        await app.run_polling()
        # tracker quietly tails events.jsonl, updates bindings.json
```

**Lifecycle**:

| Stage | Action |
| --- | --- |
| `__aenter__` | Acquire flock (released after each write). Load existing `bindings.json` into in-memory dict (empty dict if file missing). Seek `events.jsonl` to EOF. Start a background asyncio task that tails the file and applies events. |
| Steady state | Background task reads new lines, parses JSON, applies `_step()`, on structural change calls `_atomic_write()`. |
| `__aexit__` | Cancel background task, drain any pending event, force-flush the dict to disk regardless of dirty flag (covers the case where only `last_event_at` changed during the session). Release any held resources. |

**Constructor parameters** (all optional, all with sensible defaults):

```python
class BindingsTracker:
    def __init__(
        self,
        events_path: Path | None = None,    # default: claude_tap.config.events_path()
        bindings_path: Path | None = None,  # default: ccmux_core.config.ccmux_core_dir() / "bindings.json"
        lock_path: Path | None = None,      # default: bindings_path.with_suffix(".lock")
    ) -> None: ...
```

### Reading the file (consumer side)

Consumers just read the JSON. No locking needed for readers — `os.replace`
keeps writes atomic from a reader's perspective.

```python
import json
from pathlib import Path
from ccmux_core.config import ccmux_core_dir

def read_bindings() -> dict[str, dict]:
    path = ccmux_core_dir() / "bindings.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())
```

A future helper `read_bindings()` may live in `ccmux_core.bindings`
for typed convenience, but is not required for MVP.

### Snapshot CLI

```bash
ccmux-core bindings snapshot
ccmux-core bindings snapshot --output /tmp/snap.json
```

Implementation: walk the entire `events.jsonl`, fold via
`_step()`, atomic-write the result. Same lock as the
tracker.

Use cases:

- First-time setup: no `bindings.json` exists yet; create one with
  full history.
- Recovery after suspected drift: tracker missed events because the
  consumer process was down for an extended period.
- Manual inspection / scripting: generate a fresh snapshot to a
  custom path.

The CLI exits 0 on success and writes a one-line confirmation to
stderr (`wrote N bindings to <path>`). Exit code 1 if events.jsonl is
unreadable or path is unwritable.

## File-operation contract

### Atomic write

```python
import fcntl
import json
import os
from pathlib import Path

def _atomic_write(path: Path, lock_path: Path, data: dict) -> None:
    serialized = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    tmp = path.with_suffix(".json.tmp")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        try:
            tmp.write_bytes(serialized)
            os.replace(tmp, path)
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)
```

- **`os.replace`** is atomic on the same filesystem (POSIX `rename`
  semantics). Readers always see the old or new file, never partial.
- **`fcntl.flock`** is an advisory exclusive lock on a separate
  `.lock` file. Held only for the duration of the write. Prevents
  the tracker and the snapshot CLI from racing.
- **No `fsync`.** Power-loss / kernel-panic recovery is delegated to
  the snapshot CLI. The 10ms-per-write durability cost is not worth
  paying for a derivative cache.
- **Tmp file in same directory** so the rename stays on the same
  filesystem and atomicity holds.

### Reader contract

- File is JSON, top-level dict.
- Empty / missing file means "no bindings known yet" → return `{}`.
- Malformed JSON → raise. (This means tracker write corruption is
  loud, not silent.)
- No locking required on the read side.

## Module layout

`discover.py` is **renamed** to `bindings.py` and absorbs the new
feature. The two were doing the same job (folding events into
per-tmux binding state); splitting them produced two near-identical
fold functions diverging only in `session_end` handling. Unified:

```text
src/ccmux_core/
├── bindings.py           ← rename from discover.py + new code
│   ── existing surface (moved + adjusted) ──────────────────
│   - @dataclass TmuxBinding         # widened to 7 fields
│   - @dataclass _MutableBinding     # widened, mutable mirror
│   - _step(state, event) -> None    # single fold, preserve semantics
│   - list_live_tmux_bindings()      # filter `current_session_id is not None`
│   - discover_tmux_sessions()       # returns widened TmuxBinding
│   ── new surface ─────────────────────────────────────────
│   - _atomic_write(path, lock_path, data) -> None
│   - load_bindings(path) -> dict[str, dict]
│   - snapshot(events_path, bindings_path, lock_path) -> int
│   - class BindingsTracker
├── __init__.py           ← re-exports updated to point at bindings.py
├── cli.py                ← extended with `bindings snapshot` subcommand;
│                           any references to `primary_session_id`
│                           renamed to `current_session_id`
├── config.py             ← no change
├── discover.py           ← DELETED
└── ...
```

`TmuxBinding` (public dataclass) widens from 5 to 7 fields and renames
`primary_session_id` → `current_session_id`:

```python
@dataclass(frozen=True)
class TmuxBinding:
    tmux_session: str
    pane_id: str
    window_id: str
    current_session_id: str | None           # renamed from primary_session_id
    session_id_history: tuple[str, ...]      # new (tuple for frozen dataclass)
    first_seen_at: str                       # new
    last_event_at: str
    ended_at: str | None                     # new
```

This is a backward-incompatible Python API change. v0.3.0 is the
right time: pre-1.0 software, the public API is still in flux, and
the only `primary_session_id` consumer today is this same repo's
`cli.py` + tests.

Internal `_MutableBinding` mirrors the widened shape with mutable
fields and a `list[str]` for `session_id_history` (converted to
tuple at the `TmuxBinding` boundary). `list_live_tmux_bindings()`
filters out entries where `current_session_id is None`, preserving
its observable "live sessions only" contract even though the
underlying dict now retains ended entries.

## Testing

- **Pure fold tests** (no I/O): feed synthetic event sequences to
  `_step()`, assert dict shape after each event. Cover:
  - First `session_start` creates entry with `current` set, `history=[sid]`, `ended_at=null`.
  - Clean `session_end` (non-clear) sets `current=null`, `ended_at=ts`, leaves `history` intact.
  - `session_end` reason=`clear` sets `current=null`, `ended_at=ts`.
  - `session_end` reason=`prompt_input_exit` is a no-op except for `last_event_at`.
  - Subsequent `session_start` after `session_end` resets `current`, clears `ended_at`, appends sid to `history` only if `sid not in history` (full dedup).
  - Subagent `session_start` (current set, different sid) preserves current.
  - `--resume` (same sid as `current` or last history entry) does not duplicate in history.
- **Atomic write tests**: write to a tmp dir, assert the target file
  is either the old or new content at any sample point (mock
  interrupted writes by injecting a `RuntimeError` between
  `tmp.write_bytes` and `os.replace`; assert old file intact).
- **Flock test**: two processes / coroutines both holding the writer
  pattern; assert serialization (one waits for the other; final state
  matches the last writer's input).
- **Snapshot CLI test**: feed a fixture `events.jsonl`, run the CLI,
  parse the resulting bindings.json, assert it matches the expected
  dict.
- **`BindingsTracker` integration test**: write a fixture
  `events.jsonl`, start tracker, append more events, await, assert
  bindings.json reflects only the post-start events (because tracker
  seeks to EOF on startup). Then explicitly call snapshot, assert
  bindings.json now reflects the full history.
- **`list_live_tmux_bindings` filter behavior**: existing tests in
  `test_discover.py` (now `test_bindings.py`) keep their assertions —
  the observable result list is unchanged after the internal switch
  from "delete on session_end" to "preserve + filter". Add a focused
  test where an ended session is in the underlying dict but the
  filtered result does not include it.

No network, tmux, or claude required — all tests operate on synthetic
event JSON.

## Rollout

1. Land this design doc on `dev`.
2. Implement `bindings.py`, CLI subcommand, tests.
3. Cut release as ccmux-core v0.3.0.
4. ccmux-core-telegram brainstorm resumes against this contract.

## Open questions

(None currently. Decisions captured above are intentional defaults
unless re-litigated in implementation review.)

## Decisions log

For audit / future reconsideration:

- **No `fsync`.** Cost (~10ms per write) outweighs the rare power-loss
  scenarios, given the snapshot CLI is a viable recovery path. May
  revisit if production reports show corrupted bindings.json after
  abnormal shutdowns.
- **`flock` is kept.** Cheap, covers the realistic concurrent-write
  case (tracker + manual snapshot run on the same machine).
- **No automatic GC of old entries.** History grows unbounded.
  Realistic upper bound is single-digit entries per tmux session; if
  pathological, add later.
- **Tracker seeks to EOF on start.** Catching up on missed events is
  the snapshot CLI's job, not the tracker's. User-managed recovery
  rather than implicit replay.
- **`first_seen_at` immutable after creation.** Useful as a stable
  identifier of "when this binding first appeared in this machine's
  history".
- **`ended_at` resets to `null` on re-attach.** Single field doubles
  as "is this session currently ended" (when non-null) and "how long
  ago" — simpler than carrying a separate `state` enum.
- **Unify `discover.py` and the new tracker into `bindings.py`.**
  Originally drafted as two parallel modules with twin fold functions
  (`_step` vs `_step_preserve`) differing only in `session_end`
  handling. Walking through the duplication surfaced it as
  inevitable drift risk. Preserve semantics are a strict superset of
  delete semantics; `list_live_tmux_bindings()` reaches the same
  observable behavior via a single `current_session_id is not None`
  filter. Renaming `TmuxBinding.primary_session_id` →
  `current_session_id` is a small public-API break that aligns
  Python attribute names with the JSON schema field names; v0.3.0
  is the right window (pre-1.0).
