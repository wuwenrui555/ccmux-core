# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-05-12

Breaking change: from a four-stream observation library to a
three-layer model with state-gated operations.

### Added

- L1 normalized `messages()` stream — deduplicated fusion of hook
  events and transcript items into a `Message` union
  (`UserPrompt | AssistantText | ToolCall | ToolResult |
  PermissionRequest`). All 5 emission paths shipped:
  - `UserPrompt` ← `events.user_prompt_submit`
  - `AssistantText` ← transcript Assistant text blocks
  - `ToolCall` ← `events.pre_tool_use` (full `tool_input`)
  - `ToolResult` ← `events.post_tool_use` (raw `tool_response`)
  - `PermissionRequest` ← `events.permission_request`
- L2 state-gated operations:
  - `send_prompt(text)` — Idle: send immediately;
    Working: append to concat queue (flushed on next Idle);
    Blocked/Dead: raise.
  - `interrupt()` — Esc + Ctrl-U + clear pending.
  - `respond_permission(decision, mode, message)`.
  - `respond_exit_plan(mode, feedback)`.
  - `respond_question(selections)`.
  - `drop_to_tui()` — release socket, fall through to TUI.
  - `send_keys(keys, literal)` — copy-mode aware (tmux send-keys
    default, TIOCSTI fallback when pane is in copy mode).
- `Blocked` now carries `request_id` and `expired` fields.
- New error types: `BlockedError`, `DeadError`, `WrongStateError`,
  `WrongBlockedKindError`, `BlockedExpiredError`.
- `decision.sock` listener bound automatically per Backend.

### Changed (breaking)

- L0 `messages()` renamed to `transcript_items()` so the L1 name
  is free for the new normalized stream.
- `Blocked.kind` discrimination moved from `pre_tool_use` to
  `permission_request` events; `AskUserQuestion` and `ExitPlanMode`
  fire through the permission hook (consistent with claude code's
  actual behavior, validated against cmux's production logic).

### Notes

- Single-Backend scope. `MultiBackend` orchestration and
  multi-process `decision.sock` arbitration are deferred to a
  follow-up spec.
- ccmux-spinner 0.2.2 is recommended (pinned capture-pane).

## [0.1.0] - 2026-05-11

### Added

- Per-tmux-session `Backend` async-context-manager exposing
  `states() / events() / messages() / spinners()` async iterators.
- State machine `Idle / Working / Blocked / Dead` driven by
  claude-tap hook events with three independent safety-net
  signals: spinner-grace (Esc-interrupt detection via
  ccmux-spinner), pane-lost (SpinnerMonitor `PaneCaptureError`),
  and process-gone (periodic `tmux list-panes` probe).
- Spinner-grace uses dual signals from `ccmux-spinner` v0.2.1:
  `mon.current` for "spinner visible right now" and
  `mon.last_pane_change_at` for "pane content fresh" — together
  they distinguish a streaming response (pane changing, no
  spinner visible) from an actual interrupt (pane static, no
  spinner) and avoid both false positives.
- `list_live_tmux_bindings()`: synchronous one-shot scan of
  `events.jsonl` returning the current per-tmux-session bindings
  (primary `session_id`, `pane_id`, `window_id`, last-event
  timestamp).
- `discover_tmux_sessions()`: async iterator yielding
  `TmuxBinding` records as new tmux sessions appear in the
  hook stream.
- `ccmux-core list` CLI subcommand for one-shot binding snapshot.
- `ccmux-core watch <tmux_session>` CLI subcommand with two
  output modes: default pretty (block-per-emission, mirrors
  `claude-tap watch-messages`, with in-place refresh on
  consecutive `SPINNER` blocks) and `--json` for tagged JSON
  Lines machine consumption. Message labels surface
  `ClaudeMessage.source` from claude-tap v0.2.1+ so consumers
  can distinguish final replies (`hook`) from mid-turn narration
  (`transcript`).
- Six native config knobs under the `CCMUX_CORE_*` namespace:
  `_DIR`, `_SPINNER_GRACE`, `_PROCESS_PROBE_INTERVAL`,
  `_PROCESS_PROBE_STARTUP_GRACE`, `_CLAUDE_PROC_NAMES`,
  `_PRETTY_WIDTH`. Three facade aliases that mirror to upstream
  env vars: `_HOOK_POLL_INTERVAL`,
  `_HOOK_POLL_MAX_DURATION`, `_SPINNER_POLL_INTERVAL`. All read
  from `~/.ccmux-core/settings.env` (or shell exports).
- Pre-commit hooks (ruff + ruff-format + markdownlint-cli) and
  GitHub Actions CI (pytest matrix py3.11 + py3.12, plus
  pre-commit lint job).
- Design spec at
  `docs/superpowers/specs/2026-05-10-ccmux-core-design.md`.

### Dependencies

- `claude-tap >= 0.2.1` — uses `MessageStream` and
  `ClaudeMessage.source`.
- `ccmux-spinner >= 0.2.1` — uses `SpinnerMonitor`, `current`,
  and `last_pane_change_at`.
