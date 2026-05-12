def test_import_package():
    import ccmux_core

    assert ccmux_core.__version__ == "0.2.0"


def test_errors_importable():
    from ccmux_core.error import BackendError

    assert issubclass(BackendError, Exception)


def test_public_api_surface():
    """Verify all v0.2.0 public exports are reachable from ccmux_core."""
    import ccmux_core

    expected_names = {
        "__version__",
        # Lifecycle
        "Backend",
        # State family
        "Blocked",
        "Dead",
        "Idle",
        "State",
        "Working",
        # L1 messages
        "AssistantText",
        "Message",
        "PermissionRequest",
        "ToolCall",
        "ToolResult",
        "UserPrompt",
        # Errors
        "BackendError",
        "BlockedError",
        "BlockedExpiredError",
        "DeadError",
        "TmuxProbeError",
        "WrongBlockedKindError",
        "WrongStateError",
        # Discovery
        "TmuxBinding",
        "discover_tmux_sessions",
        "list_live_tmux_bindings",
    }
    missing = [name for name in expected_names if not hasattr(ccmux_core, name)]
    assert not missing, f"missing public exports: {missing}"
