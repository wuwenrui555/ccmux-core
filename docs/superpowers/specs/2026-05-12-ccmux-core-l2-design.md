<!-- markdownlint-disable MD024 -->

# ccmux-core L2 design — state-gated operations + normalized message stream

- **Date**: 2026-05-12
- **Repo**: `ccmux-core`
- **Status**: design draft, awaiting user review
- **Targets**: ccmux-core v0.2.0 (single-Backend scope)
- **Builds on**: [2026-05-10-ccmux-core-design.md](2026-05-10-ccmux-core-design.md)

## Context

ccmux-core v0.1 (2026-05-10 design) shipped `Backend` as a four-stream
observation library (`states / events / messages / spinners`),
intended for downstream consumers like a Telegram relay. After
iterating with a real Telegram consumer in mind, two architectural
limitations surfaced:

1. **The four streams are conceptually peers, but they aren't.**
   `events` and `messages` overlap (the user prompt appears in both
   `user_prompt_submit` and as a User transcript turn; the final
   assistant reply appears in both `stop.last_assistant_message` and
   as the final Assistant transcript turn). Every consumer ends up
   re-deduplicating. `spinners` is plumbing for the state machine's
   safety net, not a primary consumer concern.

2. **Observation alone isn't enough.** A Telegram relay needs to
   send prompts back, interrupt working sessions, and respond to
   permission/AskUserQuestion/ExitPlanMode dialogs. These operations
   are intimately tied to `State` — `send_prompt` while `Working`
   should queue, not race; `respond_blocked` while `Idle` is
   nonsense. Without a state-gated operation face in core, every
   consumer reinvents the guard logic.

This spec extends ccmux-core with:

- **Three-layer model** (L0 raw observations / L1 derived state +
  normalized messages / L2 state-gated operations) that gives
  consumers a clearer mental model than "four streams".
- **A normalized `messages()` stream** that de-duplicates the
  overlap between hook events and transcript items so consumers
  don't have to.
- **A state-gated operation face** (`send_prompt`, `interrupt`,
  `respond_permission`, `respond_exit_plan`, `respond_question`,
  `drop_to_tui`, `send_keys`) where each operation's behavior is
  determined by the current `State`.

This spec covers **single-Backend scope only**. `MultiBackend`
orchestration and `decision.sock` ownership (which is necessarily
process-singleton) are deferred to a follow-up spec.

## Three-layer model

```text
┌─────────────────────────────────────────────────────────────┐
│  L2: state-gated operation face                             │
│      send_prompt / interrupt / respond_* / drop_to_tui      │
│      / send_keys                                            │
│      ↑ guarded by current state                             │
├─────────────────────────────────────────────────────────────┤
│  L1: derived signals                                        │
│      states()  ← apply(events) + safety_net(spinner)        │
│      messages() ← normalized fusion of events + transcript  │
├─────────────────────────────────────────────────────────────┤
│  L0: raw observation streams                                │
│      events()           ← claude-tap.EventStream            │
│      transcript_items() ← claude-tap.MessageStream          │
│      (spinners — internal safety-net input, not exposed)    │
└─────────────────────────────────────────────────────────────┘
```

The model has two faces that share `State` as the joint:

- **Subscription face** (L0 + L1 streams): consumers subscribe to
  whatever they want. Pure observers like dashboards use this only.
- **Operation face** (L2): consumers act on the session, with each
  action's semantics determined by current `State` (the joint).

`State` is both an L1 observation (you can subscribe to its
transitions) and the gate that determines what L2 operations are
legal. This is intentional — it's the single source of truth for
"what's happening in this session right now."

## Public API

```python
class Backend:
    # ----- L0: raw observation streams -----
    def transcript_items(self) -> AsyncIterator[TranscriptItem]: ...
    def events(self) -> AsyncIterator[dict]: ...
    # spinners is internal; consumers don't need it.

    # ----- L1: derived signals -----
    @property
    def state(self) -> State:
        """Current state snapshot. Synchronously accessible."""

    def states(self) -> AsyncIterator[State]: ...
    def messages(self) -> AsyncIterator[Message]: ...

    # ----- L2: state-gated operations -----
    async def send_prompt(self, text: str) -> None: ...
    async def interrupt(self) -> None: ...

    async def respond_permission(
        self, *,
        decision: Literal["allow", "deny"],
        mode: Literal["once", "always", "all", "bypass"] | None = None,
        message: str | None = None,
    ) -> None: ...

    async def respond_exit_plan(
        self, *,
        mode: Literal["manual", "autoAccept", "ultraplan", "deny"],
        feedback: str | None = None,
    ) -> None: ...

    async def respond_question(self, selections: list[str]) -> None: ...

    async def drop_to_tui(self) -> None: ...

    async def send_keys(
        self, keys: str | list[str], *, literal: bool = True,
    ) -> None: ...

    # ----- L2: introspection -----
    @property
    def pending_count(self) -> int: ...

    @property
    def pending_preview(self) -> str: ...
```

## State family (revised)

```python
@dataclass(frozen=True)
class Idle:
    reason: Literal["stop", "interrupted", "boot"]

@dataclass(frozen=True)
class Working:
    tool_name: str | None  # None between turns / pre-first-tool

@dataclass(frozen=True)
class Blocked:
    kind: Literal["permission", "exit_plan", "question"]
    tool_name: str
    tool_input: dict
    request_id: str | None
    expired: bool = False  # True after drop_to_tui() or socket timeout

@dataclass(frozen=True)
class Dead:
    reason: str
    detail: str | None

State = Idle | Working | Blocked | Dead
```

### Blocked sub-kinds

Derived in the state machine from the `tool_name` of the
`permission_request` hook event:

| `tool_name`          | `Blocked.kind`  |
|----------------------|-----------------|
| `AskUserQuestion`    | `"question"`    |
| `ExitPlanMode`       | `"exit_plan"`   |
| anything else        | `"permission"`  |

This sub-typing lets frontends switch on `kind` to render the right
UI (multi-choice keyboard for `question`, plan-mode selector for
`exit_plan`, allow/deny buttons for `permission`) and lets core
expose three specialized `respond_*` methods instead of a generic
opaque `respond(payload)`.

### `expired` flag

Set to `True` when either:

- `drop_to_tui()` was called (consumer explicitly released the
  socket), or
- the decision-socket round-trip timed out (`decision_timeout`
  elapsed without a `respond_*` call).

When `expired=True`:

- Structured `respond_*` methods raise (`BlockedExpiredError`).
- Frontend must use `send_keys()` to navigate the in-pane TUI
  fallback dialog.
- State remains `Blocked` until claude unblocks via TUI keypress
  (the next pane event will trigger the natural state transition).

## L1 Message family

The new normalized stream over `messages()`:

```python
@dataclass(frozen=True)
class UserPrompt:
    text: str
    timestamp: float

@dataclass(frozen=True)
class AssistantText:
    text: str
    timestamp: float

@dataclass(frozen=True)
class ToolCall:
    tool_name: str
    tool_input: dict
    timestamp: float

@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    output: str
    is_error: bool
    timestamp: float

@dataclass(frozen=True)
class PermissionRequest:
    tool_name: str
    tool_input: dict
    timestamp: float

Message = (
    UserPrompt | AssistantText | ToolCall
    | ToolResult | PermissionRequest
)
```

### Dedup source-of-truth table

Each message kind is sourced from exactly one upstream channel to
avoid duplicates:

| `Message` kind       | Source                                  | Why this source                                      |
|----------------------|-----------------------------------------|------------------------------------------------------|
| `UserPrompt`         | `events.user_prompt_submit`             | Fires earliest; transcript has latency               |
| `AssistantText`      | `transcript.Assistant.text`             | Richest content (incl. thinking); `events.stop` is just a summary |
| `ToolCall`           | `transcript.Assistant.tool_use`         | Full input object; events form is simplified        |
| `ToolResult`         | `transcript.tool_result`                | Contains actual output; `events.post_tool_use` is just timing |
| `PermissionRequest`  | `events.permission_request`             | Not in transcript before user response               |

### Explicitly excluded

- `Notification` events — they are control-plane signals, not
  conversational content. Consumers that want them subscribe to
  `events()` directly.
- `stop`, `session_end`, lifecycle events — already reflected in
  `state` transitions.

## L2 operation semantics

### `send_prompt(text)`

State-dispatched:

| Current state    | Behavior                                                  |
|------------------|-----------------------------------------------------------|
| `Idle`           | Defensive `Ctrl-U` (clear stale buffer) → `send_keys(text + Enter)` |
| `Working`        | Append to internal queue. On next `Idle`, flush all queued items as one concatenated prompt. |
| `Blocked`        | Raise `BlockedError("respond to active dialog first")`   |
| `Dead`           | Raise `DeadError`                                         |

#### Queue behavior

- **Concatenation, not sequential.** When the user types follow-up
  text while claude is still working, the natural intent is "extra
  context for the current turn," not "a separate turn." Concatenating
  with `\n\n` separator preserves the intent.
- **Flush on state transition to `Idle`.** When `states()` emits an
  `Idle` event and `pending_count > 0`, flush queue as one prompt.
- **Order preserved.** Items concatenate in append order.

#### Defensive `Ctrl-U`

Before sending a prompt in `Idle` state, core sends `Ctrl-U` to
clear any residue in the TUI input buffer. This is a belt-and-
suspenders measure: even if a prior `interrupt()` failed to clean
up, or the user interacted with the pane directly via SSH and left
stray input, the next `send_prompt` starts from a known-empty
buffer.

### `interrupt()`

Only meaningful in `Working`:

```python
async def interrupt(self) -> None:
    if not isinstance(self._state, Working):
        return  # no-op
    await self._send_keys_raw("Escape", literal=False)
    await self._send_keys_raw("C-u", literal=False)
    self._pending.clear()
```

Three actions in sequence:

1. **`Esc`** — interrupts claude's current turn. Side effect: claude
   restores the just-sent prompt to the TUI input buffer (for the
   user to edit and resubmit).
2. **`Ctrl-U`** — clears the restored prompt from the input buffer,
   so the next `send_prompt` doesn't get spliced onto the leftover
   text. **This is the "chrome cleanup" fix.**
3. **Clear pending queue** — interrupt semantics is "abort current
   work," which extends to "abort everything pending."

If `Ctrl-U` turns out to be insufficient in claude code's TUI
implementation (the input box is React/Ink-based), fall back to
`Ctrl-A Ctrl-K` (move-to-start, kill-to-end). `Ctrl-C` is **not**
safe — claude code interprets it as a quit signal.

### Blocked response methods

Three specialized methods, one per `Blocked.kind`. All raise
`BlockedExpiredError` if `state.expired`, and `WrongBlockedKindError`
if called with a mismatched kind.

#### `respond_permission(decision, mode, message)`

```python
# allow once
await b.respond_permission(decision="allow", mode="once")

# allow with persistent rule
await b.respond_permission(decision="allow", mode="always")

# deny with reason
await b.respond_permission(decision="deny", message="Don't run that")
```

Constructs and sends:

```json
{"hookSpecificOutput": {
    "hookEventName": "PermissionRequest",
    "decision": {
        "behavior": "allow" | "deny",
        "message": "...",
        "updatedPermissions": [...]
    }
}}
```

Where `updatedPermissions` for `mode in ["always", "all"]` is sourced
from the `permission_suggestions` field of the hook payload (carried
on `Blocked.tool_input`).

For `mode="bypass"` to take effect, claude must have been launched
with `--allow-dangerously-skip-permissions`. The frontend (or
launcher) is responsible for that flag; core just emits the JSON.

#### `respond_exit_plan(mode, feedback)`

```python
await b.respond_exit_plan(mode="manual")
await b.respond_exit_plan(mode="autoAccept")
await b.respond_exit_plan(mode="ultraplan")
await b.respond_exit_plan(mode="deny", feedback="add tests first")
```

Construction logic (mirrors cmux production):

- `manual` → `{behavior: "allow", updatedInput: <original toolInput>}`
- `autoAccept` → same as manual, plus
  `updatedPermissions: [{type: "setMode", mode: "auto", destination: "session"}]`
- `ultraplan` → `{behavior: "deny", message: "User chose Ultraplan via ccmux..."}` (claude has no native ultraplan; we deny with a directive)
- `deny` (with or without feedback) → `{behavior: "deny", message: ...}`

#### `respond_question(selections)`

```python
# single question, single answer
await b.respond_question(["postgres"])

# multiple questions, one answer each (positional)
await b.respond_question(["postgres", "synchronous"])
```

The `selections: list[str]` is positional — index `i` is the answer
to question `i`. Each entry is an option's `label` (not `id`).

For multi-select questions, selections per question are joined into
a single string (delimiter `", "`) before placing into the answers
map. This is a known limitation (mirrors cmux); revisit if
multi-select within a question becomes important.

Constructs:

```json
{"hookSpecificOutput": {
    "hookEventName": "PermissionRequest",
    "decision": {
        "behavior": "allow",
        "updatedInput": {
            "questions": [...original...],
            "answers": {"<question text>": "<selection>", ...}
        }
    }
}}
```

### `drop_to_tui()`

```python
async def drop_to_tui(self) -> None:
    if not isinstance(self._state, Blocked):
        raise WrongStateError("drop_to_tui requires Blocked state")
    if self._state.expired:
        return  # already dropped
    # Respond to decision.sock with empty {} → claude-tap returns {}
    # → claude falls through to native TUI dialog.
    await self._respond_decision_socket({})
    self._mark_expired()
```

Sets `Blocked.expired = True` and releases the socket. After this:

- Claude's native TUI permission/question/plan dialog appears in
  the pane.
- Structured `respond_*` methods raise.
- Frontend uses `send_keys()` to navigate the TUI (arrow keys,
  Enter, etc).
- State remains `Blocked` until the next pane event naturally
  transitions it.

### `send_keys(keys, *, literal=True)`

Low-level escape hatch:

```python
# send literal text
await b.send_keys("hello world")

# send named keys
await b.send_keys("Up", literal=False)
await b.send_keys(["Down", "Down", "Enter"], literal=False)
```

Frontend uses this to:

- Navigate TUI fallback dialogs (after `drop_to_tui`).
- Send unusual key combinations not covered by higher-level methods.

Available in any state; frontend is responsible for state
appropriateness.

### Key injection strategy (tmux send-keys vs TIOCSTI)

By default, all key-sending operations (`send_prompt`, `interrupt`,
`respond_*`, `send_keys`) go through `tmux send-keys`. This is the
stable, well-supported path.

**Problem**: when the user is in tmux copy mode (scrolling
scrollback to review output), `tmux send-keys` is intercepted by
copy-mode commands and never reaches claude. The naive fix —
exit copy mode first — destroys the user's scroll position and
selection.

**Solution**: detect copy mode via
`tmux display -p -t <pane> '#{?pane_in_mode,yes,no}'`. If the pane
is **not** in copy mode, use `tmux send-keys`. If it **is**, fall
back to TIOCSTI:

```python
import fcntl
import termios
import os

pane_tty = subprocess.check_output(
    ["tmux", "display", "-t", pane_id, "-p", "#{pane_tty}"],
    text=True,
).strip()
fd = os.open(pane_tty, os.O_RDWR | os.O_NOCTTY)
try:
    for byte in encoded_bytes:
        fcntl.ioctl(fd, termios.TIOCSTI, bytes([byte]))
finally:
    os.close(fd)
```

TIOCSTI injects characters directly into the slave-side input
buffer of the pty. Tmux's input routing (and therefore copy mode)
is bypassed entirely. Claude reads the injected bytes from stdin
as if the user had typed them. The user's copy-mode view stays
intact.

#### TIOCSTI prior art

The technique is widely used in production tools:

- `pyserial/pyserial` (miniterm)
- `Orange-Cyberdefense/arsenal` (security tooling)
- `gorilla-llm/gorilla-cli` (LLM CLI helper)
- `RetroPie/RetroPie-Setup` (joy2key joystick mapper)
- `bdring/FluidNC` (CNC controller)

Pattern is uniform: `fcntl.ioctl(fd, termios.TIOCSTI, byte)` one
byte at a time.

#### Deprecation status

Linux 6.2 introduced `CONFIG_LEGACY_TIOCSTI` allowing distributions
to disable TIOCSTI. As of 2026-05, mainstream distributions
(Ubuntu, Debian, Arch, Fedora) still ship with it enabled. Only
hardened distributions (e.g., Microsoft Azure Linux) disable it.
TIOCSTI is expected to remain available for 3–5 more years; once
mainstream distros start disabling it, we'll need a different
approach for the copy-mode case (most likely: refuse with a clear
error).

#### Key-name to bytes table (TIOCSTI path)

When using TIOCSTI, ccmux-core must convert named keys to raw
bytes itself (no tmux to translate). A fixed map covers our needs:

| Key name | Bytes (hex) | Notes |
|----------|-------------|-------|
| Enter / Return | `\r` | submit |
| Escape | `\x1b` | interrupt |
| C-u | `\x15` | clear line (readline) |
| C-a | `\x01` | move to start |
| C-k | `\x0b` | kill to end |
| C-c | `\x03` | **disallowed** (claude quits) |
| Up | `\x1b[A` | nav arrow |
| Down | `\x1b[B` | nav arrow |
| Left | `\x1b[D` | nav arrow |
| Right | `\x1b[C` | nav arrow |
| Tab | `\t` | completion |
| literal text | UTF-8 encoded | as-is |

The tmux-send-keys path uses tmux's native key names directly.
Both paths must accept the same input vocabulary; the dispatch
based on copy-mode happens transparently inside `send_keys`.

### Pane capture in copy mode (defensive change recommended)

Initial concern: ccmux-spinner uses `tmux capture-pane -p -J -t <pane>`
without `-S/-E`. Was suspected to read the user's scrolled viewport
when the pane is in copy mode, returning stale content for spinner
detection.

**Empirically tested 2026-05-12** (50-line bash session, pane 125×56,
copy mode with `scroll_position=30`, plus new output written while
viewport scrolled):

| Setup                              | Default capture returns           |
|------------------------------------|-----------------------------------|
| Not in copy mode                   | Active screen tail (LINE_46..50)  |
| Copy mode, viewport scrolled       | **Same — active screen tail**     |
| Copy mode, scrolled, new output    | **Live tail including new lines** |

Conclusion: `tmux capture-pane -p` returns the **active screen**
(where the program is writing), not the copy-mode viewport. The
scrolled viewport is purely a user-visible overlay; capture-pane
operates on the active screen buffer regardless of copy mode.

The current default behavior is **correct for ccmux-spinner's
present use case** (bash-like programs with normal pane sizes, on
Linux tmux).

#### Recommended defensive change

Despite the above, switch to explicit `-S -200 -E -`:

```python
["tmux", "capture-pane", "-p", "-J", "-t", pane_id, "-S", "-200", "-E", "-"]
```

Rationale:

- **Small panes**: with a 10-row pane, the default returns only 10
  rows; not enough context for some spinner-detection heuristics.
  `-S -200` always returns up to 200 rows of history-plus-current.
- **Cross-platform tmux variants**: OpenBSD vs Linux tmux
  occasionally diverge on edge cases of "visible region" semantics.
  Explicit `-S/-E` constrains the behavior precisely.
- **Future TUI changes**: if claude adds multi-row spinners,
  scrolling status bars, or similar, the extra history buffer is
  insurance against missing context.
- **Cost**: negligible. Spinner detection inspects only the last
  few rows; extra captured lines are discarded by the parser.

This is a one-line change to ccmux-spinner, not ccmux-core; tracked
here because ccmux-core depends on ccmux-spinner.

## Naming convention

Per user feedback memory (`feedback_module_names_singular.md`):

- Module names singular: `state.py`, `error.py`, `discover.py`,
  `backend.py`.
- Method names plural: `b.states()`, `b.events()`, `b.messages()`.

### `transcript_items` (not `transcript`)

Methods that yield items follow the rule above: each yield is one
item, so the method name is the plural. To avoid ambiguity with the
new L1 `messages()`, the L0 method is renamed from `messages()` to
`transcript_items()`. The plural form is verbose but consistent.

`transcript_items()` yields `ClaudeMessage` objects from
`claude-tap.MessageStream`.

## decision.sock interaction (single-Backend version)

A single `Backend` instance binds `~/.claude-tap/decision.sock` on
`__aenter__` and unbinds on `__aexit__`. Inside the listener loop:

- If the incoming `DecisionRequest`'s `session_id` matches this
  Backend's tracked claude session_id, store the `request_id`
  on the `Blocked` state and emit the state transition.
- If it doesn't match, respond with `{}` immediately (we don't own
  that session).

In `MultiBackend` (deferred to a follow-up spec), this listener is
hoisted to the top level and routes by `session_id` to the right
Backend.

## Errors

```python
class BackendError(Exception): ...
class BlockedError(BackendError): ...           # send_prompt while Blocked
class DeadError(BackendError): ...              # any L2 op while Dead
class WrongStateError(BackendError): ...        # respond_* in non-Blocked
class WrongBlockedKindError(BackendError): ...  # respond_question on Blocked.kind=permission
class BlockedExpiredError(BackendError): ...    # respond_* after expired=True
```

Errors are deliberately specific — frontends should reflect the
distinction to users (e.g., "respond first" vs "session died").

## Out of scope

- **`MultiBackend`** — multi-session orchestration. Deferred.
- **`decision.sock` arbitration across processes** — only one
  process can bind the socket; the single-Backend path assumes
  ccmux-core is that process. Multi-process coordination is
  deferred.
- **Pane parsing for TUI fallback** — after `drop_to_tui()`, the
  frontend can blindly `send_keys()` but has no visibility into
  the TUI dialog's current state. Pane parsing for TUI fallback is
  a Phase 2 enhancement, only if usage shows the blind path is
  insufficient.
- **Queue persistence** — pending queue lives in memory only.
  Backend restart clears it. Frontends needing recovery semantics
  must persist on their own.
- **Voice input, bash output scraping, status line rendering** —
  all frontend-layer concerns, not core.

## Open questions for next iteration

1. `decision_timeout` configurability — default 120s matches claude
   code's hook timeout (125s) minus margin. Should we allow `0` as
   a "pre-drop to TUI" mode? Pros: simple opt-in for users who
   prefer pane-driven workflow. Cons: more config surface.
2. Multi-select within a single AskUserQuestion — cmux joins
   selections with `", "`. Is that sufficient, or do we want a
   distinct response shape?
3. `claude-tap` README inconsistency — the example response shape
   uses `permissionDecision` directly under `hookSpecificOutput`,
   but cmux production uses `decision: {behavior: ...}`. We follow
   cmux; need to either confirm with claude-tap maintainer that
   their README is outdated, or empirically verify which form
   claude code accepts today.
4. `MultiBackend.attach()` lifecycle — when an attached session
   dies (state → `Dead`), does MultiBackend auto-detach? Eager
   cleanup vs. preserving the Dead state for frontend to display?

## Compatibility with v0.1

This is a breaking change to the v0.1 API:

- L0 `messages()` is renamed to `transcript_items()`.
- New L1 `messages()` returns a different shape (`Message` union,
  not `ClaudeMessage`).
- `spinners()` is removed from public API.
- New L2 operation methods are added.
- `Blocked` gains `kind`, `request_id`, `expired` fields.

Since v0.1 has no external consumers yet (ccmux-core is at 0.1.0
and no production code outside the repo depends on the L0 stream
shape), we don't bother with deprecation shims. Bump to v0.2.0 and
make the cut.
