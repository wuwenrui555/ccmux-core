# Backend Dead `_END` Sentinel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. The user has overridden the inline executor to `executing-plans-test-first`. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix ccmux-core#13 so `Backend` releases all consumer iterators when a `Dead` transition occurs (both `_event_consumer` and `_trigger_safety` paths). Ship the fix as ccmux-core v0.3.1, then bump the downstream `ccmux-core-telegram` (cct) pin.

**Architecture:** Add a private `Backend._terminate_consumer_iters()` helper that pushes the `_END` sentinel into the four non-self-terminating queues (`_events_q`, `_messages_q`, `_spinners_q`, `_l1_messages_q`). Call it from both Dead branches. `_states_q` is intentionally skipped: `states()` already self-terminates after yielding `Dead`. TDD: write the cct-regression failing test first, fix the `_event_consumer` path, then add the safety-net failing test and fix the `_trigger_safety` path.

**Tech Stack:** Python 3.11+, asyncio, pytest + pytest-asyncio, ruff, pyright, hatchling, git-flow with PR-required branch protection (`main` and `dev`, required checks: pytest py3.11, pytest py3.12, pre-commit).

**Source of truth:** `docs/superpowers/specs/2026-05-12-backend-dead-end-sentinel-design.md` (committed on branch `docs/backend-dead-end-sentinel-spec` at `f4add30`). This plan is the *how*; the spec is the *what*.

---

## File Structure

**Phase A — bug fix (bugfix/backend-dead-end-sentinel branch):**

- Modify `src/ccmux_core/backend.py`:
  - Add `_terminate_consumer_iters` method on `Backend` in the
    "internal tasks" section (after the existing `_decision_consumer`,
    before `_event_consumer`).
  - Edit `_event_consumer` Dead branch (currently lines 716-718) to
    call the helper before `return`.
  - Edit `_trigger_safety` Dead branch (currently lines 896-897) to
    call the helper after `self._stopped.set()`.
- Modify `tests/test_backend.py`:
  - Append 4 new tests after the Safety net tests section
    (current end-of-section landmark: line 552 is the last test in
    that section; new tests go immediately after).

**Phase B — release (release/v0.3.1 branch):**

- Modify `pyproject.toml`: `version = "0.3.0"` → `"0.3.1"`.
- Modify `src/ccmux_core/_version.py`: `__version__ = "0.2.0"` → `"0.3.1"`.
  **Note:** `_version.py` was not bumped during the v0.3.0 release.
  This step incidentally fixes the discrepancy (CLI's `ccmux-core
  version` currently prints "0.2.0" despite pyproject declaring
  0.3.0). Treat this as a single forward bump to "0.3.1", not as
  re-litigating the v0.3.0 release.
- Modify `tests/test_skeleton.py`: assertion `ccmux_core.__version__
  == "0.2.0"` → `"0.3.1"`.
- Modify `CHANGELOG.md`: insert new `## [0.3.1] - 2026-05-12`
  section above the `## [0.3.0]` section with a `### Fixed`
  subsection.

**Phase C — downstream cct (separate repo, separate branch):**

- Modify `~/ccmux/ccmux-core-telegram/pyproject.toml`: dep line
  `"ccmux-core>=0.3.0"` → `"ccmux-core>=0.3.1"`.
- Modify `~/ccmux/ccmux-core-telegram/.github/workflows/ci.yml`:
  the install line `pip install
  git+https://github.com/wuwenrui555/ccmux-core.git@v0.3.0` →
  `@v0.3.1`.

---

## Phase A: Bug fix on bugfix branch

### Task 1: Branch setup

**Files:** none modified yet (git plumbing only).

- [ ] **Step 1: Verify clean dev**

Run:

```bash
cd ~/ccmux/ccmux-core
git status
git branch --show-current
git fetch origin
git log --oneline origin/dev..dev 2>/dev/null
git log --oneline dev..origin/dev 2>/dev/null
```

Expected: branch `dev`, working tree clean, no commits ahead/behind
`origin/dev`. If not on `dev`, run `git checkout dev && git pull`.

- [ ] **Step 2: Branch off dev**

Run:

```bash
git checkout -b bugfix/backend-dead-end-sentinel dev
```

Expected: `Switched to a new branch 'bugfix/backend-dead-end-sentinel'`.

- [ ] **Step 3: Merge spec branch into bugfix branch**

The spec is already committed on `docs/backend-dead-end-sentinel-spec`
(commit `f4add30`). Merging it in means a single PR carries both
spec and implementation.

Run:

```bash
git merge docs/backend-dead-end-sentinel-spec --no-ff
```

This will open an editor for the merge commit message — accept the
git default (`Merge branch 'docs/backend-dead-end-sentinel-spec'
into bugfix/backend-dead-end-sentinel`).

Expected: fast no-conflict merge; `git log --oneline -3` shows the
merge commit and the spec commit `f4add30`.

- [ ] **Step 4: Confirm spec file is present**

Run:

```bash
ls docs/superpowers/specs/2026-05-12-backend-dead-end-sentinel-design.md
```

Expected: file exists.

---

### Task 2: Contract test for `states()` self-termination on Dead

**Files:**

- Test: `tests/test_backend.py` (append)

This test passes immediately against current code (it locks in the
pre-existing self-terminating behavior of `states()` that the fix
relies on). Not strictly TDD — it's an invariant lock.

- [ ] **Step 1: Identify insertion point**

Run:

```bash
grep -n "^# ---\|test_backend_grace_does_not_fire_while_pane_changes_without_spinner\|test_backend_grace_fires_when_pane_static_and_no_spinner" tests/test_backend.py | head -10
```

Locate the end of the "Safety net tests" section. The last test
currently in that section ends near line 552. New tests are
appended directly after the closing `}` / final assertion of that
test, before the next `# ----` section banner.

For surgical insertion, add a new banner section above the new
tests:

```python
# ---------------------------------------------------------------------------
# Dead transition: consumer iterator termination (issue #13)
# ---------------------------------------------------------------------------
```

- [ ] **Step 2: Write the contract test**

Append to `tests/test_backend.py` (under the new banner from Step 1):

```python
@pytest.mark.asyncio
async def test_states_iterator_self_terminates_on_dead(monkeypatch):
    """states() returns after yielding Dead without needing _END.

    Contract test for the invariant the #13 fix relies on:
    _states_q is excluded from _terminate_consumer_iters precisely
    because states() ends itself after a Dead yield. If this
    invariant ever breaks, the fix's queue scope needs revisiting.
    """
    import ccmux_core.backend as bk

    later_ts = "2099-12-31T23:59:59+00:00"
    events = [
        _ev("session_start", sid="S1", ts=later_ts),
        _ev("session_end", sid="S1", payload={"reason": "other"}, ts=later_ts),
    ]
    monkeypatch.setattr(bk, "EventStream", lambda **kw: _FakeEventStream(events))

    out: list = []
    async with Backend(tmux_session="ccmux", pane_id="%1") as b:
        async def consume():
            async for s in b.states():
                out.append(s)

        await asyncio.wait_for(consume(), timeout=1.0)

    assert any(isinstance(s, Dead) and s.reason == "session_end" for s in out)
    # Critically: consume() returned without TimeoutError, i.e.
    # states() ended itself after yielding Dead — no _END needed.
```

- [ ] **Step 3: Run test, confirm pass**

Run:

```bash
uv run pytest tests/test_backend.py::test_states_iterator_self_terminates_on_dead -v
```

Expected: PASSED.

- [ ] **Step 4: Commit**

Run:

```bash
git add tests/test_backend.py
git commit -m "test(backend): lock states() self-termination on Dead (#13)"
```

---

### Task 3: TDD cycle 1 — `_event_consumer` Dead path

Write a failing cct-style regression test plus the broader
all-iterators test, then add the helper and wire it in. After
this task, both tests pass and the cct hang scenario is fixed.

**Files:**

- Test: `tests/test_backend.py` (append two tests)
- Modify: `src/ccmux_core/backend.py` (add helper, edit Dead branch)

- [ ] **Step 1: Write the failing regression test**

Append to `tests/test_backend.py` after the Task 2 test:

```python
@pytest.mark.asyncio
async def test_messages_consumer_finally_runs_after_dead(monkeypatch):
    """cct-style regression: async-for over messages() must wind
    down on Dead so consumer's finally: runs.

    Reproduces the 2026-05-12 cct v0.1.0 incident (issue #13): a
    consumer doing `async for msg in b.messages()` with a `finally:`
    block to send a 🪦 death notification never reached that block
    because messages() hung after Dead.
    """
    import contextlib

    import ccmux_core.backend as bk

    later_ts = "2099-12-31T23:59:59+00:00"
    events = [
        _ev("session_start", sid="S1", ts=later_ts),
        _ev("session_end", sid="S1", payload={"reason": "other"}, ts=later_ts),
    ]
    monkeypatch.setattr(bk, "EventStream", lambda **kw: _FakeEventStream(events))

    completed = asyncio.Event()
    async with Backend(tmux_session="ccmux", pane_id="%1") as b:
        async def consume():
            try:
                async for _msg in b.messages():
                    pass
            finally:
                completed.set()

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(completed.wait(), timeout=1.0)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    assert completed.is_set(), (
        "messages() consumer never reached finally: after Dead — "
        "iterator hung waiting on _l1_messages_q (issue #13)"
    )
```

- [ ] **Step 2: Write the failing all-iterators test for the event-driven Dead path**

Append immediately after the test from Step 1:

```python
@pytest.mark.asyncio
async def test_event_dead_terminates_all_iterators(monkeypatch):
    """When _event_consumer observes a fatal session_end and
    transitions to Dead, all four non-self-terminating iterators
    (events / messages / transcript_items / spinners) must return
    cleanly so 'async with Backend(...)' can reach __aexit__.
    """
    import ccmux_core.backend as bk

    later_ts = "2099-12-31T23:59:59+00:00"
    events = [
        _ev("session_start", sid="S1", ts=later_ts),
        _ev("session_end", sid="S1", payload={"reason": "other"}, ts=later_ts),
    ]
    monkeypatch.setattr(bk, "EventStream", lambda **kw: _FakeEventStream(events))

    async with Backend(tmux_session="ccmux", pane_id="%1") as b:
        async def drain(iterator_factory):
            async for _ in iterator_factory():
                pass

        tasks = [
            asyncio.create_task(drain(b.events)),
            asyncio.create_task(drain(b.messages)),
            asyncio.create_task(drain(b.transcript_items)),
            asyncio.create_task(drain(b.spinners)),
        ]
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=1.0)

    # If we got here without TimeoutError, every iterator returned
    # on its own — i.e., _terminate_consumer_iters successfully
    # released them when Dead was reached.
```

- [ ] **Step 3: Run both new tests, confirm both FAIL (hang to timeout)**

Run:

```bash
uv run pytest tests/test_backend.py::test_messages_consumer_finally_runs_after_dead tests/test_backend.py::test_event_dead_terminates_all_iterators -v
```

Expected: both FAIL with `TimeoutError` / `asyncio.exceptions.TimeoutError`
(consumer hung — exactly the #13 symptom).

- [ ] **Step 4: Add the `_terminate_consumer_iters` helper**

In `src/ccmux_core/backend.py`, find the `_decision_consumer`
method (currently around line 599). The helper goes between
`_decision_consumer` and `_event_consumer`.

Apply this edit — insert the new method immediately before the
existing `async def _event_consumer(self) -> None:` line (currently
line 622):

```python
    def _terminate_consumer_iters(self) -> None:
        """Push _END into the four non-self-terminating consumer queues.

        Called when the Backend transitions to Dead, to release any
        consumer awaiting on messages() / events() / transcript_items()
        / spinners() so their async-for loops can wind down and
        'async with Backend(...)' can reach __aexit__.

        _states_q is excluded: states() returns immediately after
        yielding a Dead state, so its queue doesn't need a sentinel
        push to terminate.

        Safe to call multiple times. __aexit__ also pushes _END to
        every queue; a second _END sitting unread in a queue is
        harmless (the consumer has already returned on the first one,
        and asyncio.Queue is unbounded by default).
        """
        self._events_q.put_nowait(_END)
        self._messages_q.put_nowait(_END)
        self._spinners_q.put_nowait(_END)
        self._l1_messages_q.put_nowait(_END)

```

(Note the blank line at the end so the next method's `async def`
keeps its leading blank.)

- [ ] **Step 5: Wire the helper into `_event_consumer` Dead branch**

In `src/ccmux_core/backend.py`, locate the current Dead branch at
the end of `_event_consumer` (lines 716-718 in the unmodified file):

```python
                    if isinstance(step.new_state, Dead):
                        self._stopped.set()
                        return
```

Edit it to:

```python
                    if isinstance(step.new_state, Dead):
                        self._stopped.set()
                        self._terminate_consumer_iters()
                        return
```

(Single inserted line; indentation matches surrounding code.)

- [ ] **Step 6: Run the two new tests, confirm both PASS**

Run:

```bash
uv run pytest tests/test_backend.py::test_messages_consumer_finally_runs_after_dead tests/test_backend.py::test_event_dead_terminates_all_iterators -v
```

Expected: both PASSED. (`_trigger_safety` path test from Task 4 is
not added yet, so it's not in this run.)

- [ ] **Step 7: Run the full test_backend.py to confirm no regressions**

Run:

```bash
uv run pytest tests/test_backend.py -v
```

Expected: all tests PASSED, including the existing 60+ tests and
the 3 new ones added so far (Task 2 + 2 from Task 3).

- [ ] **Step 8: Commit**

Run:

```bash
git add src/ccmux_core/backend.py tests/test_backend.py
git commit -m "fix(backend): terminate consumer iterators on _event_consumer Dead (#13)"
```

---

### Task 4: TDD cycle 2 — `_trigger_safety` Dead path

The safety-net Dead paths (`process_gone`, `pane_lost`,
`spinner_grace`) also need the same fix. Without it, a consumer
iterating `messages()` while the tmux pane disappears (or the
claude process dies under the process probe) still hangs.

**Files:**

- Test: `tests/test_backend.py` (append one test)
- Modify: `src/ccmux_core/backend.py` (edit `_trigger_safety` Dead branch)

- [ ] **Step 1: Write the failing safety-net all-iterators test**

Append to `tests/test_backend.py` after the Task 3 tests:

```python
@pytest.mark.asyncio
async def test_safety_net_dead_terminates_all_iterators(monkeypatch):
    """When _trigger_safety fires (process_gone here, but same path
    for pane_lost / spinner_grace) and transitions to Dead, all
    four non-self-terminating iterators must return cleanly.

    Mirrors the _event_consumer test but exercises the
    _trigger_safety call site.
    """
    import ccmux_core.backend as bk

    later_ts = "2099-12-31T23:59:59+00:00"
    events = [_ev("session_start", ts=later_ts)]
    monkeypatch.setattr(bk, "EventStream", lambda **kw: _FakeEventStream(events))

    class _NoClaude:
        returncode = 0
        stdout = "bash\nzsh\n"
        stderr = ""

    monkeypatch.setattr(bk.subprocess, "run", lambda *a, **kw: _NoClaude())

    async with Backend(
        tmux_session="ccmux",
        pane_id="%1",
        process_probe_startup_grace=0.05,
        process_probe_interval=0.05,
    ) as b:
        async def drain(iterator_factory):
            async for _ in iterator_factory():
                pass

        tasks = [
            asyncio.create_task(drain(b.events)),
            asyncio.create_task(drain(b.messages)),
            asyncio.create_task(drain(b.transcript_items)),
            asyncio.create_task(drain(b.spinners)),
        ]
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=2.0)
```

(Timeout is 2.0s rather than 1.0s because `process_probe_startup_grace`
plus a couple of probe intervals needs headroom.)

- [ ] **Step 2: Run the new test, confirm it FAILS (hang)**

Run:

```bash
uv run pytest tests/test_backend.py::test_safety_net_dead_terminates_all_iterators -v
```

Expected: FAILED with `TimeoutError`. (The `_event_consumer`
helper from Task 3 doesn't run here — Dead arrives via
`_trigger_safety` instead.)

- [ ] **Step 3: Wire helper into `_trigger_safety` Dead branch**

In `src/ccmux_core/backend.py`, locate the end of
`_trigger_safety` (currently lines 896-897 of the unmodified file):

```python
        if isinstance(step.new_state, Dead):
            self._stopped.set()
```

Edit to:

```python
        if isinstance(step.new_state, Dead):
            self._stopped.set()
            self._terminate_consumer_iters()
```

(Single inserted line; indentation matches surrounding code.)

- [ ] **Step 4: Run the new test, confirm PASS**

Run:

```bash
uv run pytest tests/test_backend.py::test_safety_net_dead_terminates_all_iterators -v
```

Expected: PASSED.

- [ ] **Step 5: Run the full test_backend.py to confirm no regressions**

Run:

```bash
uv run pytest tests/test_backend.py -v
```

Expected: all tests PASSED, including all 4 new tests.

- [ ] **Step 6: Commit**

Run:

```bash
git add src/ccmux_core/backend.py tests/test_backend.py
git commit -m "fix(backend): terminate consumer iterators on _trigger_safety Dead (#13)"
```

---

### Task 5: Pre-push checks, push, PR, merge

Per `managing-git-branches`, run the same checks CI runs *before*
pushing. Pre-commit covers ruff + ruff-format + markdownlint at
commit time, but pyright is not in pre-commit and must be run
by hand. Also re-run the full test suite (not just test_backend.py).

**Files:** none modified.

- [ ] **Step 1: Lint and format check**

Run:

```bash
cd ~/ccmux/ccmux-core
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

Expected: both report no issues. If `ruff format --check` reports
formatting drift, run `uv run ruff format src/ tests/` and commit
the result as a `style:` commit before continuing.

- [ ] **Step 2: Type check**

Run:

```bash
uv run pyright src/
```

Expected: 0 errors. If errors appear in `backend.py` related to
the new helper, fix them inline (likely import or annotation
issues) and amend the most recent fix commit using a new commit
(never `--amend`; per managing-git-branches "Always create NEW
commits rather than amending").

- [ ] **Step 3: Full test suite**

Run:

```bash
uv run pytest -v
```

Expected: all tests PASSED. Pay attention to anything in
`test_integration_l2.py` or `test_skeleton.py` — if `test_skeleton.py`
fails on the `__version__ == "0.2.0"` assertion, that means
someone bumped `_version.py` to a different value between branches.
Leave it as-is for now; the release task in Phase B will reconcile.

- [ ] **Step 4: Push branch**

Run:

```bash
git push -u origin bugfix/backend-dead-end-sentinel
```

Expected: branch created on origin, tracking set.

- [ ] **Step 5: Open PR to dev**

Run:

```bash
gh pr create --base dev --head bugfix/backend-dead-end-sentinel --title "fix(backend): terminate consumer iterators on Dead (#13)" --body "$(cat <<'EOF'
## Summary

Fixes #13. Backend now pushes the `_END` sentinel into the four
non-self-terminating consumer queues (`_events_q`, `_messages_q`,
`_spinners_q`, `_l1_messages_q`) when a `Dead` transition occurs,
so any `async for ... in b.messages() / events() / spinners() /
transcript_items()` winds down cleanly and `async with Backend(...)`
can reach `__aexit__`. `_states_q` is intentionally skipped
(`states()` self-terminates after yielding Dead).

Both Dead paths covered:

- `_event_consumer` (fatal `session_end`)
- `_trigger_safety` (`process_gone`, `pane_lost`, `spinner_grace`)

Spec: `docs/superpowers/specs/2026-05-12-backend-dead-end-sentinel-design.md`
(included in this PR).

## Test plan

- [x] `test_states_iterator_self_terminates_on_dead` (contract lock)
- [x] `test_messages_consumer_finally_runs_after_dead` (cct regression)
- [x] `test_event_dead_terminates_all_iterators`
- [x] `test_safety_net_dead_terminates_all_iterators`
- [x] Full local pytest + ruff + pyright clean

Downstream cct (`ccmux-core-telegram`) pin bump is a separate PR
after this lands and v0.3.1 is tagged.
EOF
)"
```

Expected: PR URL printed. Note the PR number.

- [ ] **Step 6: Wait for CI checks**

Run:

```bash
gh pr checks --watch
```

Expected: all three required checks pass (`pytest (py3.11)`,
`pytest (py3.12)`, `pre-commit (ruff + markdownlint)`).

If any check fails: fix the underlying issue, commit, push, and
re-run `gh pr checks --watch`. Never bypass branch protection
(no `--admin`, no force-push, no disabling the rule — see
`managing-git-branches` "Branch Protection" section).

- [ ] **Step 7: Merge PR**

Run:

```bash
gh pr merge --merge --delete-branch
```

Expected: PR merged via merge commit (preserves history like
`--no-ff`); remote branch deleted.

- [ ] **Step 8: Sync local dev**

Run:

```bash
git checkout dev
git pull
git branch -d bugfix/backend-dead-end-sentinel
git branch -d docs/backend-dead-end-sentinel-spec
```

Expected: local `dev` updated; both feature branches deleted
locally (they're already gone on origin via `--delete-branch`).

---

## Phase B: Release v0.3.1

### Task 6: Release branch — bump versions + CHANGELOG

**Files:**

- Modify `pyproject.toml`
- Modify `src/ccmux_core/_version.py`
- Modify `tests/test_skeleton.py`
- Modify `CHANGELOG.md`

- [ ] **Step 1: Create release branch**

Run:

```bash
cd ~/ccmux/ccmux-core
git checkout dev
git pull
git checkout -b release/v0.3.1 dev
```

Expected: on `release/v0.3.1`, dev fully merged in.

- [ ] **Step 2: Bump `pyproject.toml`**

Edit `pyproject.toml`. Change:

```toml
version = "0.3.0"
```

to:

```toml
version = "0.3.1"
```

- [ ] **Step 3: Bump `_version.py`**

Edit `src/ccmux_core/_version.py`. Change:

```python
__version__ = "0.2.0"
```

to:

```python
__version__ = "0.3.1"
```

(Yes, jumping from 0.2.0 straight to 0.3.1. The v0.3.0 release
forgot to bump this file; this single edit reconciles the
discrepancy and lands the new version in one move.)

- [ ] **Step 4: Update `test_skeleton.py` assertion**

Edit `tests/test_skeleton.py`. Find:

```python
    assert ccmux_core.__version__ == "0.2.0"
```

Change to:

```python
    assert ccmux_core.__version__ == "0.3.1"
```

- [ ] **Step 5: Add CHANGELOG entry**

Edit `CHANGELOG.md`. Insert a new section directly above the
existing `## [0.3.0] - 2026-05-12` section:

```markdown
## [0.3.1] - 2026-05-12

### Fixed

- `Backend` now releases all consumer iterators (`events()`,
  `messages()`, `transcript_items()`, `spinners()`) when the
  session transitions to `Dead`, so `async for` loops wind down
  and consumer `finally:` blocks run. Both Dead paths covered:
  fatal `session_end` (via `_event_consumer`) and the safety nets
  `process_gone` / `pane_lost` / `spinner_grace` (via
  `_trigger_safety`). Closes [#13]. `states()` already
  self-terminates after yielding `Dead` and is unchanged.
- `ccmux-core version` (and `ccmux_core.__version__`) now reports
  the actual installed version. The v0.3.0 release forgot to bump
  `_version.py`, leaving the runtime reporting "0.2.0"; this
  release reconciles `_version.py` to `0.3.1` alongside the
  `pyproject.toml` bump.

[#13]: https://github.com/wuwenrui555/ccmux-core/issues/13

```

(Mind the blank line at the end before the existing `## [0.3.0]`
header.)

- [ ] **Step 6: Verify version files are consistent**

Run:

```bash
grep -E '^version|^__version__|"0\.[0-9]\.[0-9]"' pyproject.toml src/ccmux_core/_version.py tests/test_skeleton.py
```

Expected output (or similar):

```
pyproject.toml:version = "0.3.1"
src/ccmux_core/_version.py:__version__ = "0.3.1"
tests/test_skeleton.py:    assert ccmux_core.__version__ == "0.3.1"
```

- [ ] **Step 7: Re-run full pre-push checks**

Run:

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/
uv run pytest -v
```

Expected: all green. `test_skeleton.py` now passes because
`__version__` matches the assertion.

- [ ] **Step 8: Commit**

Run:

```bash
git add pyproject.toml src/ccmux_core/_version.py tests/test_skeleton.py CHANGELOG.md
git commit -m "chore: bump version to 0.3.1 and update CHANGELOG"
```

(`chore:` per managing-git-branches commit conventions for version
bumps and release prep.)

---

### Task 7: PR release branch to main, merge, tag, push tag

`main` is PR-protected. Per managing-git-branches Release/Hotfix
specifics: open PR to main first, merge, tag locally, push tag.
Then open a separate PR for back-merge to dev (Task 8).

**Files:** none modified (git plumbing + remote actions).

- [ ] **Step 1: Push release branch**

Run:

```bash
git push -u origin release/v0.3.1
```

Expected: branch on origin.

- [ ] **Step 2: Open PR to main**

Run:

```bash
gh pr create --base main --head release/v0.3.1 --title "Release v0.3.1" --body "$(cat <<'EOF'
## Summary

Patch release: ships the #13 fix for consumer iterator termination
on Dead, and reconciles `_version.py` (forgotten during v0.3.0).

See `CHANGELOG.md` `[0.3.1]` for the full entry.

## Test plan

- [x] Full pytest green on `release/v0.3.1`
- [x] ruff / ruff-format / pyright clean
- [x] CHANGELOG and all three version-bearing files (pyproject.toml,
  _version.py, test_skeleton.py) updated and consistent

After main merges + tag: open the back-merge PR to dev.
EOF
)"
```

Note the PR number printed.

- [ ] **Step 3: Wait for CI**

Run:

```bash
gh pr checks --watch
```

Expected: all required checks green.

- [ ] **Step 4: Merge to main**

Run:

```bash
gh pr merge --merge
```

(Do **not** pass `--delete-branch` yet — the same branch is
needed for the dev back-merge PR in Task 8.)

Expected: merged.

- [ ] **Step 5: Sync local main**

Run:

```bash
git checkout main
git pull
```

Expected: local main has the merge commit.

- [ ] **Step 6: Tag v0.3.1 on main**

Run:

```bash
git tag v0.3.1 -m "v0.3.1: terminate consumer iterators on Backend Dead (#13)"
```

Expected: tag created. Verify with `git tag --list 'v0.3.*'`.

- [ ] **Step 7: Push tag**

Run:

```bash
git push origin v0.3.1
```

Expected: tag pushed. Verify the GitHub Releases page picks it up
(`gh release list` should now show v0.3.1 if release automation is
configured, otherwise just the tag is fine — no release-creation
step is in scope here).

---

### Task 8: Back-merge release branch to dev

**Files:** none modified (git plumbing).

- [ ] **Step 1: Open PR from release/v0.3.1 to dev**

Run:

```bash
gh pr create --base dev --head release/v0.3.1 --title "chore: back-merge v0.3.1 into dev" --body "Back-merge of the v0.3.1 release into dev. No new content beyond the main PR."
```

- [ ] **Step 2: Wait for CI**

Run:

```bash
gh pr checks --watch
```

Expected: green.

- [ ] **Step 3: Merge and clean up**

Run:

```bash
gh pr merge --merge --delete-branch
git checkout dev
git pull
git branch -d release/v0.3.1
```

Expected: dev now contains the release commit; release branch
deleted both locally and on origin.

- [ ] **Step 4: Verify post-release state**

Run:

```bash
git log --oneline -5 main
git log --oneline -5 dev
git tag --list 'v0.3.*'
```

Expected: both `main` and `dev` show the version bump commit
near the top; `v0.3.1` is in the tag list.

---

## Phase C: Downstream cct pin bump

cct (`~/ccmux/ccmux-core-telegram`) is a separate repo. It only has
a `main` branch (no `dev`), and `main` is PR-protected (assumption
based on the ccmux ecosystem convention; verify with
`gh api repos/wuwenrui555/ccmux-core-telegram/branches/main --jq
'.protection // "unprotected"'` before opening the PR).

### Task 9: Bump cct dep to ccmux-core 0.3.1

**Files:**

- Modify `~/ccmux/ccmux-core-telegram/pyproject.toml`
- Modify `~/ccmux/ccmux-core-telegram/.github/workflows/ci.yml`

- [ ] **Step 1: Verify clean main**

Run:

```bash
cd ~/ccmux/ccmux-core-telegram
git status
git checkout main
git pull
```

Expected: clean, up to date.

- [ ] **Step 2: Branch off main**

Run:

```bash
git checkout -b chore/bump-ccmux-core-0.3.1 main
```

(cct's branch naming so far: `chore/`, `feature/`, `fix/`,
`refactor/`. `chore/` fits a dep-pin bump.)

Expected: switched to new branch.

- [ ] **Step 3: Bump pyproject dep**

Edit `pyproject.toml`. Find:

```toml
    "ccmux-core>=0.3.0",
```

Change to:

```toml
    "ccmux-core>=0.3.1",
```

- [ ] **Step 4: Bump CI install line**

Edit `.github/workflows/ci.yml`. Find:

```yaml
          pip install git+https://github.com/wuwenrui555/ccmux-core.git@v0.3.0
```

Change to:

```yaml
          pip install git+https://github.com/wuwenrui555/ccmux-core.git@v0.3.1
```

- [ ] **Step 5: Verify both edits**

Run:

```bash
grep -E 'ccmux-core[>=@v]' pyproject.toml .github/workflows/*.yml
```

Expected: both lines now show `0.3.1`.

- [ ] **Step 6: Local pre-push checks**

Run (whatever cct's standard checks are; minimally):

```bash
uv run ruff check src/ tests/ 2>/dev/null || true
uv run pytest -v 2>/dev/null || true
```

cct's local dev uses `tool.uv.sources` to point `ccmux-core` at
`../ccmux-core` editable, so `uv sync` will pick up the locally
built v0.3.1 immediately and tests should run against the new
code. If tests pass, the dep bump is functionally validated.

If `uv` / `pytest` are not the configured runners for cct, fall
back to whatever the cct README / CI workflow specifies. Skip
checks gracefully — CI on the PR will catch real failures.

- [ ] **Step 7: Commit**

Run:

```bash
git add pyproject.toml .github/workflows/ci.yml
git commit -m "chore: bump ccmux-core dep to >=0.3.1"
```

---

### Task 10: cct PR + merge

**Files:** none modified.

- [ ] **Step 1: Push branch**

Run:

```bash
git push -u origin chore/bump-ccmux-core-0.3.1
```

- [ ] **Step 2: Open PR**

Run:

```bash
gh pr create --base main --head chore/bump-ccmux-core-0.3.1 --title "chore: bump ccmux-core dep to >=0.3.1" --body "$(cat <<'EOF'
## Summary

Picks up ccmux-core v0.3.1, which fixes the Backend Dead-time
iterator hang ([ccmux-core#13]). That hang was the root cause of
[cct#2] (🪦 death notifications not sent after kill) and unblocks
[cct#1] (DeadError fallback in cct).

Two file changes:

- `pyproject.toml` dep line: `ccmux-core>=0.3.0` → `>=0.3.1`
- `.github/workflows/ci.yml` install line: `@v0.3.0` → `@v0.3.1`

[ccmux-core#13]: https://github.com/wuwenrui555/ccmux-core/issues/13
[cct#1]: https://github.com/wuwenrui555/ccmux-core-telegram/issues/1
[cct#2]: https://github.com/wuwenrui555/ccmux-core-telegram/issues/2

## Test plan

- [x] Local tests pass against locally-built ccmux-core 0.3.1 via
  `tool.uv.sources` editable path
- [ ] CI green against `git+...@v0.3.1`

Follow-up: cct#1 (DeadError fallback) is a separate session.
EOF
)"
```

Note PR number.

- [ ] **Step 3: Wait for CI**

Run:

```bash
gh pr checks --watch
```

Expected: green. If the install step fails with "tag v0.3.1 not
found", the ccmux-core v0.3.1 tag was not pushed in Task 7 — go
back and confirm `git push origin v0.3.1` succeeded.

- [ ] **Step 4: Merge and clean up**

Run:

```bash
gh pr merge --merge --delete-branch
git checkout main
git pull
git branch -d chore/bump-ccmux-core-0.3.1
```

Expected: cct main now points to ccmux-core 0.3.1.

- [ ] **Step 5: Final verification (manual, out of plan scope)**

Optional sanity check: re-run the original cct reproducer to
confirm the 🪦 death notification now reaches Telegram. This is
the real-world acceptance test for #13. Per the original prompt
context, the cct test setup is still live in the `__cct__` tmux
session and the ccmux session bound to topic 2 is the one this
plan was created in. To run it: kill claude in the ccmux tmux
session (`tmux send-keys -t ccmux 'C-c'` is not sufficient — find
the claude PID and `kill <pid>`) and watch Telegram topic 2 for
the 🪦 message.

---

## Done criteria

After all 10 tasks:

- ccmux-core `main` is at v0.3.1 with the #13 fix merged and tagged.
- ccmux-core `dev` is in sync with `main` (back-merge complete).
- cct `main` is bumped to require ccmux-core 0.3.1, CI installs the
  new tag, all tests still pass.
- All four new tests live under `tests/test_backend.py` and pass.
- CHANGELOG `[0.3.1]` entry documents both the fix and the
  `_version.py` reconciliation.
- No follow-up work is outstanding except cct#1 (DeadError
  fallback), which is explicitly a separate session per the
  ccmux-core#13 brainstorm.
