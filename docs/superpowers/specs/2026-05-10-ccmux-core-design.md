<!-- markdownlint-disable MD024 -->

# ccmux-core v0.1 design

- **Date**: 2026-05-10
- **Repo**: `ccmux-core` (new)
- **Status**: design draft, awaiting user review
- **Targets**: ccmux-core v0.1.0

## Revision history

- **2026-05-10 (initial)** — initial design after iterative
  brainstorming. Empirically validated `/clear` and
  `prompt_input_exit` semantics against the user's
  `~/.claude-tap/events.jsonl` (2811 events); empirically validated
  Esc-interrupt does not fire a `stop` hook.

## Context

`claude-tap` v0.2 exposes two upstream async iterators —
`EventStream` (raw hook events) and `MessageStream` (derived
`ClaudeMessage` per turn). `ccmux-spinner` v0.2 exposes
`SpinnerMonitor` (live tmux pane spinner / idle decoration). Each is
the canonical source for its respective signal.

Downstream consumers (Telegram relay, monitor / HUD, IDE dashboards)
need three things on top of those primitives:

1. **A coarse session-state stream** — `Idle / Working / Blocked /
   Dead` — derived from the combination of hook events and pane
   observations, with all the subtleties of `/clear`, subagents,
   Esc-interrupts, and `prompt_input_exit` resolved once and for all
   in one library rather than rediscovered by each consumer.
2. **A per-tmux-session container** that bundles state + raw events +
   derived messages + live spinner into one async-iterator object
   keyed on the persistent identifier consumers actually care about
   (tmux session name, not claude session_id, which churns on
   `/clear`).
3. **Discovery helpers** that, on consumer restart, can recover the
   full `tmux_session ↔ pane_id ↔ primary_session_id` mapping from
   `events.jsonl` without consulting any separate state log.

`ccmux-core` is that composition layer. It owns the state machine
and the per-session orchestration; it does **not** re-implement
transcript parsing, pane parsing, or hook dispatch (which all stay
upstream in `claude-tap` and `ccmux-spinner`).

The package supersedes the original `ccmux-backend` (which
implemented its own pane parsing and transcript polling). The old
backend remains in place for existing consumers until those migrate;
there is no forced migration deadline in this spec.

## Empirical findings used in this design

All findings drawn from the user's local
`~/.claude-tap/events.jsonl` (2811 events, 7 sessions, captured
2026-05-08 → 2026-05-11).

### Esc-interrupt does not fire `stop`

Session `1aaafefd` at 23:07:56 UTC fired `pre_tool_use(Bash)`,
then 60 seconds of complete silence (no `post_tool_use`, no `stop`,
no `notification`, nothing), then `user_prompt_submit` arrived
("其实我觉得你搞复杂了..."). The user had pressed Esc to interrupt
the Bash. **No `stop` hook fired for the interrupted turn.**

Design consequence: a spinner-driven safety net is required to
move Working → Idle after Esc; otherwise sessions would pin
in Working indefinitely.

### `/clear` fires `session_end(reason="clear")` then `session_start` ~100 ms later

Observed three `/clear` chains in the user's data, all in
`tmux=ccmux`:

```text
sid=4de5b663 (session_end reason="clear")  → +0.1s session_start sid=69a3c45e
sid=69a3c45e (session_end reason="clear")  → +0.1s session_start sid=1aaafefd
sid=1aaafefd (session_end reason="clear")  → +0.1s session_start sid=504921bb
```

Same tmux session, new claude session_id each time. Design
consequence: per-tmux-session state must follow this transition
transparently (no spurious Dead).

### `prompt_input_exit` reuses the same session_id

Observed once: `4de5b663` ended with `reason="prompt_input_exit"`,
then 50.3 seconds later a `session_start` event reappeared with
**the same** `session_id=4de5b663`. The session paused and resumed,
not died and restarted.

Design consequence: `prompt_input_exit` must not trigger Dead
declaration in the state machine; the process probe and pane
capture mechanisms will catch the case where the process actually
died.

### `session_end` reasons distribution

```text
reason="clear":               3   (followed by session_start)
reason="prompt_input_exit":   1   (followed by same-sid session_start)
reason="" or missing:         3   (no rejoin within 60s — true death)
```

## Goals (v0.1)

1. Provide a per-tmux-session `Backend` async-context-manager class
   that exposes four typed async iterators: `states()`, `events()`,
   `messages()`, `spinners()`.
2. Implement a state machine (`Idle / Working / Blocked / Dead`)
   driven primarily by hook events and supplemented by spinner
   observations and tmux process probes, with full handling of
   `/clear`, `prompt_input_exit`, and subagent events.
3. Provide a synchronous `list_live_tmux_bindings()` helper that
   one-shot scans `events.jsonl` and returns current
   `tmux_session ↔ pane_id ↔ primary_session_id` mappings; used by
   consumers on restart to reconstruct in-memory state.
4. Provide an async `discover_tmux_sessions()` iterator that yields
   bindings for newly-observed tmux sessions, optionally including
   existing-on-subscribe ones.
5. Ship two debug CLI subcommands: `ccmux-core list` (snapshot) and
   `ccmux-core watch <tmux_session>` (dump all four streams to
   stdout as tagged JSON Lines).
6. Take **no runtime dependencies** beyond `claude-tap >= 0.2.0`
   and `ccmux-spinner >= 0.2.0`. Mirror the lean dependency
   philosophy of both upstreams.

## Non-goals (v0.1)

- **No persistence.** `ccmux-core` does not write its own event /
  state log. `events.jsonl` (owned by claude-tap) is the sole
  source of truth; restart-time reconstruction replays from there.
- **No multi-session orchestration logic.** One `Backend` watches
  exactly one tmux session. Consumers spawn multiple `Backend`
  instances if they want to track multiple tmux sessions.
- **No auto-resume.** The old `ccmux-backend` had a `Dead → respawn
  in new tmux window` flow with verification + circuit breaker.
  ccmux-core does not do this; on Dead the streams terminate and
  the consumer decides what to do.
- **No subagent topic routing.** Subagent events are silently
  filtered out of the state machine (so subagents do not pollute
  parent state), but `events()` / `messages()` do yield them so
  consumers can render them if desired. ccmux-core does not provide
  a "list subagents of session X" API.
- **No fan-out broadcast on iterators.** Each of `b.states()`,
  `b.events()`, `b.messages()`, `b.spinners()` supports a single
  concurrent iterator. Multiple subscribers within one process must
  create multiple `Backend` instances.
- **No following of pane moves.** `Backend(tmux_session, pane_id)`
  fixes the pane at construction. If the user `move-pane`s claude
  to a different pane mid-life, the Backend continues to watch the
  original pane. Consumer must dispose and reconstruct.
- **No `events.jsonl` rotation handling.** claude-tap currently
  does not rotate; if it ever does, this spec gets a follow-up.
- **No non-tmux deployments.** `tmux_session` and `pane_id` are
  required at Backend construction. Sessions run outside tmux are
  unsupported.

## Architecture

```text
            ┌────────────────────────────────────────┐
            │    claude (wrapped)                     │
            │  fires hooks → events.jsonl             │
            │  writes transcript → ~/.claude/...      │
            │  renders into → tmux pane               │
            └─────────────┬──────────────────────────┘
                          │
            ┌─────────────┼────────────────────────────────────┐
            │             │                                     │
            ▼             ▼                                     ▼
    ┌─────────────┐  ┌──────────────────┐         ┌──────────────────────┐
    │ claude-tap  │  │ claude-tap        │         │  ccmux-spinner       │
    │ EventStream │  │ MessageStream     │         │  SpinnerMonitor      │
    │ (global)    │  │ (global)          │         │  (per pane_id)       │
    └──────┬──────┘  └────────┬──────────┘         └──────────┬───────────┘
           │                  │                                │
           │                  │                                │
           ▼                  ▼                                ▼
    ┌───────────────────────────────────────────────────────────────┐
    │                    ccmux_core.Backend                          │
    │     (per tmux_session + pane_id, 1 instance = 1 watcher)       │
    │                                                                │
    │  internal tasks:                                               │
    │    • event consumer     (drives state_machine,                 │
    │                          filters by tmux.session_name)         │
    │    • message consumer   (filters by known_session_ids set)     │
    │    • spinner monitor    (feeds spinners + grace timer)         │
    │    • grace timer        (Working + no spinner ≥ N → Idle)      │
    │    • process probe      (tmux list-panes every M → Dead)       │
    └──────┬──────────────┬──────────────┬──────────────┬───────────┘
           │              │              │              │
           ▼              ▼              ▼              ▼
       b.states()    b.events()    b.messages()    b.spinners()
        (State)        (dict)      (ClaudeMessage)   (Activity)
```

### Module layout

```text
src/ccmux_core/
  __init__.py        — re-exports Backend, discover_tmux_sessions,
                        list_live_tmux_bindings, TmuxBinding, and all
                        State subclasses
  _version.py
  config.py          — settings.env loader + 5 env var getters
  state.py           — Idle / Working / Blocked / Dead frozen dataclasses
                        and the State union alias
  state_machine.py   — pure transition functions; takes (current_state,
                        primary_session_id, event) and returns
                        (new_state, new_primary, emit?)
  backend.py         — Backend class: __aenter__/__aexit__, four iterator
                        methods, all internal tasks
  discover.py        — TmuxBinding dataclass, list_live_tmux_bindings(),
                        discover_tmux_sessions()
  error.py          — exception types
  cli.py             — `ccmux-core list` and `ccmux-core watch <tmux>` subcommands

tests/
  test_state_machine.py       — table-driven, every transition row
  test_list_bindings.py       — synthetic events.jsonl traces
  test_backend.py             — feed events + patched capture_pane +
                                 patched tmux list-panes; assert outputs
  test_cli.py                 — invoke watch / list with mocks
  fixtures/
    events_clear_chain.jsonl
    events_subagent.jsonl
    events_esc_interrupt.jsonl
    events_prompt_input_exit.jsonl
```

Estimated implementation size: ~800–1000 lines plus tests.

## Data model

### State

```python
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Idle:
    """Session is between turns or interrupted.

    reason indicates how we got here:
      "start" — session just started (session_start), or rebound after /clear
      "stop"  — last turn ended cleanly (stop hook)
      "interrupted" — spinner grace timer fired (Esc-interrupt safety net)
    """
    reason: Literal["start", "stop", "interrupted"]


@dataclass(frozen=True)
class Working:
    """Session is processing a turn.

    tool_name is the most recent pre_tool_use's tool, or None if Working
    was entered via user_prompt_submit (or after a post_tool_use ended
    the previous tool without a new pre_tool_use arriving yet).
    """
    tool_name: str | None


@dataclass(frozen=True)
class Blocked:
    """Session is waiting on the user.

    kind indicates which subsystem is blocking:
      "permission"      — permission_request hook is open (run-tool dialog)
      "ask_user"        — pre_tool_use(AskUserQuestion); CC asked the user
      "exit_plan_mode"  — pre_tool_use(ExitPlanMode); CC awaits plan approval

    tool_name is the tool generating the block.
    tool_input is the raw payload.tool_input dict (for consumers that want
    to render the question or plan body).
    """
    kind: Literal["permission", "ask_user", "exit_plan_mode"]
    tool_name: str
    tool_input: dict | None


@dataclass(frozen=True)
class Dead:
    """Session is gone. Backend's iterators all terminate after this is emitted.

    reason indicates how:
      "session_end"  — session_end hook fired with a fatal reason
      "pane_lost"    — SpinnerMonitor raised PaneCaptureError
      "process_gone" — process probe found no claude/node in any pane
                        of the tmux session

    detail carries the raw session_end payload.reason when reason ==
    "session_end"; None otherwise.
    """
    reason: Literal["session_end", "pane_lost", "process_gone"]
    detail: str | None = None


State = Idle | Working | Blocked | Dead
```

`reason` and `kind` are typed as `Literal` so consumers can use
exhaustive `match`/`case` dispatch.

### TmuxBinding

```python
@dataclass(frozen=True)
class TmuxBinding:
    """Current mapping for one tmux session.

    Produced by list_live_tmux_bindings() (snapshot) and
    discover_tmux_sessions() (stream).
    """
    tmux_session: str           # tmux session name (e.g. "ccmux")
    pane_id: str                # tmux pane id (e.g. "%42")
    window_id: str              # tmux window id (e.g. "@7"), informational
    primary_session_id: str     # current bound claude session_id
    last_event_at: str          # ISO 8601 timestamp of most recent event
```

## Public API

### Backend

```python
class Backend:
    """Per-tmux-session async-context-manager wrapping claude-tap's streams
    and ccmux-spinner's monitor into one observable.

    Usage::

        async with Backend(tmux_session="ccmux", pane_id="%42") as b:
            async for state in b.states():
                handle(state)

    Both tmux_session and pane_id are required at construction. They
    can be obtained from list_live_tmux_bindings() / discover_tmux_sessions(),
    from a claude-tap event envelope, or by the caller out-of-band.

    The iterator methods (states / events / messages / spinners) each
    return a fresh AsyncIterator on each call. Each iterator supports
    a single concurrent consumer; calling the same method twice and
    iterating both simultaneously is undefined.
    """

    def __init__(
        self,
        tmux_session: str,
        pane_id: str,
        *,
        events_path: Path | None = None,
        spinner_grace: float | None = None,
        process_probe_interval: float | None = None,
        process_probe_startup_grace: float | None = None,
        claude_proc_names: frozenset[str] | None = None,
    ) -> None: ...

    async def __aenter__(self) -> "Backend": ...
    async def __aexit__(self, exc_type, exc, tb) -> None: ...

    def states(self) -> AsyncIterator[State]: ...
    def events(self) -> AsyncIterator[dict]: ...        # raw claude-tap Event dict
    def messages(self) -> AsyncIterator[ClaudeMessage]: ...
    def spinners(self) -> AsyncIterator[Activity]: ...  # Spinner | IdleDecoration | None
```

#### Constructor parameter defaults

All keyword-only parameters except `events_path` default to `None`,
in which case the value falls back to the corresponding
`CCMUX_CORE_*` env var (see [Settings](#settings)) or, failing
that, the documented default.

`events_path=None` defers to `claude_tap.config.events_path()`,
which honours `CLAUDE_TAP_DIR` upstream. Explicit `events_path` is
intended for tests; production callers leave it `None`.

#### Iterator semantics

- `states()`: yields a `State` on every state transition per the
  emission rules in [State machine](#state-machine). Drops the first
  emission's predecessor (consumers see an initial baseline state
  immediately after subscribe).
- `events()`: yields raw claude-tap `Event` dicts whose
  `tmux.session_name` matches the Backend's `tmux_session`. Includes
  subagent events.
- `messages()`: yields `ClaudeMessage` whose `session_id` is in the
  Backend's `known_session_ids` set (the union of all session_ids
  ever observed for this tmux_session — primary and subagents).
- `spinners()`: yields `Activity` (the union type from ccmux-spinner)
  on every pane change. Includes `None` (no spinner) and
  `IdleDecoration` (post-turn summary).

All iterators terminate cleanly when `Backend.__aexit__` runs, when
Dead is emitted on `states()`, or when the underlying upstream
stream signals end.

### discover_tmux_sessions

```python
async def discover_tmux_sessions(
    events_path: Path | None = None,
    include_existing: bool = True,
) -> AsyncIterator[TmuxBinding]:
    """Yield TmuxBinding on each new tmux session entering an observable
    state.

    A "new" tmux session is one this iterator has not yielded before
    in the current process.

    include_existing=True (default): first scans events.jsonl from the
    beginning, yields one TmuxBinding per currently-live tmux session
    (using list_live_tmux_bindings semantics), then continues tailing
    events.jsonl for genuinely new ones.

    include_existing=False: only yields on tmux session names seen for
    the first time in events.jsonl entries written after subscribe.

    Rebinds (a tmux session's primary_session_id changing via /clear)
    are NOT re-yielded; existing Backend instances follow rebinds
    internally and surface them via b.states().
    """
```

### list_live_tmux_bindings

```python
def list_live_tmux_bindings(
    events_path: Path | None = None,
) -> list[TmuxBinding]:
    """One-shot snapshot of currently-live tmux session bindings.

    Synchronous; opens events.jsonl, processes every entry, returns
    the current bindings dict converted to a list.

    "Live" means the most recent event for the tmux session leaves the
    binding in a state where primary_session_id is set (i.e., not in
    a post-/clear gap awaiting rebind, and not after a fatal
    session_end).

    Returns [] if events.jsonl does not exist, is empty, or contains
    no live bindings.

    Skips malformed JSONL lines without raising.
    """
```

## State machine

The state machine has three concerns, addressed in turn below:

1. **Primary session_id tracking** — which claude session_id is the
   "real" one for this tmux session, given that subagents share the
   tmux session and `/clear` swaps to a new id.
2. **Hook-driven state transitions** — what each event_type, applied
   to events whose `session_id == primary`, does to the current
   state.
3. **Safety-net transitions** — spinner timeout, pane loss, and
   process death, each driven outside the hook stream.

### Primary session_id tracking

The Backend maintains:

- `primary_session_id: str | None` — the currently-bound claude
  session_id for this tmux session. Starts `None` at Backend
  construction; bound on the first qualifying event.
- `known_session_ids: set[str]` — every session_id ever observed in
  any event for this tmux_session. Used for `messages()` filtering.
  Never shrinks.

For each event whose `tmux.session_name` matches the Backend's
tmux_session:

| event_type | `event.session_id` vs `primary` | Action |
|---|---|---|
| `session_start` | primary is None | bind primary = event.session_id; drive state machine with this event |
| `session_start` | event.session_id == primary | (resume after `prompt_input_exit`): no rebind; **skip state machine** — state stays at whatever it was before the pause |
| `session_start` | different from primary (which is set) | (subagent start): skip state machine; add to `known_session_ids` |
| `session_end` | event.session_id == primary, `payload.reason == "clear"` | clear primary; drop the state machine event (do not transition; await next session_start to rebind) |
| `session_end` | event.session_id == primary, `payload.reason == "prompt_input_exit"` | no change: keep primary, keep state, do not emit |
| `session_end` | event.session_id == primary, other reason | drive state machine (will produce `Dead`) |
| `session_end` | different from primary | (subagent end): skip state machine; keep in `known_session_ids` |
| any other | event.session_id == primary | drive state machine |
| any other | different from primary | skip state machine; add session_id to `known_session_ids` |

`known_session_ids.add(event.session_id)` happens unconditionally
for every event with a matching `tmux.session_name`, regardless of
primary status.

### Hook-driven transitions

Only applies to events where `event.session_id == primary` (per the
filter above) and `event_type` is **not** one of the
primary-tracking special cases (`session_start` with primary
already set, `session_end` with reason `clear` or
`prompt_input_exit`).

| event_type | new state |
|---|---|
| `session_start` | `Idle(reason="start")` |
| `user_prompt_submit` | `Working(tool_name=None)` |
| `pre_tool_use`, tool ∈ `{AskUserQuestion, ExitPlanMode}` | `Blocked(kind="ask_user" or "exit_plan_mode", tool_name=tool, tool_input=...)` |
| `pre_tool_use`, other tool | `Working(tool_name=tool)` |
| `permission_request` | `Blocked(kind="permission", tool_name=payload.tool_name, tool_input=payload.tool_input)` |
| `post_tool_use` | `Working(tool_name=None)` (clears tool field; next pre_tool_use will repopulate) |
| `stop` | `Idle(reason="stop")` |
| `session_end`, fatal reason | `Dead(reason="session_end", detail=payload.reason)` |
| `notification` | no transition (informational only) |

The `Working ↔ Blocked` distinction is purely tool-name-driven:
`AskUserQuestion` and `ExitPlanMode` are treated as blocking
because they wait synchronously for user input, while every other
tool is treated as non-blocking work-in-progress.

### Safety-net transitions

These run as internal tasks parallel to the event consumer, and
they emit state changes directly (without going through the hook
table above).

| Trigger | When applicable | Resulting state |
|---|---|---|
| `SpinnerMonitor` has yielded a non-`Spinner` activity (`None` or `IdleDecoration`) that has remained current for ≥ `spinner_grace` seconds, with no subsequent `Spinner` emit cancelling it | current state is `Working` and at least one `Spinner` has been observed | `Idle(reason="interrupted")` |
| `SpinnerMonitor` raises `PaneCaptureError` | current state is not `Dead` | `Dead(reason="pane_lost")` |
| Process probe (every `process_probe_interval` s, after `process_probe_startup_grace` s startup hold-off) finds no `claude_proc_names` in any pane of the tmux session | current state is not `Dead` | `Dead(reason="process_gone")` |

The grace timer keys on an **explicit observed transition** from
`Spinner` to non-`Spinner`, not on Spinner-emit silence.
`SpinnerMonitor` only emits on text change, so a spinner that
shows constant text for tens of seconds produces no emits even
though the pane has a live spinner; relying on emit silence
produces false positives. The first non-`Spinner` emit starts the
timer; any subsequent `Spinner` emit cancels it. Once the timer
exceeds `spinner_grace`, the grace transition fires. The very
first emit observed by the Backend is ignored if it is a
non-`Spinner` (cold start on a pane without a spinner does not
arm the timer until a Spinner has been seen at least once).

### Emission rules

State transitions are emitted to `b.states()` consumers when **any
field of the resulting state record differs** from the previous
state, with **one exception**: spinner observations and process
probe successes do not produce emissions on their own — they only
trigger emission when they cause a transition into `Idle(interrupted)`
or `Dead(*)` respectively.

Concretely, `b.states()` emits on transitions where the **new
state record is not equal** (by dataclass field-by-field
comparison) to the previously emitted record. The expected
trigger points are:

1. State class changes (`Idle ↔ Working ↔ Blocked ↔ Dead`).
2. `Working.tool_name` changes (`pre_tool_use` → new tool name;
   or `post_tool_use` → `None`). The comparison is value-equality
   on the `tool_name` field, so two consecutive `pre_tool_use`
   events for the **same** tool name produce only one emission.
3. `Blocked.kind` or `Blocked.tool_name` changes (rare but
   possible when a permission_request supersedes an in-flight
   ask_user, or similar).
4. `Idle.reason` changes (`start` → `stop` → `interrupted`, or
   any transition between these even if the state class itself
   doesn't change).
5. `Dead` is emitted exactly once, after which the state stream
   closes.

Identical consecutive transitions (`Working(Bash) → Working(Bash)`)
are coalesced and produce no emission.

### Initial emission

When a consumer first `async for`s `b.states()`, the Backend emits
the **current state** as the first record. This is the state
derived from replaying events.jsonl from the beginning (per
[Replay/live phase mechanism](#replaylive-phase-mechanism)) and
serves as the consumer's baseline.

If the Backend has not yet finished replay when the consumer starts
iterating, the first yield is delayed until replay completes.

If replay completes with **no primary session bound** (events.jsonl
exists but contains no events for this tmux session, or does not
exist at all), no state is emitted yet. The Backend waits for one
of: (a) a hook event for this tmux session arriving and binding a
primary (yields `Idle(reason="start")` as the first record), or
(b) a safety-net trigger firing — process probe finding no claude
in any pane, or SpinnerMonitor raising `PaneCaptureError` — which
yields `Dead(...)` as both the first and only record.

## Internal architecture

### Per-Backend tasks

Each `Backend` instance, on `__aenter__`, spawns five concurrent
asyncio tasks:

| Task | Lifecycle | Source | Output |
|---|---|---|---|
| Event consumer | starts immediately | `claude_tap.EventStream(from_start=True)` | drives state machine; pushes filtered events to `events()` queue; updates `known_session_ids` |
| Message consumer | starts when live phase entered | `claude_tap.MessageStream(from_start=False)` | filters by `known_session_ids`; pushes to `messages()` queue |
| Spinner monitor | starts when live phase entered | `ccmux_spinner.SpinnerMonitor(pane_id)` | pushes `Activity` to `spinners()` queue; feeds grace timer |
| Grace timer | starts when live phase entered | (in-process clock + spinner observations) | when state is `Working` and `seconds_since_last_active_spinner ≥ spinner_grace`, triggers `Idle(reason="interrupted")` transition |
| Process probe | starts when live phase entered, after `process_probe_startup_grace` | (in-process clock + tmux subprocess) | every `process_probe_interval` s, runs `tmux list-panes -t <tmux_session> -F '#{pane_current_command}'`; if intersection with `claude_proc_names` is empty, triggers `Dead(reason="process_gone")` |

Only the event consumer runs during replay phase; the others wait
for live phase to begin. This prevents safety-net triggers from
emitting `Dead` before the consumer has seen the initial baseline
state.

All five are awaited (and cancelled if still running) by
`__aexit__`.

**Known consequence**: if the state derived from replay is
`Working` but the underlying session is already Esc-interrupted at
subscribe time, the consumer sees a 5 s window of `Working`
followed by `Idle(interrupted)` (the spinner monitor needs 5 s of
consecutive non-Spinner polls to fire the grace timer; it cannot
"remember" pre-subscribe absence). Acceptable for v0.1.

### Replay/live phase mechanism

The Backend, on `__aenter__`, captures `subscribe_unix = time.time()`
and opens the event consumer with `from_start=True`. Each event is
classified:

- `event_unix = parse(event.timestamp).timestamp()`
- If `event_unix < subscribe_unix`: **replay phase**. The event is
  fed to the state machine (to derive the current primary, current
  state, and `known_session_ids`) but is **not** emitted to user
  queues.
- If `event_unix >= subscribe_unix`: **live phase**. The Backend has
  reached "now". On the first such event, the current state
  (computed from replay) is emitted as the baseline. Subsequent
  events drive emissions normally.

This mirrors `claude_tap.MessageStream`'s pattern and means no
separate "scan then tail" code path. The boundary is sharp enough
in practice (event timestamps are wall-clock ISO 8601 with
sub-second precision; subscribe_unix is a Python `time.time()`
in the same wall clock).

#### Live phase entry conditions

Live phase is considered entered when **any** of the following
becomes true (whichever fires first):

1. The event consumer processes an event whose `event_unix ≥
   subscribe_unix` (the normal case for an active session).
2. The event consumer has polled at EOF for ≥ `2 ×
   CLAUDE_TAP_POLL_INTERVAL` (default `0.2 s`) with no new event
   arriving (the case for sessions with only historical events,
   or sessions with no current activity).
3. 1.0 second has elapsed since `__aenter__` (a safety fallback
   for the degenerate case where `events.jsonl` does not exist or
   has no entries at all).

Once live phase is entered:

- The current state derived from replay (or `None` if no primary
  is bound) becomes the baseline.
- Message consumer, spinner monitor, grace timer, and process
  probe (with its startup grace counting from this moment) begin
  to run.
- All subsequent user-facing emissions are unsuppressed.

If `events.jsonl` does not exist at `__aenter__`, the event consumer
waits per `EventStream`'s existing logic; the 1.0 s safety fallback
covers this case and gets the safety-net tasks running so the
process probe can declare `Dead(process_gone)` if no claude is in
the tmux session.

### Stream filtering

- `events()`: yields any event with `event.tmux.session_name ==
  self.tmux_session`. Includes subagents. Includes all event types.
- `messages()`: yields any `ClaudeMessage` whose `session_id ∈
  known_session_ids`. The set is updated by the event consumer on
  every observed event, so messages from a session_id we have seen
  but not yet bound as primary (e.g., a freshly-started subagent)
  are still surfaced.
- `spinners()`: passes through `SpinnerMonitor`'s `Activity` items
  unchanged; no filtering.
- `states()`: emits per the emission rules above.

### Sharing of upstream streams

In v0.1, each `Backend` instance constructs its own
`EventStream` and `MessageStream`. Ten Backends in the same
process means ten tail loops on the same `events.jsonl`. This is
wasteful but correct; not a v0.1 concern. A future optimization
(see [Future work](#future-work-deferred)) would have ccmux-core
maintain a process-global event broker that Backends subscribe to.

## CLI

Two subcommands.

### `ccmux-core list`

```text
$ ccmux-core list
TMUX_SESSION   PANE_ID  WINDOW_ID  PRIMARY_SESSION_ID                    LAST_EVENT
ccmux          %42      @0         504921bb-b0f6-4d3c-8e95-...           2026-05-11T01:55:42Z
demo           %78      @0         a1b2c3d4-...                          2026-05-11T01:30:11Z
```

Synchronous; reads `events.jsonl` once via
`list_live_tmux_bindings()`, prints a tab-aligned table, exits.

### `ccmux-core watch <tmux_session>`

```text
$ ccmux-core watch ccmux
{"stream":"state","type":"Idle","reason":"start"}
{"stream":"event","event_type":"user_prompt_submit",...}
{"stream":"state","type":"Working","tool_name":null}
{"stream":"message","session_id":"504921bb...","role":"user","content_type":"text",...}
{"stream":"event","event_type":"pre_tool_use",...}
{"stream":"state","type":"Working","tool_name":"Bash"}
{"stream":"spinner","type":"Spinner","text":"Thinking 1s..."}
{"stream":"spinner","type":"Spinner","text":"Thinking 2s..."}
...
```

Resolves the binding for `tmux_session` via
`list_live_tmux_bindings()`. If not live, prints an error and
exits non-zero. Otherwise constructs `Backend(tmux_session, pane_id)`
and runs four concurrent tasks that each pump one stream to stdout
as JSON Lines.

JSON shape for `state` records: flat object with `stream`, `type`,
and the state's data fields directly. No nesting under `state:`.
JSON shape for other streams: the upstream dict / dataclass-asdict
with `stream` prepended; `messages` get image_data bytes encoded as
base64 if present (mirroring `claude-tap watch-messages`).

Ctrl-C terminates cleanly via `Backend.__aexit__`.

## Settings

`config.py` mirrors `claude_tap.config`'s `settings.env` discovery
pattern. Settings lookup order:

1. Shell environment variable (always wins).
2. `./settings.env` in current working directory.
3. `$CCMUX_CORE_DIR/settings.env`.

| Env var | Default | Meaning |
|---|---|---|
| `CCMUX_CORE_DIR` | `~/.ccmux-core` | State directory; `settings.env` loaded from here. |
| `CCMUX_CORE_SPINNER_GRACE` | `3` | Seconds Working with an observed non-Spinner activity staying current before falling back to `Idle(interrupted)`. |
| `CCMUX_CORE_PROCESS_PROBE_INTERVAL` | `10` | Seconds between successive process probes. |
| `CCMUX_CORE_PROCESS_PROBE_STARTUP_GRACE` | `10` | Seconds after Backend `__aenter__` during which no process probe runs (avoids false-positives during claude boot). |
| `CCMUX_CORE_CLAUDE_PROC_NAMES` | `claude,node` | Comma-separated set of foreground process names that count as "claude is alive". |

Upstream env vars (`CLAUDE_TAP_POLL_INTERVAL`,
`CCMUX_SPINNER_POLL_INTERVAL`, etc.) are read by the upstream
libraries directly; ccmux-core does not proxy them.

## Dependencies

| Package | Version | Reason |
|---|---|---|
| `claude-tap` | `>= 0.2.0` | `EventStream`, `MessageStream`, `ClaudeMessage` |
| `ccmux-spinner` | `>= 0.2.0` | `SpinnerMonitor(pane_id)`, `Activity`, `Spinner`, `IdleDecoration` |

No other runtime dependencies. dev/test deps follow the existing
lean stack (pytest, pytest-asyncio, ruff, pyright).

## Failure modes

| Condition | Behavior |
|---|---|
| `events.jsonl` does not exist at Backend `__aenter__` | Event consumer waits; first event appearing is treated as live. |
| `events.jsonl` malformed lines | Skipped by upstream `EventStream` and by `list_live_tmux_bindings`. |
| `tmux` binary missing | `SpinnerMonitor` raises `TmuxResolutionError` → Backend `__aenter__` raises. |
| `pane_id` does not exist | `SpinnerMonitor` raises `TmuxResolutionError` → Backend `__aenter__` raises. |
| Process probe subprocess fails (tmux not responding, transient error) | Log warning, skip this tick, continue. Does not kill the Backend. |
| Backend constructed for a tmux session with no live claude | Process probe declares `Dead(process_gone)` within `process_probe_startup_grace + process_probe_interval` ≈ 20 s. |
| Backend's `states()` iterator consumed after Dead is emitted | Naturally terminates (`StopAsyncIteration`). |
| Consumer calls `states()` (or any iterator) twice and iterates both concurrently | Undefined; v0.1 supports single-subscriber per iterator. |

## Known limitations (v0.1)

1. **Multiple Backends in one process duplicate event tailing**:
   each Backend opens its own `EventStream` and `MessageStream`. For
   small N (< ~20) this is fine; larger deployments should wait for
   the broker optimization.
2. **`events.jsonl` rotation is not handled**: claude-tap currently
   does not rotate. If a future claude-tap version rotates, replay
   may break. Addressed when claude-tap addresses rotation.
3. **Pane moves are not followed**: `Backend(tmux_session, pane_id)`
   fixes the pane at construction. Consumer must dispose and
   reconstruct if the user `move-pane`s claude elsewhere.
4. **Single subscriber per iterator**: see Failure modes.
5. **`prompt_input_exit` resume relies on same-session_id reuse**:
   if Claude Code's behavior changes such that resume gets a new
   session_id, the resume will be treated as either a new primary
   binding (if previous primary was unbound) or a subagent (if not).
   In either case state will not silently misalign, but the user
   experience may differ.

## Testing

### Unit tests

**`test_state_machine.py`** — `state_machine.transition(current,
primary, event)` is a pure function; tests are table-driven, one row
per cell in the transition tables above:

- Every `(event_type, current_state)` pair in the hook-driven table.
- Every primary-tracking action in the primary table.
- Every safety-net transition (simulated via the `event` argument
  being a synthetic `_TIMER`, `_PANE_LOST`, `_PROCESS_GONE`
  sentinel that the transition function recognizes).

**`test_list_bindings.py`** — `list_live_tmux_bindings` traced
against fixture event logs:

- Empty file → `[]`.
- One session_start, no end → one binding.
- Full `/clear` chain (the empirical scenario) → one binding with
  the final session_id as primary.
- `prompt_input_exit` then resume → one binding, primary unchanged.
- Subagent activity interleaved → one binding, primary unchanged
  (subagent never overrides).
- Fatal session_end (reason="" or "error") → empty list.
- Malformed JSONL lines mixed in → skipped, no crash.

### Backend integration tests

**`test_backend.py`** — patches `claude-tap`'s `EventStream`,
`MessageStream`, ccmux-spinner's `SpinnerMonitor` and `pane.capture_pane`,
and the `subprocess.run` calls used by process probe. Drives
synthetic event sequences and asserts emitted state / events /
messages / spinners.

Key scenarios:

- Clean turn (`user_prompt_submit` → `pre_tool_use` → `post_tool_use`
  → `stop`) → states `Idle(start) → Working(None) → Working(Bash)
  → Working(None) → Idle(stop)`.
- Esc interrupt (`pre_tool_use` then 5 s of `None` from spinner) →
  states `Working(Bash) → Idle(interrupted)`.
- Permission flow → `Working(?) → Blocked(permission, ...) →
  Working(...)`.
- AskUserQuestion → `Working(?) → Blocked(ask_user, AskUserQuestion,
  ...) → Working(...)`.
- /clear → state stays Idle through `session_end(clear)` → no
  spurious Dead → `Idle(start)` on the new session_start with new
  session_id; `messages()` correctly carries the new session's
  messages.
- prompt_input_exit then resume → no state change, no Dead.
- Subagent activity → state machine ignores subagent events;
  `events()` and `messages()` include them.
- Process probe → no claude in tmux session → `Dead(process_gone)`
  emitted; iterators terminate.
- Pane killed (capture_pane raises) → `Dead(pane_lost)`.

### CLI smoke tests

**`test_cli.py`** — invokes `ccmux-core list` and `ccmux-core watch
<session>` with a fake Backend; asserts stdout shape (table or JSON
Lines).

### Replay regression test

The current `~/.claude-tap/events.jsonl` snapshot (or a captured
copy) is used as a fixture. `list_live_tmux_bindings` against this
fixture must return exactly one binding `("ccmux", current_pane_id,
current_window_id, "504921bb...", <last_event_ts>)`. Any future
refactor that breaks this assertion gets flagged.

## Release plan

Standard git-flow with the existing CCMUX conventions:

1. Initialize new repo `ccmux-core/` (git init, README, LICENSE,
   pyproject.toml, src/ layout, tests/, CHANGELOG.md, .github/
   workflows from existing siblings).
2. Branch `feat/v0.1.0-initial` off `main` (or `dev` once dev
   exists).
3. Implement modules in order: `error.py` → `state.py` →
   `state_machine.py` → `discover.py` → `config.py` → `backend.py`
   → `cli.py` → `__init__.py`. Tests alongside each module.
4. Open PR; verify CI green (ruff / pyright / pytest).
5. Tag `v0.1.0`, push to PyPI.
6. **Blocking on**: `ccmux-spinner v0.2.0` released first.

No special migration is required from `ccmux-backend` users; that
remains a separate piece of work and is out of scope here.

## Future work (deferred, not in scope)

- **Process-global event broker** to share one `EventStream` /
  `MessageStream` across all Backends in the same process.
- **Multi-subscriber iterators** (fan-out queues) so multiple
  consumers in the same process can iterate the same Backend's
  stream concurrently.
- **Following pane moves** — detect `tmux.pane_id` change in the
  event envelope, gracefully restart `SpinnerMonitor` against the
  new pane.
- **Persistence checkpoint** (one-line key/value file with
  `events.jsonl` byte offset + per-tmux-session state snapshot) for
  fast restart once history grows past ~100 MB.
- **`events.jsonl` rotation handling**, coordinated with claude-tap.
- **`ccmux-core` daemon mode** (background process exposing a
  unix-socket API for query / subscribe) — only if real demand
  appears.
- **Subagent topology API** — `list_subagents_of(session_id)` and
  related — only if a consumer needs it.
- **Migration of existing `ccmux-backend` consumers** to ccmux-core
  + claude-tap MessageStream — separate piece of work.
