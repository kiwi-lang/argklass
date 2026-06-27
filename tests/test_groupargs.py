"""Tests for argklass/groupargs.py — argument grouping and conversion."""

import argparse
from dataclasses import dataclass
from typing import Union

import pytest

from argklass.arguments import ArgumentParser, add_arguments, group, subparsers



@dataclass
class GrpSub:
    opt: int = 10  # option


@dataclass
class GrpArgs:
    name: str = "test"  # name
    sub: GrpSub = group(default=GrpSub, help="sub")


@dataclass
class GrpSubA:
    x: int = 0  # x val


@dataclass
class GrpSubB:
    y: str = ""  # y val


@dataclass
class GrpMain:
    cmd: Union[GrpSubA, GrpSubB] = subparsers(a=GrpSubA, b=GrpSubB)


@dataclass
class PnInner:
    val: int = 0  # inner val


class TestGroupArgs:
    def test_group_by_dataclass_conversion(self):
        from argklass.groupargs import group_by_dataclass

        parser = ArgumentParser(group_by_dataclass=True)
        parser.add_arguments(GrpArgs, create_group=True)

        raw_args = argparse.ArgumentParser.parse_args(parser, ["--name", "hello", "--opt", "42"])
        result = group_by_dataclass(parser, raw_args, False, True, argparse.Namespace)
        assert hasattr(result, "GrpArgs")

    def test_group_by_parser_with_subparsers(self):
        parser = ArgumentParser(group_by_dataclass=True, group_by_parser=True)
        parser.add_arguments(GrpMain, create_group=True)
        args = parser.parse_args(["a", "--x", "5"])

    def test_groupargs_failed_conversion(self, capsys):
        from argklass.groupargs import GroupArguments

        ga = GroupArguments(argparse.Namespace(x=1), argparse.Namespace)
        ga.group_by_dataclass = True
        ga.new_group("test", argparse.Namespace)
        ga.dest_to_dataclass["test"] = int
        ga.current["x"] = 1
        ga.pop_group()

        out = capsys.readouterr().out
        assert "Could not convert" in out

    def test_dotted_dest_grouping(self):
        parser = ArgumentParser(group_by_dataclass=True)
        add_arguments(parser, PnInner, pathname=True, dest="outer", create_group=True)
        dests = [a.dest for g in parser._action_groups for a in g._group_actions]
        assert any("." in d for d in dests)


class TestGroupArgsFailureModes:
    def test_getattr_missing_attribute(self):
        from argklass.groupargs import _getattr
        obj = argparse.Namespace(a=1)
        assert _getattr(obj, "missing", "default") == "default"

    def test_getattr_falsy_returns_default(self):
        from argklass.groupargs import _getattr
        obj = argparse.Namespace(a=0, b="", c=None)
        assert _getattr(obj, "a", "default") == "default"
        assert _getattr(obj, "b", "default") == "default"
        assert _getattr(obj, "c", "default") == "default"

    def test_convert_with_no_dataclass(self):
        from argklass.groupargs import GroupArguments

        parser = ArgumentParser()
        parser.add_argument("--x", type=int, default=0)

        raw_args = argparse.ArgumentParser.parse_args(parser, ["--x", "5"])
        ga = GroupArguments(raw_args, None)
        result = ga.convert(parser, dataclass=argparse.Namespace)
        assert result.x == 5

    def test_pop_group_none_dataclass(self):
        from argklass.groupargs import GroupArguments

        ga = GroupArguments(argparse.Namespace(), argparse.Namespace)
        ga.new_group("test_group", None)
        ga.current["x"] = 1
        ga.pop_group()
        assert isinstance(ga.current["test_group"], dict)

    def test_format_action_dotted_name(self):
        from argklass.groupargs import GroupArguments

        args = argparse.Namespace(**{"outer.inner": 99})
        ga = GroupArguments(args, argparse.Namespace)

        action = argparse.Action(["--outer.inner"], "outer.inner", default=None)
        ga.format_action(action, 0)
        assert ga.current["outer"]["inner"] == 99

    def test_new_group_existing_namespace(self):
        from argklass.groupargs import GroupArguments

        ga = GroupArguments(argparse.Namespace(), argparse.Namespace)
        ga.current["existing"] = argparse.Namespace(x=1, y=2)
        ga.new_group("existing")
        assert ga.current.get("x") == 1

    def test_format_subparser_no_dest(self):
        from argklass.groupargs import GroupArguments

        ga = GroupArguments(argparse.Namespace(), argparse.Namespace)

        class FakeSubparsersAction:
            dest = "nonexistent"
            choices = {}

        assert ga.format_subparser(FakeSubparsersAction(), 0) is False

    def test_format_action_ignores_none_default(self):
        from argklass.groupargs import GroupArguments

        args = argparse.Namespace(myopt=None)
        ga = GroupArguments(args, argparse.Namespace)
        ga.ignore_default = True

        action = argparse.Action(["--myopt"], "myopt", default=None)
        ga.format_action(action, 0)
        assert "myopt" not in ga.current

    def test_format_action_keeps_value(self):
        from argklass.groupargs import GroupArguments

        args = argparse.Namespace(myopt=42)
        ga = GroupArguments(args, argparse.Namespace)
        ga.ignore_default = True

        action = argparse.Action(["--myopt"], "myopt", default=None)
        ga.format_action(action, 0)
        assert ga.current["myopt"] == 42
