def test_import_package():
    import ccmux_core

    assert ccmux_core.__version__ == "0.1.0"


def test_errors_importable():
    from ccmux_core.errors import BackendError

    assert issubclass(BackendError, Exception)
