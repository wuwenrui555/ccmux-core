def test_import_package():
    import ccmux_core

    assert ccmux_core.__version__ == "0.2.0"


def test_errors_importable():
    from ccmux_core.error import BackendError

    assert issubclass(BackendError, Exception)
