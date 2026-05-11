# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
