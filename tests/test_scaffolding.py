def test_package_is_importable():
    import luxctl

    assert luxctl.__name__ == "luxctl"


def test_core_and_shell_subpackages_exist():
    import luxctl.core
    import luxctl.shell

    assert luxctl.core.__name__ == "luxctl.core"
    assert luxctl.shell.__name__ == "luxctl.shell"
