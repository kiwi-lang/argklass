"""Tests for argklass/cli.py — CommandLineInterface."""

import types
from dataclasses import dataclass
from unittest.mock import patch

import pytest

from argklass.arguments import ArgumentParser


@dataclass
class CliCmd1Args:
    value: int = 42  # a value


def _make_registry(*cmd_classes):
    from argklass.plugin import CommandRegistry

    registry = CommandRegistry()
    for cls in cmd_classes:
        registry.insert_commands(cls)
    return registry


def _make_module(name):
    mod = types.ModuleType(name)
    mod.__name__ = name
    mod.__file__ = __file__
    mod.__path__ = []
    return mod


class TestCommandLineInterface:
    def test_basic_cli_build(self):
        from argklass.cli import CommandLineInterface
        from argklass.command import Command

        class Greet(Command):
            name = "greet"
            Arguments = CliCmd1Args

            @staticmethod
            def execute(args):
                return args.value

        registry = _make_registry(Greet)

        with patch("argklass.cli.discover_module_commands", return_value=registry):
            cli = CommandLineInterface(_make_module("cli_build"), prog="testcli")
            args = cli.parse_args(["greet", "--value", "10"])
            result = cli.execute(args)
            assert result == 10

    def test_cli_no_command(self, capsys):
        from argklass.cli import CommandLineInterface
        from argklass.command import Command

        class Dummy(Command):
            name = "dummy_nocmd"

            @staticmethod
            def execute(args):
                return 0

        registry = _make_registry(Dummy)

        with patch("argklass.cli.discover_module_commands", return_value=registry):
            cli = CommandLineInterface(_make_module("cli_nocmd"), prog="testcli2")
            args = cli.parse_args(["dummy_nocmd"])
            result = cli.execute(args)
            assert result == 0

    def test_cli_save_load_defaults(self, tmp_path):
        from argklass.cli import CommandLineInterface
        from argklass.command import Command

        class SaveCmd(Command):
            name = "savecmd"
            Arguments = CliCmd1Args

            @staticmethod
            def execute(args):
                return 0

        registry = _make_registry(SaveCmd)

        with patch("argklass.cli.discover_module_commands", return_value=registry):
            cli = CommandLineInterface(_make_module("cli_save"), prog="testcli3")
            path = str(tmp_path / "cli_defaults.hjson")
            cli.save_defaults(path)
            cli.load_defaults(path)

    def test_cli_rebuild(self):
        from argklass.cli import CommandLineInterface
        from argklass.command import Command

        class RebuildCmd(Command):
            name = "rebuild_cmd"

            @staticmethod
            def execute(args):
                return 0

        registry = _make_registry(RebuildCmd)

        with patch("argklass.cli.discover_module_commands", return_value=registry):
            cli = CommandLineInterface(_make_module("cli_rebuild"), prog="testcli4")
            parser = cli.rebuild()
            assert parser is cli.parser

    def test_cli_run(self):
        from argklass.cli import CommandLineInterface
        from argklass.command import Command

        class RunCmd(Command):
            name = "run_cmd"
            Arguments = CliCmd1Args

            @staticmethod
            def execute(args):
                return args.value

        registry = _make_registry(RunCmd)

        with patch("argklass.cli.discover_module_commands", return_value=registry):
            cli = CommandLineInterface(_make_module("cli_run"), prog="testcli5")
            result = cli.run(["run_cmd", "--value", "99"])
            assert result == 99

    def test_cli_run_help(self):
        from argklass.cli import CommandLineInterface
        from argklass.command import Command

        class HelpCmd(Command):
            name = "help_cmd_exit"

            @staticmethod
            def execute(args):
                return 0

        registry = _make_registry(HelpCmd)

        with patch("argklass.cli.discover_module_commands", return_value=registry):
            cli = CommandLineInterface(_make_module("cli_help"), prog="testcli6")
            with pytest.raises(SystemExit):
                cli.run(["-h"])

    def test_cli_apply_config(self, tmp_path):
        from argklass.cli import CommandLineInterface
        from argklass.command import Command

        class ApplyCmd(Command):
            name = "apply_cmd_test"
            Arguments = CliCmd1Args

            @staticmethod
            def execute(args):
                return args.value

        registry = _make_registry(ApplyCmd)

        with patch("argklass.cli.discover_module_commands", return_value=registry):
            cli = CommandLineInterface(_make_module("cli_apply"), prog="testcli_apply")
            path = str(tmp_path / "apply.hjson")
            cli.save_defaults(path)
            cli.load_defaults(path)

    def test_cli_with_non_registry_commands(self):
        from argklass.cli import CommandLineInterface
        from argklass.command import Command

        class DictCmd(Command):
            name = "dict_cmd"

            @staticmethod
            def execute(args):
                return 0

        cmds = {"dict_cmd": DictCmd}

        with patch("argklass.cli.discover_module_commands", return_value=cmds):
            cli = CommandLineInterface(_make_module("cli_dict"), prog="testcli_dict")
