import pytest

from argklass.argformat import HelpActionException
from argklass.cli import CommandLineInterface
from argklass.command import commands


@pytest.fixture
def clean_registry():
    commands().clear()


def test_interface_help(clean_registry, capsys, file_regression):
    import clitest

    cli = CommandLineInterface(clitest, prog="here")

    try:
        cli.parse_args(["--help"])
    except HelpActionException:
        pass

    all = capsys.readouterr()
    stdout = all.out
    assert stdout != ""
    file_regression.check(stdout)


def test_interface_command_dispatch(clean_registry, capsys, file_regression):
    import clitest

    cli = CommandLineInterface(clitest, prog="here")

    cli.run(["sub", "cmd1"])
    cli.run(["sub", "cmd2"])
    cli.run(["sub", "cmd3"])
    cli.run(["cmd1"])
    cli.run(["cmd2"])

    all = capsys.readouterr()
    stdout = all.out
    assert stdout != ""
    file_regression.check(stdout)
