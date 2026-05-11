# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-05-10

### Added

- Initial release. Per-tmux-session `Backend` async-context-manager
  exposing `states() / events() / messages() / spinners()` async
  iterators. State machine `Idle / Working / Blocked / Dead` driven
  by claude-tap hook events + ccmux-spinner safety net + tmux
  process probe. `list_live_tmux_bindings()` snapshot helper and
  `discover_tmux_sessions()` live stream. `ccmux-core list` and
  `ccmux-core watch <tmux_session>` debug CLI.
- See `docs/superpowers/specs/2026-05-10-ccmux-core-design.md` for
  design.
