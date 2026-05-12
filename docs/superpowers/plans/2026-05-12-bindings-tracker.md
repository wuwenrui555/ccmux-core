# ccmux-core bindings tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or executing-plans-test-first to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the `BindingsTracker` async context manager and `ccmux-core bindings snapshot` CLI in ccmux-core v0.3.0, with `discover.py` unified into a new `bindings.py` module that uses preserve-mode event folding.

**Architecture:** Single module `src/ccmux_core/bindings.py` owns the public `TmuxBinding` dataclass (widened from 5 to 7 fields), the in-memory mutable mirror `_MutableBinding`, a single preserve-semantics `_step()` fold, plus the new `_atomic_write` / `load_bindings` / `snapshot` / `BindingsTracker` surface. The JSON file at `~/.ccmux-core/bindings.json` is the contract; `os.replace` keeps readers consistent and `fcntl.flock` keeps multiple writers serialized. No `fsync` — the snapshot CLI is the recovery path.

**Tech Stack:** Python 3.11+, asyncio, claude-tap (upstream event source), fcntl (advisory file lock), `pytest` for tests. Pre-commit (ruff + ruff-format + markdownlint) runs at commit time.

**Spec:** [2026-05-12-bindings-tracker-design.md](../specs/2026-05-12-bindings-tracker-design.md)

---

## File Structure

**Created**

- `src/ccmux_core/bindings.py` — replaces `discover.py`; holds `TmuxBinding`, `_MutableBinding`, `_step`, `list_live_tmux_bindings`, `discover_tmux_sessions`, `_atomic_write`, `load_bindings`, `snapshot`, `BindingsTracker`.
- `tests/test_bindings.py` — replaces `test_discover.py`; existing folds + new preserve / atomic / snapshot / tracker tests.

**Modified**

- `src/ccmux_core/__init__.py` — re-exports point at `bindings` module.
- `src/ccmux_core/cli.py` — adds `bindings snapshot` subparser; renames `primary_session_id` → `current_session_id` references.
- `tests/test_cli.py` — same rename in any reference.
- `CHANGELOG.md` — `[Unreleased]` section grows with this feature.

**Deleted**

- `src/ccmux_core/discover.py`
- `tests/test_discover.py`

---

### Task 1: Rename `discover.py` → `bindings.py`

Mechanical move so subsequent tasks edit one file with the right name. No behavior change; all existing tests must stay green.

**Files:**

- Rename: `src/ccmux_core/discover.py` → `src/ccmux_core/bindings.py`
- Rename: `tests/test_discover.py` → `tests/test_bindings.py`
- Modify: `src/ccmux_core/__init__.py`
- Modify: `src/ccmux_core/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Move the source file with git**

```bash
git mv src/ccmux_core/discover.py src/ccmux_core/bindings.py
git mv tests/test_discover.py tests/test_bindings.py
```

- [ ] **Step 2: Update import in `tests/test_bindings.py`**

```python
# was: from ccmux_core.discover import (
from ccmux_core.bindings import (
```

- [ ] **Step 3: Update import in `src/ccmux_core/__init__.py`**

```python
# was: from .discover import (
from .bindings import (
    TmuxBinding,
    discover_tmux_sessions,
    list_live_tmux_bindings,
)
```

- [ ] **Step 4: Update import in `src/ccmux_core/cli.py`**

```python
# was: from .discover import TmuxBinding, list_live_tmux_bindings
from .bindings import TmuxBinding, list_live_tmux_bindings
```

- [ ] **Step 5: Update import in `tests/test_cli.py`**

```python
# was: from ccmux_core.discover import TmuxBinding
from ccmux_core.bindings import TmuxBinding
```

- [ ] **Step 6: Run full test suite to verify rename is clean**

Run: `uv run --extra dev pytest -q`
Expected: all tests pass (same count as before the rename).

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor(bindings): rename discover.py to bindings.py

Pure rename + import updates; no behavior change. Sets up the
module for the preserve-mode fold + tracker additions per the
bindings-tracker spec."
```

---

### Task 2: Widen `TmuxBinding` (public dataclass) to 7 fields

Rename `primary_session_id` → `current_session_id`, add `session_id_history` / `first_seen_at` / `ended_at`. Public-API breaking, intentional for v0.3.0.

**Files:**

- Modify: `src/ccmux_core/bindings.py`
- Modify: `src/ccmux_core/cli.py`
- Modify: `tests/test_bindings.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write a failing test for the widened dataclass**

In `tests/test_bindings.py`, add at the top of the existing test list (after the existing `_ev` helper):

```python
def test_tmuxbinding_has_new_fields():
    from ccmux_core.bindings import TmuxBinding

    tb = TmuxBinding(
        tmux_session="ccmux",
        pane_id="%1",
        window_id="@0",
        current_session_id="sid-1",
        session_id_history=("sid-1",),
        first_seen_at="2026-05-12T00:00:00Z",
        last_event_at="2026-05-12T00:00:01Z",
        ended_at=None,
    )
    assert tb.current_session_id == "sid-1"
    assert tb.session_id_history == ("sid-1",)
    assert tb.ended_at is None
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `uv run --extra dev pytest tests/test_bindings.py::test_tmuxbinding_has_new_fields -v`
Expected: FAIL — unexpected keyword arguments / dataclass mismatch.

- [ ] **Step 3: Update the dataclass in `src/ccmux_core/bindings.py`**

Replace the existing `TmuxBinding` definition with:

```python
@dataclass(frozen=True)
class TmuxBinding:
    """Current mapping for one tmux session.

    Produced by :func:`list_live_tmux_bindings` (snapshot) and
    :func:`discover_tmux_sessions` (stream).

    ``current_session_id is None`` indicates the binding has been
    observed before but no Claude session is currently attached
    (post-``session_end`` or post-``/clear``). ``session_id_history``
    is the first-seen-order, deduplicated list of every Claude
    session id this tmux session has hosted. ``ended_at`` is the
    timestamp of the most recent ``current → None`` transition or
    ``None`` if currently attached.
    """

    tmux_session: str
    pane_id: str
    window_id: str
    current_session_id: str | None
    session_id_history: tuple[str, ...]
    first_seen_at: str
    last_event_at: str
    ended_at: str | None
```

- [ ] **Step 4: Update existing `TmuxBinding(...)` test construction in `tests/test_cli.py`**

Find the construction in `test_bindings_table_single` (or similar) and update field names + add new fields:

```python
TmuxBinding(
    tmux_session="ccmux",
    pane_id="%42",
    window_id="@0",
    current_session_id="abc-12345",
    session_id_history=("abc-12345",),
    first_seen_at="2026-05-11T01:55:42Z",
    last_event_at="2026-05-11T01:55:42Z",
    ended_at=None,
)
```

- [ ] **Step 5: Update consumers of `primary_session_id` in `src/ccmux_core/cli.py`**

`cli.py` reads `match.primary_session_id` in `_watch_async` (around the `ctx["primary_sid"]` initialization). Rename:

```python
# was: "primary_sid": match.primary_session_id,
"primary_sid": match.current_session_id,
```

Grep first to be sure no other reference exists: `grep -n primary_session_id src/ccmux_core/cli.py`. Update every hit.

- [ ] **Step 6: Run the new test + everything else**

Run: `uv run --extra dev pytest -q`
Expected: pass count matches Task 1 + 1 (the new test). If existing tests in `test_bindings.py` fail because `TmuxBinding(...)` constructions in their assertions are missing fields, fix each by adding the new fields. Don't change the assertion shape — just supply the new fields with realistic placeholder values (e.g. `session_id_history=("S1",)`, `first_seen_at="T1"`, `ended_at=None`).

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(bindings)!: widen TmuxBinding to 7 fields

BREAKING CHANGE: TmuxBinding.primary_session_id is renamed to
current_session_id. Adds session_id_history (tuple), first_seen_at,
and ended_at. Internal _MutableBinding still on the old shape;
event-fold updates come in the next commit."
```

---

### Task 3: Widen `_MutableBinding` and populate new fields on creation

Update internal record to mirror `TmuxBinding`. `session_id_history` is a `list[str]` internally (mutability), converted to tuple at the `TmuxBinding` boundary.

**Files:**

- Modify: `src/ccmux_core/bindings.py`
- Modify: `tests/test_bindings.py`

- [ ] **Step 1: Write a failing test for first-seen-at and history on session_start**

```python
def test_session_start_populates_new_fields(tmp_path):
    p = tmp_path / "events.jsonl"
    _write_events(p, [_ev("session_start", "S1", ts="T1")])
    [out] = list_live_tmux_bindings(events_path=p)
    assert out.current_session_id == "S1"
    assert out.session_id_history == ("S1",)
    assert out.first_seen_at == "T1"
    assert out.last_event_at == "T1"
    assert out.ended_at is None
```

- [ ] **Step 2: Run to confirm it fails**

Run: `uv run --extra dev pytest tests/test_bindings.py::test_session_start_populates_new_fields -v`
Expected: FAIL — `AttributeError` or wrong field values (current `_MutableBinding` doesn't have these).

- [ ] **Step 3: Update `_MutableBinding`**

Replace its definition with:

```python
@dataclass
class _MutableBinding:
    tmux_session: str
    pane_id: str
    window_id: str
    current_session_id: str | None
    session_id_history: list[str]
    first_seen_at: str
    last_event_at: str
    ended_at: str | None
```

- [ ] **Step 4: Update `_step()` `session_start` branches to populate new fields on creation**

Inside `_step`, replace the existing `if et == "session_start":` block with:

```python
if et == "session_start":
    if b is None:
        bindings[tmux_session] = _MutableBinding(
            tmux_session=tmux_session,
            pane_id=tmux.get("pane_id", ""),
            window_id=tmux.get("window_id", ""),
            current_session_id=sid,
            session_id_history=[sid],
            first_seen_at=ts,
            last_event_at=ts,
            ended_at=None,
        )
        return
    if b.current_session_id is None:
        # Re-attach after end / clear. Append sid to history if new.
        if sid not in b.session_id_history:
            b.session_id_history.append(sid)
        b.current_session_id = sid
        b.pane_id = tmux.get("pane_id", b.pane_id)
        b.window_id = tmux.get("window_id", b.window_id)
        b.ended_at = None
        b.last_event_at = ts
        return
    # current set already — subagent / duplicate event
    b.last_event_at = ts
    return
```

- [ ] **Step 5: Update `list_live_tmux_bindings()` to build the new `TmuxBinding` shape**

Replace the return expression with:

```python
return [
    TmuxBinding(
        tmux_session=b.tmux_session,
        pane_id=b.pane_id,
        window_id=b.window_id,
        current_session_id=b.current_session_id or "",
        session_id_history=tuple(b.session_id_history),
        first_seen_at=b.first_seen_at,
        last_event_at=b.last_event_at,
        ended_at=b.ended_at,
    )
    for b in bindings.values()
    if b.current_session_id is not None
]
```

The `current_session_id or ""` mirrors the legacy "empty string when None" behavior for the public dataclass; the filter already excludes None cases, so this is just a type-safety belt.

Apply the same change to `discover_tmux_sessions()`'s yield.

- [ ] **Step 6: Run tests**

Run: `uv run --extra dev pytest tests/test_bindings.py -v`
Expected: the new test passes, and existing tests stay green. Any test that constructed `TmuxBinding` literally and is now missing fields needs the same supply-the-new-fields fix as Task 2 Step 6.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(bindings): populate first_seen_at and history on session_start

_MutableBinding mirrors the widened TmuxBinding shape. _step() sets
first_seen_at on creation and seeds session_id_history with the
initial sid. session_end deletion semantics are unchanged in this
commit; the preserve-semantics switch comes next."
```

---

### Task 4: Preserve entries on `session_end`; filter at `list_live`

Stop deleting from the dict on clean `session_end`. Set `current_session_id = None` and `ended_at = ts` instead. Existing observable tests stay green because `list_live_tmux_bindings()` filters by `current_session_id is not None`.

**Files:**

- Modify: `src/ccmux_core/bindings.py`
- Modify: `tests/test_bindings.py`

- [ ] **Step 1: Write a failing test that asserts entry is preserved (via the dict directly)**

Since `list_live` filters out ended sessions, write the test against the internal fold helper directly. Add to `tests/test_bindings.py`:

```python
def test_session_end_preserves_entry():
    from ccmux_core.bindings import _MutableBinding, _step

    bindings: dict[str, _MutableBinding] = {}
    _step(bindings, _ev("session_start", "S1", ts="T1"))
    _step(bindings, _ev("session_end", "S1", payload={"reason": "exit"}, ts="T2"))

    assert "ccmux" in bindings, "entry must be preserved after session_end"
    entry = bindings["ccmux"]
    assert entry.current_session_id is None
    assert entry.ended_at == "T2"
    assert entry.session_id_history == ["S1"]
```

- [ ] **Step 2: Run to confirm it fails**

Run: `uv run --extra dev pytest tests/test_bindings.py::test_session_end_preserves_entry -v`
Expected: FAIL — `KeyError: "ccmux"` because the old code did `del bindings[tmux_session]`.

- [ ] **Step 3: Update `session_end` branch in `_step()`**

Replace the existing `if et == "session_end":` block with:

```python
if et == "session_end":
    if b is None or sid != b.current_session_id:
        if b is not None:
            b.last_event_at = ts
        return
    reason = payload.get("reason", "")
    if reason == "clear":
        b.current_session_id = None
        b.ended_at = ts
        b.last_event_at = ts
        return
    if reason == "prompt_input_exit":
        b.last_event_at = ts
        return
    # Other reason (clean exit, etc.) — preserve entry, mark ended.
    b.current_session_id = None
    b.ended_at = ts
    b.last_event_at = ts
    return
```

- [ ] **Step 4: Update the now-misleading legacy test name**

Find `test_fatal_session_end_removes_binding` in `tests/test_bindings.py`. Rename + tighten the assertion. New version:

```python
def test_fatal_session_end_excludes_from_live_list(tmp_path):
    p = tmp_path / "events.jsonl"
    _write_events(
        p,
        [
            _ev("session_start", "S1"),
            _ev("session_end", "S1", payload={"reason": "error"}),
        ],
    )
    # Observable behavior unchanged: list_live filters out ended sessions
    # even though the internal dict now preserves the entry.
    assert list_live_tmux_bindings(events_path=p) == []
```

- [ ] **Step 5: Run all tests**

Run: `uv run --extra dev pytest tests/test_bindings.py -v`
Expected: all pass, including `test_session_end_preserves_entry`, `test_fatal_session_end_excludes_from_live_list`, and `test_clear_with_no_rebind_excludes_binding`. The last one tests `/clear` with no rebind → list_live returns `[]`, which still works because clear sets `current_session_id = None` and the filter excludes it.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(bindings): preserve entries through session_end

session_end (clean) now sets current_session_id=None and ended_at=ts
instead of deleting the entry. list_live_tmux_bindings filters out
None entries, so its observable contract is unchanged. _step is now
the single preserve-semantics fold used by both the live-list API
and the upcoming snapshot/tracker file writers."
```

---

### Task 5: History dedup on re-attach with new sid

Verify the dedup contract: re-attach with a sid already in history doesn't duplicate; re-attach with a new sid appends. The bulk of this was implemented in Task 3; this task adds explicit tests and any missing dedup hardening.

**Files:**

- Modify: `tests/test_bindings.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_history_appends_new_sid_on_reattach(tmp_path):
    p = tmp_path / "events.jsonl"
    _write_events(
        p,
        [
            _ev("session_start", "S1", ts="T1"),
            _ev("session_end", "S1", payload={"reason": "exit"}, ts="T2"),
            _ev("session_start", "S2", ts="T3"),
        ],
    )
    [out] = list_live_tmux_bindings(events_path=p)
    assert out.current_session_id == "S2"
    assert out.session_id_history == ("S1", "S2")
    assert out.ended_at is None


def test_history_dedups_resume_of_same_sid(tmp_path):
    p = tmp_path / "events.jsonl"
    _write_events(
        p,
        [
            _ev("session_start", "S1", ts="T1"),
            _ev("session_end", "S1", payload={"reason": "exit"}, ts="T2"),
            _ev("session_start", "S1", ts="T3"),  # --resume scenario
        ],
    )
    [out] = list_live_tmux_bindings(events_path=p)
    assert out.current_session_id == "S1"
    assert out.session_id_history == ("S1",), "resume must not duplicate"
    assert out.ended_at is None
```

- [ ] **Step 2: Run to confirm both fail (or pass — depends on Task 3 implementation)**

Run: `uv run --extra dev pytest tests/test_bindings.py::test_history_appends_new_sid_on_reattach tests/test_bindings.py::test_history_dedups_resume_of_same_sid -v`
Expected: both pass already if the Task 3 implementation is correct. If either fails, fix the `_step()` re-attach branch:

```python
# Inside _step(), in the "session_start with existing entry and current_session_id is None" branch:
if sid not in b.session_id_history:
    b.session_id_history.append(sid)
b.current_session_id = sid
b.ended_at = None
```

- [ ] **Step 3: Run all tests**

Run: `uv run --extra dev pytest -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "test(bindings): cover history append and dedup on re-attach"
```

---

### Task 6: `_atomic_write` helper with `fcntl.flock`

Single writer at a time; atomic from a reader's perspective via `os.replace`.

**Files:**

- Modify: `src/ccmux_core/bindings.py`
- Modify: `tests/test_bindings.py`

- [ ] **Step 1: Write a failing test for the basic atomic write**

```python
def test_atomic_write_creates_file_with_payload(tmp_path):
    from ccmux_core.bindings import _atomic_write

    path = tmp_path / "bindings.json"
    lock = tmp_path / "bindings.lock"
    _atomic_write(path, lock, {"ccmux": {"pane_id": "%1"}})

    assert path.exists()
    assert json.loads(path.read_text()) == {"ccmux": {"pane_id": "%1"}}


def test_atomic_write_leaves_no_tmp_file_on_success(tmp_path):
    from ccmux_core.bindings import _atomic_write

    path = tmp_path / "bindings.json"
    lock = tmp_path / "bindings.lock"
    _atomic_write(path, lock, {"a": 1})

    leftover = list(tmp_path.glob("*.tmp"))
    assert leftover == [], f"unexpected tmp files: {leftover}"
```

- [ ] **Step 2: Run to confirm fail**

Run: `uv run --extra dev pytest tests/test_bindings.py::test_atomic_write_creates_file_with_payload -v`
Expected: FAIL — ImportError (`_atomic_write` does not exist).

- [ ] **Step 3: Add the helper to `src/ccmux_core/bindings.py`**

Near the top of the file (after the imports), add:

```python
import fcntl
import json
import os
```

(`json` is already imported; keep one instance.)

At the bottom of the file, add:

```python
def _atomic_write(
    path: Path,
    lock_path: Path,
    data: dict,
) -> None:
    """Write ``data`` to ``path`` atomically, serialized through ``lock_path``.

    Uses an advisory ``fcntl.flock`` on ``lock_path`` for the duration
    of the write so that concurrent writers (the tracker + a manual
    ``bindings snapshot``) cannot race. Readers do not lock; the
    ``os.replace`` is atomic from their perspective.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(lock_path, "w") as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        try:
            tmp.write_bytes(serialized)
            os.replace(tmp, path)
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)
```

- [ ] **Step 4: Run tests**

Run: `uv run --extra dev pytest tests/test_bindings.py::test_atomic_write_creates_file_with_payload tests/test_bindings.py::test_atomic_write_leaves_no_tmp_file_on_success -v`
Expected: both pass.

- [ ] **Step 5: Add a serialization-under-contention test using threads**

```python
def test_atomic_write_is_serialized_under_contention(tmp_path):
    """Two threads racing for the lock; final file is one writer's output, not interleaved."""
    import threading

    from ccmux_core.bindings import _atomic_write

    path = tmp_path / "bindings.json"
    lock = tmp_path / "bindings.lock"
    big_a = {"k": "A" * 10000}
    big_b = {"k": "B" * 10000}

    def w(payload):
        for _ in range(10):
            _atomic_write(path, lock, payload)

    threads = [
        threading.Thread(target=w, args=(big_a,)),
        threading.Thread(target=w, args=(big_b,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # File is valid JSON and is exactly one of the two payloads
    # (whichever writer happened to be last).
    parsed = json.loads(path.read_text())
    assert parsed in (big_a, big_b)
```

- [ ] **Step 6: Run the contention test**

Run: `uv run --extra dev pytest tests/test_bindings.py::test_atomic_write_is_serialized_under_contention -v`
Expected: PASS. If it fails (file contains interleaved garbage), the flock is missing.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(bindings): atomic_write with flock arbitration

os.replace gives readers atomic visibility; fcntl.flock serializes
concurrent writers (tracker + manual snapshot). No fsync — derived
cache, snapshot CLI is the recovery path."
```

---

### Task 7: `load_bindings(path)` reader helper

Cheap typed reader for consumers. Returns `{}` when file is missing.

**Files:**

- Modify: `src/ccmux_core/bindings.py`
- Modify: `tests/test_bindings.py`

- [ ] **Step 1: Write failing tests**

```python
def test_load_bindings_missing_file_returns_empty(tmp_path):
    from ccmux_core.bindings import load_bindings

    path = tmp_path / "bindings.json"
    assert load_bindings(path) == {}


def test_load_bindings_reads_well_formed_file(tmp_path):
    from ccmux_core.bindings import load_bindings

    path = tmp_path / "bindings.json"
    payload = {"ccmux": {"pane_id": "%1", "current_session_id": "abc"}}
    path.write_text(json.dumps(payload))
    assert load_bindings(path) == payload
```

- [ ] **Step 2: Run to confirm fail**

Run: `uv run --extra dev pytest tests/test_bindings.py::test_load_bindings_missing_file_returns_empty tests/test_bindings.py::test_load_bindings_reads_well_formed_file -v`
Expected: FAIL — ImportError.

- [ ] **Step 3: Implement `load_bindings`**

Add at the bottom of `src/ccmux_core/bindings.py`:

```python
def load_bindings(path: Path) -> dict[str, dict]:
    """Read the bindings JSON file from disk. Returns ``{}`` if missing.

    Readers do not need to take ``flock`` because writers use
    ``os.replace`` for atomic visibility.
    """
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
```

- [ ] **Step 4: Run tests**

Run: `uv run --extra dev pytest tests/test_bindings.py::test_load_bindings_missing_file_returns_empty tests/test_bindings.py::test_load_bindings_reads_well_formed_file -v`
Expected: both pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(bindings): load_bindings reader helper"
```

---

### Task 8: `snapshot()` function — full-scan rebuild of bindings.json

Walk `events.jsonl` end-to-end, fold via `_step()`, atomic-write the resulting dict. Used by the CLI and as a recovery primitive.

**Files:**

- Modify: `src/ccmux_core/bindings.py`
- Modify: `tests/test_bindings.py`

- [ ] **Step 1: Write the failing test**

```python
def test_snapshot_writes_current_bindings(tmp_path):
    from ccmux_core.bindings import load_bindings, snapshot

    events = tmp_path / "events.jsonl"
    _write_events(
        events,
        [
            _ev("session_start", "S1", tmux="alpha", pane="%1", ts="T1"),
            _ev("session_start", "S2", tmux="beta", pane="%2", ts="T2"),
            _ev("session_end", "S2", tmux="beta", payload={"reason": "exit"}, ts="T3"),
        ],
    )
    out_path = tmp_path / "bindings.json"
    lock_path = tmp_path / "bindings.lock"

    written = snapshot(
        events_path=events,
        bindings_path=out_path,
        lock_path=lock_path,
    )
    assert written == 2, "two tmux sessions seen (one live, one ended)"

    data = load_bindings(out_path)
    assert set(data.keys()) == {"alpha", "beta"}
    assert data["alpha"]["current_session_id"] == "S1"
    assert data["alpha"]["ended_at"] is None
    assert data["beta"]["current_session_id"] is None
    assert data["beta"]["ended_at"] == "T3"
    assert data["beta"]["session_id_history"] == ["S2"]
```

Note: the existing `_ev` helper in `tests/test_bindings.py` already supports `session=` for the tmux session name. If the helper signature differs, adjust to match the helper's actual parameter names — read the file's `_ev` definition first.

- [ ] **Step 2: Run to confirm fail**

Run: `uv run --extra dev pytest tests/test_bindings.py::test_snapshot_writes_current_bindings -v`
Expected: FAIL — ImportError (`snapshot` does not exist).

- [ ] **Step 3: Implement `snapshot()`**

Add at the bottom of `src/ccmux_core/bindings.py`:

```python
def _mutable_to_dict(b: _MutableBinding) -> dict:
    return {
        "pane_id": b.pane_id,
        "window_id": b.window_id,
        "current_session_id": b.current_session_id,
        "session_id_history": list(b.session_id_history),
        "first_seen_at": b.first_seen_at,
        "last_event_at": b.last_event_at,
        "ended_at": b.ended_at,
    }


def snapshot(
    events_path: Path | None = None,
    bindings_path: Path | None = None,
    lock_path: Path | None = None,
) -> int:
    """One-shot full rebuild of the bindings file from ``events.jsonl``.

    Walks the entire event log, folds via :func:`_step`, atomic-writes
    the resulting dict. Returns the number of tmux sessions written.
    """
    from .config import ccmux_core_dir

    events_path = events_path if events_path is not None else _default_events_path()
    bindings_path = (
        bindings_path
        if bindings_path is not None
        else ccmux_core_dir() / "bindings.json"
    )
    lock_path = (
        lock_path
        if lock_path is not None
        else bindings_path.with_suffix(bindings_path.suffix + ".lock")
    )

    bindings: dict[str, _MutableBinding] = {}
    if events_path.exists():
        for raw_line in events_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            _step(bindings, ev)

    payload = {name: _mutable_to_dict(b) for name, b in bindings.items()}
    _atomic_write(bindings_path, lock_path, payload)
    return len(payload)
```

- [ ] **Step 4: Run the test**

Run: `uv run --extra dev pytest tests/test_bindings.py::test_snapshot_writes_current_bindings -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(bindings): snapshot() rebuilds bindings.json from events.jsonl

Full-scan recovery primitive shared by the CLI subcommand (next
commit) and any future drift-correction tooling."
```

---

### Task 9: `ccmux-core bindings snapshot` CLI subcommand

Thin CLI wrapper around `snapshot()`. Default path falls through to `${CCMUX_CORE_DIR}/bindings.json`; `--output PATH` overrides.

**Files:**

- Modify: `src/ccmux_core/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing parser + command tests**

In `tests/test_cli.py`, after the existing parser tests:

```python
def test_parser_bindings_snapshot_default_output():
    p = build_parser()
    args = p.parse_args(["bindings", "snapshot"])
    assert args.cmd == "bindings"
    assert args.bindings_cmd == "snapshot"
    assert args.output is None


def test_parser_bindings_snapshot_with_output():
    p = build_parser()
    args = p.parse_args(["bindings", "snapshot", "--output", "/tmp/x.json"])
    assert args.output == "/tmp/x.json"


def test_cmd_bindings_snapshot_writes_default_path(tmp_path, monkeypatch):
    """The default path is derived from CCMUX_CORE_DIR; redirect it for the test."""
    monkeypatch.setenv("CCMUX_CORE_DIR", str(tmp_path))
    # Empty events file in claude-tap's location — point at our tmp.
    events = tmp_path / "events.jsonl"
    events.write_text("")
    monkeypatch.setenv("CLAUDE_TAP_DIR", str(tmp_path))

    p = build_parser()
    args = p.parse_args(["bindings", "snapshot"])
    rc = args.fn(args)
    assert rc == 0
    assert (tmp_path / "bindings.json").exists()
```

- [ ] **Step 2: Run to confirm fail**

Run: `uv run --extra dev pytest tests/test_cli.py::test_parser_bindings_snapshot_default_output -v`
Expected: FAIL — unrecognized arguments / subcommand.

- [ ] **Step 3: Wire the subcommand into `build_parser()`**

In `src/ccmux_core/cli.py`, near where `p_watch` is added (around line 1020), add:

```python
p_bindings = sub.add_parser(
    "bindings", help="Inspect or rebuild the bindings cache file"
)
bindings_sub = p_bindings.add_subparsers(dest="bindings_cmd")
p_bindings_snapshot = bindings_sub.add_parser(
    "snapshot",
    help="One-shot full scan of events.jsonl, rewrite bindings.json",
)
p_bindings_snapshot.add_argument(
    "--output",
    default=None,
    help="Override the default ~/.ccmux-core/bindings.json path",
)
p_bindings_snapshot.set_defaults(fn=cmd_bindings_snapshot)
```

- [ ] **Step 4: Implement `cmd_bindings_snapshot`**

Add somewhere alongside `cmd_list` / `cmd_version`:

```python
def cmd_bindings_snapshot(args) -> int:
    from pathlib import Path

    from .bindings import snapshot

    output = Path(args.output).expanduser() if args.output else None
    count = snapshot(bindings_path=output)
    target = output if output is not None else "~/.ccmux-core/bindings.json"
    print(f"wrote {count} bindings to {target}", file=sys.stderr)
    return 0
```

- [ ] **Step 5: Run the tests**

Run: `uv run --extra dev pytest tests/test_cli.py::test_parser_bindings_snapshot_default_output tests/test_cli.py::test_parser_bindings_snapshot_with_output tests/test_cli.py::test_cmd_bindings_snapshot_writes_default_path -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(cli): ccmux-core bindings snapshot subcommand

Wraps bindings.snapshot() for manual one-shot rebuild of
bindings.json. --output PATH overrides the default location."
```

---

### Task 10: `BindingsTracker` async context manager

Live tail starting at EOF. Loads any existing bindings.json on enter, applies new events, writes on structural change, force-flushes on exit.

**Files:**

- Modify: `src/ccmux_core/bindings.py`
- Modify: `tests/test_bindings.py`

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.asyncio
async def test_tracker_writes_on_session_start(tmp_path, monkeypatch):
    from ccmux_core.bindings import BindingsTracker, load_bindings

    events = tmp_path / "events.jsonl"
    events.write_text("")
    bindings_path = tmp_path / "bindings.json"
    lock_path = tmp_path / "bindings.lock"

    async with BindingsTracker(
        events_path=events,
        bindings_path=bindings_path,
        lock_path=lock_path,
    ):
        # Append a session_start event after tracker enters
        events.write_text(json.dumps(_ev("session_start", "S1", ts="T1")) + "\n")
        # Give the tail coroutine a moment to pick it up
        for _ in range(20):
            await asyncio.sleep(0.05)
            if bindings_path.exists():
                data = load_bindings(bindings_path)
                if "ccmux" in data:
                    break
        else:
            raise AssertionError("tracker did not write within 1s")

    data = load_bindings(bindings_path)
    assert data["ccmux"]["current_session_id"] == "S1"


@pytest.mark.asyncio
async def test_tracker_seeks_to_eof_on_start(tmp_path):
    """Pre-existing events written before the tracker starts must NOT be folded."""
    from ccmux_core.bindings import BindingsTracker, load_bindings

    events = tmp_path / "events.jsonl"
    # Pre-existing event
    events.write_text(json.dumps(_ev("session_start", "OLD", ts="T0")) + "\n")
    bindings_path = tmp_path / "bindings.json"
    lock_path = tmp_path / "bindings.lock"

    async with BindingsTracker(
        events_path=events,
        bindings_path=bindings_path,
        lock_path=lock_path,
    ):
        # Append a fresh event after start
        with events.open("a") as f:
            f.write(json.dumps(_ev("session_start", "NEW", tmux="other", ts="T1")) + "\n")
        for _ in range(20):
            await asyncio.sleep(0.05)
            data = load_bindings(bindings_path)
            if "other" in data:
                break

    data = load_bindings(bindings_path)
    assert "other" in data, "post-start event must be folded"
    assert "ccmux" not in data, "pre-existing event must be skipped (seek to EOF)"
```

If `pytest-asyncio` is not yet configured for the project, the existing tests don't use it heavily — check for a `conftest.py` or `pyproject.toml` `tool.pytest.ini_options` block that sets `asyncio_mode`. ccmux-core already declares `pytest-asyncio>=0.24` as a dev dependency and the existing tests use the `@pytest.mark.asyncio` decorator pattern, so this should work out of the box.

- [ ] **Step 2: Run to confirm fail**

Run: `uv run --extra dev pytest tests/test_bindings.py::test_tracker_writes_on_session_start tests/test_bindings.py::test_tracker_seeks_to_eof_on_start -v`
Expected: FAIL — ImportError.

- [ ] **Step 3: Implement `BindingsTracker`**

At the bottom of `src/ccmux_core/bindings.py`:

```python
class BindingsTracker:
    """Live updater for ~/.ccmux-core/bindings.json.

    Runs as an async context manager inside a long-running consumer.
    On entry, loads any existing bindings.json into memory, seeks
    ``events.jsonl`` to EOF, starts a background task that tails new
    events and folds them via :func:`_step`. On every structural
    change (new entry, pane/window/current_session_id change, history
    append), atomically rewrites bindings.json. On exit, force-flushes.

    Bind a consumer like::

        async with BindingsTracker() as _tracker:
            await app.run_polling()
    """

    def __init__(
        self,
        events_path: Path | None = None,
        bindings_path: Path | None = None,
        lock_path: Path | None = None,
        poll_interval: float = 0.1,
    ) -> None:
        from .config import ccmux_core_dir

        self._events_path = (
            events_path if events_path is not None else _default_events_path()
        )
        self._bindings_path = (
            bindings_path
            if bindings_path is not None
            else ccmux_core_dir() / "bindings.json"
        )
        self._lock_path = (
            lock_path
            if lock_path is not None
            else self._bindings_path.with_suffix(self._bindings_path.suffix + ".lock")
        )
        self._poll_interval = poll_interval
        self._bindings: dict[str, _MutableBinding] = {}
        self._task: asyncio.Task | None = None
        self._stop = False

    async def __aenter__(self) -> "BindingsTracker":
        # Load existing bindings.json into our mutable mirror, if it exists.
        if self._bindings_path.exists():
            try:
                loaded = json.loads(self._bindings_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                loaded = {}
            for tmux_session, entry in loaded.items():
                self._bindings[tmux_session] = _MutableBinding(
                    tmux_session=tmux_session,
                    pane_id=entry.get("pane_id", ""),
                    window_id=entry.get("window_id", ""),
                    current_session_id=entry.get("current_session_id"),
                    session_id_history=list(entry.get("session_id_history", [])),
                    first_seen_at=entry.get("first_seen_at", ""),
                    last_event_at=entry.get("last_event_at", ""),
                    ended_at=entry.get("ended_at"),
                )
        self._task = asyncio.create_task(self._run())
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._stop = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        # Final flush — covers any last_event_at-only updates accumulated.
        self._flush()

    async def _run(self) -> None:
        # Wait for events.jsonl to exist, then seek to EOF.
        while not self._events_path.exists():
            if self._stop:
                return
            await asyncio.sleep(self._poll_interval)

        with open(self._events_path, encoding="utf-8") as f:
            f.seek(0, 2)  # EOF
            buf = ""
            while not self._stop:
                line = f.readline()
                if not line:
                    await asyncio.sleep(self._poll_interval)
                    continue
                buf += line
                if not buf.endswith("\n"):
                    continue
                try:
                    ev = json.loads(buf.rstrip("\n"))
                except json.JSONDecodeError:
                    buf = ""
                    continue
                buf = ""
                before = _snapshot_structural(self._bindings)
                _step(self._bindings, ev)
                after = _snapshot_structural(self._bindings)
                if before != after:
                    self._flush()

    def _flush(self) -> None:
        payload = {
            name: _mutable_to_dict(b) for name, b in self._bindings.items()
        }
        _atomic_write(self._bindings_path, self._lock_path, payload)
```

And add the structural-fingerprint helper, placed near `_mutable_to_dict`:

```python
def _snapshot_structural(
    bindings: dict[str, _MutableBinding],
) -> tuple[tuple, ...]:
    """Hashable fingerprint of the ``structural`` parts of the binding state.

    Excludes ``last_event_at``, which mutates on every event but does
    not warrant a disk write.
    """
    return tuple(
        (
            name,
            b.pane_id,
            b.window_id,
            b.current_session_id,
            tuple(b.session_id_history),
            b.first_seen_at,
            b.ended_at,
        )
        for name, b in sorted(bindings.items())
    )
```

- [ ] **Step 4: Run the tracker tests**

Run: `uv run --extra dev pytest tests/test_bindings.py::test_tracker_writes_on_session_start tests/test_bindings.py::test_tracker_seeks_to_eof_on_start -v`
Expected: both pass.

- [ ] **Step 5: Run the full test suite**

Run: `uv run --extra dev pytest -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(bindings): BindingsTracker async live updater

Tails events.jsonl from EOF, applies _step(), writes bindings.json
atomically on structural changes only. On __aenter__ loads any
existing bindings.json snapshot into the in-memory dict so consumers
restarting against a populated file resume cleanly. On __aexit__
force-flushes so accumulated last_event_at-only updates land before
exit."
```

---

### Task 11: CHANGELOG entry + final sanity sweep

**Files:**

- Modify: `CHANGELOG.md`

- [ ] **Step 1: Update CHANGELOG.md**

Add a new section at the top, above `[0.2.0]`:

```markdown
## [Unreleased]

### Added — bindings tracker

- `~/.ccmux-core/bindings.json` derived live cache of tmux ↔ Claude
  bindings, written incrementally by the new `BindingsTracker`
  async context manager and rebuildable on demand via the
  `ccmux-core bindings snapshot` CLI. Schema preserves
  `session_id_history` through `session_end` / `/clear` so users
  can `claude --resume <sid>` after an abnormal reboot.
  Atomic writes via `os.replace` + `fcntl.flock`; no `fsync`
  (snapshot is the recovery path).

### Changed (breaking)

- `discover` module is renamed to `bindings`. The `ccmux_core`
  package re-exports keep working without source changes for direct
  consumers of `TmuxBinding` / `list_live_tmux_bindings` /
  `discover_tmux_sessions`.
- `TmuxBinding` widens from 5 to 7 fields. `primary_session_id` is
  renamed to `current_session_id`. New fields:
  `session_id_history: tuple[str, ...]`, `first_seen_at: str`,
  `ended_at: str | None`.
- `list_live_tmux_bindings()` keeps its observable contract (returns
  only sessions with an attached Claude) but now implements it as a
  filter over the preserve-mode fold; the underlying dict retains
  ended entries with `current_session_id=None`.
```

- [ ] **Step 2: Run the whole suite + lint + format**

```bash
uv run --extra dev pytest -q
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog entry for bindings tracker (Unreleased)"
```

---

### Task 12: Open feature PR and merge to dev

**Files:** (none)

- [ ] **Step 1: Push the feature branch**

```bash
git push -u origin <branch-name>
```

Branch name should be `feature/bindings-tracker` if not already on that branch (the implementer may have used a different branch name in the worktree setup — keep whatever is in use).

- [ ] **Step 2: Open the PR**

```bash
gh pr create --base dev \
  --title "feat: bindings tracker + bindings.py module unification" \
  --body "$(cat <<'EOF'
## Summary

Implements the bindings tracker per
[2026-05-12-bindings-tracker-design.md](docs/superpowers/specs/2026-05-12-bindings-tracker-design.md).

- Renames `discover.py` → `bindings.py`; single preserve-semantics `_step()`.
- Widens `TmuxBinding` to 7 fields; `primary_session_id` → `current_session_id`.
- Adds `_atomic_write` / `load_bindings` / `snapshot()` / `BindingsTracker` / `ccmux-core bindings snapshot` CLI.
- Atomic writes via `os.replace` + `fcntl.flock`; no `fsync`.

## Test plan

- [ ] CI green on `pytest (py3.11)` + `pytest (py3.12)`
- [ ] CI green on `pre-commit (ruff + markdownlint)`
- [ ] After merge: cut v0.3.0 release
EOF
)"
```

- [ ] **Step 3: Wait for CI**

```bash
gh pr checks <pr-number> --watch --interval 15
```

- [ ] **Step 4: Merge with --merge --delete-branch**

```bash
gh pr merge <pr-number> --merge --delete-branch
git fetch origin --prune
git checkout dev && git pull
```

- [ ] **Step 5: Cut v0.3.0 release**

Follow the `managing-git-branches` skill's Release Flow with the PR-substitution variant (branch protection is on). The CHANGELOG `[Unreleased]` block is renamed to `[0.3.0] - YYYY-MM-DD` during the release branch's chore commit; `pyproject.toml` bumps to `0.3.0`.

---

## Definition of Done

- All tests in `tests/test_bindings.py` and `tests/test_cli.py` pass.
- `uv run ruff check src/ tests/` clean.
- `uv run ruff format --check src/ tests/` clean.
- `discover.py` and `test_discover.py` no longer exist.
- `bindings.py` exposes `TmuxBinding`, `_MutableBinding`, `_step`,
  `list_live_tmux_bindings`, `discover_tmux_sessions`,
  `_atomic_write`, `load_bindings`, `snapshot`, `BindingsTracker`.
- `ccmux-core bindings snapshot` writes a valid `bindings.json` when
  run against a non-empty `events.jsonl`.
- An ad-hoc `async with BindingsTracker():` block applied to a tmux
  session that emits hooks results in a live-updated `bindings.json`.
- CHANGELOG `[Unreleased]` documents the change.
- Feature branch merged to `dev` via PR; v0.3.0 release is the next
  natural step (handled by `managing-git-branches` skill, not this
  plan).
