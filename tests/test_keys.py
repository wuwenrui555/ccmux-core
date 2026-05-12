"""Tests for the key-injection layer."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from ccmux_core.keys import KeyInjectionError, send_via_tmux


def test_send_via_tmux_literal_text():
    with patch("subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stderr = ""
        send_via_tmux(pane_id="%0", keys="hello", literal=True)
    args = run.call_args[0][0]
    assert args[:3] == ["tmux", "send-keys", "-t"]
    assert args[3] == "%0"
    # literal flag -l present
    assert "-l" in args
    assert "hello" in args


def test_send_via_tmux_named_key():
    with patch("subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stderr = ""
        send_via_tmux(pane_id="%0", keys="Enter", literal=False)
    args = run.call_args[0][0]
    # no literal flag for named keys
    assert "-l" not in args
    assert "Enter" in args


def test_send_via_tmux_list_of_keys():
    with patch("subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stderr = ""
        send_via_tmux(pane_id="%0", keys=["Up", "Up", "Enter"], literal=False)
    args = run.call_args[0][0]
    assert "Up" in args
    assert "Enter" in args


def test_send_via_tmux_raises_on_failure():
    with patch("subprocess.run") as run:
        run.return_value.returncode = 1
        run.return_value.stderr = "can't find pane"
        with pytest.raises(KeyInjectionError) as excinfo:
            send_via_tmux(pane_id="%0", keys="hi", literal=True)
        assert "can't find pane" in str(excinfo.value)


def test_keyname_to_bytes_table_covers_required_keys():
    from ccmux_core.keys import KEYNAME_TO_BYTES

    assert KEYNAME_TO_BYTES["Enter"] == b"\r"
    assert KEYNAME_TO_BYTES["Escape"] == b"\x1b"
    assert KEYNAME_TO_BYTES["C-u"] == b"\x15"
    assert KEYNAME_TO_BYTES["Up"] == b"\x1b[A"
    assert KEYNAME_TO_BYTES["Down"] == b"\x1b[B"


def test_send_via_tiocsti_writes_literal_text():
    from unittest.mock import patch

    from ccmux_core.keys import send_via_tiocsti

    with (
        patch("ccmux_core.keys._get_pane_tty", return_value="/dev/pts/0"),
        patch("os.open", return_value=42) as op_open,
        patch("os.close") as op_close,
        patch("fcntl.ioctl") as ioctl,
    ):
        send_via_tiocsti(pane_id="%0", keys="hi", literal=True)

    # 2 chars × 1 ioctl each
    assert ioctl.call_count == 2
    op_open.assert_called_once()
    op_close.assert_called_once_with(42)


def test_send_via_tiocsti_named_key_resolves_to_bytes():
    from unittest.mock import patch

    from ccmux_core.keys import send_via_tiocsti

    with (
        patch("ccmux_core.keys._get_pane_tty", return_value="/dev/pts/0"),
        patch("os.open", return_value=42),
        patch("os.close"),
        patch("fcntl.ioctl") as ioctl,
    ):
        send_via_tiocsti(pane_id="%0", keys="Enter", literal=False)

    # one byte for '\r'
    ioctl.assert_called_once()
    # third positional arg of ioctl is the single-byte bytes object
    args = ioctl.call_args[0]
    assert args[2] == b"\r"


def test_send_via_tiocsti_unknown_named_key_raises():
    from unittest.mock import patch

    from ccmux_core.keys import KeyInjectionError, send_via_tiocsti

    with (
        patch("ccmux_core.keys._get_pane_tty", return_value="/dev/pts/0"),
        patch("os.open", return_value=42),
        patch("os.close"),
    ):
        with pytest.raises(KeyInjectionError) as excinfo:
            send_via_tiocsti(pane_id="%0", keys="NotAKey", literal=False)
        assert "NotAKey" in str(excinfo.value)


def test_send_via_tiocsti_closes_fd_on_ioctl_failure():
    """If ioctl raises, the fd must still be closed."""
    from unittest.mock import patch

    from ccmux_core.keys import KeyInjectionError, send_via_tiocsti

    with (
        patch("ccmux_core.keys._get_pane_tty", return_value="/dev/pts/0"),
        patch("os.open", return_value=42),
        patch("os.close") as op_close,
        patch("fcntl.ioctl", side_effect=OSError("no perms")),
    ):
        with pytest.raises(KeyInjectionError):
            send_via_tiocsti(pane_id="%0", keys="hi", literal=True)
        op_close.assert_called_once_with(42)


def test_pane_in_copy_mode_true():
    from unittest.mock import patch

    from ccmux_core.keys import pane_in_copy_mode

    with patch("subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = "1\n"
        assert pane_in_copy_mode("%0") is True


def test_pane_in_copy_mode_false():
    from unittest.mock import patch

    from ccmux_core.keys import pane_in_copy_mode

    with patch("subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = "0\n"
        assert pane_in_copy_mode("%0") is False


def test_pane_in_copy_mode_falls_back_to_false_on_tmux_error():
    """If detection itself fails, assume normal mode."""
    from unittest.mock import patch

    from ccmux_core.keys import pane_in_copy_mode

    with patch("subprocess.run") as run:
        run.return_value.returncode = 1
        run.return_value.stdout = ""
        run.return_value.stderr = "can't find pane"
        assert pane_in_copy_mode("%0") is False


def test_send_keys_dispatches_to_tmux_when_normal_mode():
    from unittest.mock import patch

    from ccmux_core.keys import send_keys

    with (
        patch("ccmux_core.keys.pane_in_copy_mode", return_value=False),
        patch("ccmux_core.keys.send_via_tmux") as via_tmux,
        patch("ccmux_core.keys.send_via_tiocsti") as via_tiocsti,
    ):
        send_keys(pane_id="%0", keys="hi", literal=True)

    via_tmux.assert_called_once_with("%0", "hi", literal=True)
    via_tiocsti.assert_not_called()


def test_send_keys_dispatches_to_tiocsti_when_copy_mode():
    from unittest.mock import patch

    from ccmux_core.keys import send_keys

    with (
        patch("ccmux_core.keys.pane_in_copy_mode", return_value=True),
        patch("ccmux_core.keys.send_via_tmux") as via_tmux,
        patch("ccmux_core.keys.send_via_tiocsti") as via_tiocsti,
    ):
        send_keys(pane_id="%0", keys="hi", literal=True)

    via_tmux.assert_not_called()
    via_tiocsti.assert_called_once_with("%0", "hi", literal=True)
