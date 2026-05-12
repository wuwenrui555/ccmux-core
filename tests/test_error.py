"""Tests for ccmux-core error types."""

from ccmux_core.error import (
    BackendError,
    BlockedError,
    BlockedExpiredError,
    DeadError,
    WrongBlockedKindError,
    WrongStateError,
)


def test_all_errors_inherit_from_backend_error():
    for cls in (
        BlockedError,
        BlockedExpiredError,
        DeadError,
        WrongBlockedKindError,
        WrongStateError,
    ):
        assert issubclass(cls, BackendError)


def test_errors_carry_messages():
    e = BlockedError("respond to the active dialog first")
    assert "active dialog" in str(e)


def test_each_error_class_is_distinct():
    # Ensure they're not aliases of one base class
    classes = {
        BlockedError,
        BlockedExpiredError,
        DeadError,
        WrongBlockedKindError,
        WrongStateError,
    }
    assert len(classes) == 5
