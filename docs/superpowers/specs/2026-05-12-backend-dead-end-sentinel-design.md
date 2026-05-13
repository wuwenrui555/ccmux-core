<!-- markdownlint-disable MD024 -->

# Backend `_END` sentinel on Dead — terminate consumer iterators when the session dies

- **Date**: 2026-05-12
- **Repo**: `ccmux-core`
- **Status**: design draft, awaiting user review
- **Targets**: ccmux-core v0.3.1
- **Issue**: [#13](https://github.com/wuwenrui555/ccmux-core/issues/13)
- **Builds on**: [2026-05-12-ccmux-core-l2-design.md](2026-05-12-ccmux-core-l2-design.md)

## Context

`Backend` exposes five async iterators (`states`, `events`,
`transcript_items`, `messages`, `spinners`), each backed by an
`asyncio.Queue`. Each iterator's `async for` loop terminates by
reading a sentinel `_END` value from its queue.

`__aexit__` pushes `_END` into all five queues during teardown, so
any consumer doing `async for ... in iter()` inside the
`async with Backend(...)` block will wind down when the context
manager exits.

However, when `Backend` transitions to `Dead` mid-session (either
through `_event_consumer` observing a fatal `session_end`, or
through `_trigger_safety` firing on `process_gone` / `pane_lost` /
`spinner_grace`), the current code only sets `_stopped`. No `_END`
is pushed. The consumer is still inside the `async with` block,
its `async for` loop is still awaiting `q.get()`, so:

1. The consumer's iterator hangs forever (queue is empty, no
   producer pushing anything new, no `_END` either).
2. The consumer's `finally:` block never runs (cleanup, death
   notification, etc. are skipped).
3. `async with Backend(...)` never reaches `__aexit__` (consumer
   body never returns).
4. The `__aexit__` `_END` puts never happen. Deadlock.

The downstream `ccmux-core-telegram` (`cct`) v0.1.0 hit this in
production on 2026-05-12: killing the claude process triggered
`session_end` (reason=other) → Dead, but cct's
`async for msg in b.messages()` hung indefinitely and the 🪦
death-notification it was supposed to send to Telegram never went
out. See cct issues
[#1](https://github.com/wuwenrui555/ccmux-core-telegram/issues/1)
and [#2](https://github.com/wuwenrui555/ccmux-core-telegram/issues/2).

## Goals

- On every Dead transition (both event-driven and safety-net
  driven), unblock consumers of all non-self-terminating iterators
  so their `async for` loops return cleanly and their `finally:`
  blocks run.
- No change to `__aexit__` semantics: the extra `_END` puts during
  teardown remain harmless because each consumer returns on the
  first `_END` it sees, and asyncio.Queue is unbounded by default.
- Preserve the existing self-terminating behavior of `states()`
  (it returns after yielding `Dead`) and lock it in with a
  contract test.

## Non-Goals

- Version bump, CHANGELOG entry, downstream cct pin bump — handled
  in the implementation plan.
- Refactoring the broader async-task lifecycle in `Backend`
  (the five-task spawn, cancellation order, etc.).
- The cct-side `DeadError` fallback ([cct#1]) — tracked
  separately so that it is verified to be **independent of** this
  upstream fix rather than masking it.

[cct#1]: https://github.com/wuwenrui555/ccmux-core-telegram/issues/1

## Design

### `_terminate_consumer_iters` helper

Add a private synchronous method on `Backend`:

```python
def _terminate_consumer_iters(self) -> None:
    """Push _END into the four non-self-terminating consumer queues.

    Called when the Backend transitions to Dead, to release any
    consumer awaiting on messages() / events() / transcript_items()
    / spinners() so their async-for loops can wind down and
    'async with Backend(...)' can reach __aexit__.

    _states_q is excluded: states() returns immediately after
    yielding a Dead state (see Backend.states), so its queue
    doesn't need a sentinel push to terminate.

    Safe to call multiple times. __aexit__ also pushes _END to
    every queue; a second _END sitting unread in a queue is
    harmless (the consumer has already returned on the first one).
    """
    self._events_q.put_nowait(_END)
    self._messages_q.put_nowait(_END)
    self._spinners_q.put_nowait(_END)
    self._l1_messages_q.put_nowait(_END)
```

### Call sites

**`_event_consumer` Dead branch** (current backend.py:716-718):

```python
if isinstance(step.new_state, Dead):
    self._stopped.set()
    self._terminate_consumer_iters()
    return
```

**`_trigger_safety` Dead branch** (current backend.py:896-897):

```python
if isinstance(step.new_state, Dead):
    self._stopped.set()
    self._terminate_consumer_iters()
```

Both paths get identical treatment. Extracting the helper
(rather than duplicating four `put_nowait` lines) keeps the
queue list as a single source of truth — if a future queue
is added to `Backend`, only the helper needs to be updated.

### Queue scope: why four, not three, not five

| Queue | Push `_END` on Dead? | Reason |
| --- | --- | --- |
| `_states_q` | No | `states()` returns after yielding `Dead` (`backend.py:203-204`). The consumer never awaits another `get()`. |
| `_events_q` | Yes | `events()` has no Dead-aware self-termination; hangs without `_END`. |
| `_messages_q` | Yes | `transcript_items()` has no Dead-aware self-termination; hangs without `_END`. |
| `_l1_messages_q` | Yes | `messages()` has no Dead-aware self-termination; hangs without `_END`. This is the iterator cct was blocked on. |
| `_spinners_q` | Yes | **Beyond the issue body.** `_spinner_consumer` only checks `self._stopped.is_set()` *inside* the `async for activity in mon` loop. When the pane is quiet, SpinnerMonitor doesn't yield, the `break` never fires, `_spinners_q` receives no further items, and any `async for sp in b.spinners()` hangs by the same mechanism as the cct case. The issue body's note that "spinners_q termination happens when SpinnerMonitor exits" is correct in principle but the exit itself only triggers via task cancellation in `__aexit__` — which we can never reach. |

### Push order

Order across queues is irrelevant for correctness: each queue
backs an independent consumer task, and `put_nowait` is
synchronous (no scheduling point between calls). Within a queue,
order matters and is already correct: when Dead is reached, the
`Dead` state itself has already been pushed onto `_states_q` in
the `step.emit` block immediately above the Dead branch, so the
queue ends up as `[..., Dead, _END]`.

The helper writes in `__aexit__` order minus `_states_q`
(`events`, `messages`, `spinners`, `l1_messages`) purely for
reader pattern-matching against the existing teardown code.

### Interaction with `__aexit__`

`__aexit__` continues to push `_END` to all five queues, unchanged.
After this fix, the four non-state queues will receive `_END`
twice when Dead is reached: once eagerly in `_terminate_consumer_iters`,
and once during the eventual teardown. This is harmless:

- Each consumer's `async for` loop returns on the first `_END` it
  reads. Subsequent `_END` values sit unread in the queue.
- `asyncio.Queue` is unbounded by default (`maxsize=0`), so
  `put_nowait` cannot raise `QueueFull`.
- The queue is garbage-collected with the `Backend` instance.

### Failure modes considered

- **Re-entry**: If `_trigger_safety` fires and pushes Dead, and
  then `_event_consumer` separately observes a Dead-triggering
  event, both will call `_terminate_consumer_iters`. Each queue
  gets two extra `_END` puts. Still harmless (same reasoning as
  with `__aexit__`).
- **Consumer not iterating**: If a consumer never starts an
  `async for ... in b.messages()`, the queue accumulates items
  (`Dead`-related and otherwise) until `Backend` is GC'd. No
  behavioral change from today.
- **No consumer for one of the four iterators**: Same as above.
  The extra `_END` is queued and never read. Harmless.

## Tests

Add four tests to `tests/test_backend.py`, placed after the
Safety net tests section (after the test currently ending at
`tests/test_backend.py:552`):

### 1. `test_event_dead_terminates_all_iterators`

Drive a `session_end` event with a fatal reason via the existing
`_FakeEventStream`. Spawn four concurrent consumer tasks, one per
non-state iterator (`events`, `messages`, `transcript_items`,
`spinners`). Assert all four complete within a short timeout
(e.g., `asyncio.wait_for(..., timeout=1.0)`). Also assert that
`states()` yields a `Dead` and then returns.

### 2. `test_safety_net_dead_terminates_all_iterators`

Same shape as test 1, but induce Dead through `process_gone`
(reuse the `_NoClaude` patch pattern from
`test_backend_process_probe_declares_dead`). This covers the
`_trigger_safety` call site.

### 3. `test_messages_consumer_finally_runs_after_dead`

Regression test for the exact cct symptom. Spawn a task whose
body is:

```python
async def consume():
    try:
        async for msg in b.messages():
            ...
    finally:
        completed.set()
```

Trigger Dead. Assert `completed` is set within a short timeout.
This locks down the user-visible contract that motivated the
fix: consumer `finally:` blocks run after the session dies.

### 4. `test_states_iterator_self_terminates_on_dead`

Contract test for `states()`'s pre-existing self-terminating
behavior. The fix depends on this invariant
(`_states_q` is deliberately excluded from the helper); locking
it in costs ~10 lines and prevents accidental regression.

All four tests use existing fakes (`_FakeEventStream`,
`_FakeMessageStream`, `_FakeSpinnerMonitor`) and existing fixture
infrastructure (`_reset_fakes`). No new test infrastructure.

## Implementation order

1. Add `_terminate_consumer_iters` helper.
2. Wire it into `_event_consumer` Dead branch.
3. Wire it into `_trigger_safety` Dead branch.
4. Write the four tests; run them; assert they pass.
5. Re-run the full existing test suite; assert no regressions.

The version bump, CHANGELOG, and downstream cct pin update live
in the implementation plan, not in this spec.
