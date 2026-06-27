"""Tests for argklass/argformat.py — formatting, help actions, normalize."""

import argparse
from dataclasses import dataclass
from typing import List, Optional, Union

import pytest

from argklass.arguments import ArgumentParser, argument, choice, group, subparsers


@dataclass
class FmtArgs:
    name: str = "test"  # the name
    flag: bool = False  # a flag


@dataclass
class FmtSimple:
    x: int = 1  # x


@dataclass
class FmtVal:
    val: int = 0  # value


@dataclass
class FmtFlag:
    flag: bool = False  # toggle


@dataclass
class FmtChoices:
    mode: str = choice("a", "b", "c", default="a")


@dataclass
class FmtLongDefault:
    path: str = "/very/long/path/that/will/be/truncated/when/displayed/in/help"  # the path


@dataclass
class FmtOptional:
    maybe: Optional[int] = None  # optional


@dataclass
class FmtList:
    items: List[int] = argument(default=[1, 2], help="list of items")


@dataclass
class SubA:
    x: int = 0  # x val


@dataclass
class SubB:
    y: str = ""  # y val


@dataclass
class FmtSubMain:
    cmd: Union[SubA, SubB] = subparsers(a=SubA, b=SubB)


class TestArgumentFormater:
    def test_recursively_show_actions(self, capsys):
        from argklass.argformat import recursively_show_actions

        parser = ArgumentParser()
        parser.add_arguments(FmtArgs, create_group=True)
        recursively_show_actions(parser)

    def test_show_parsing_tree(self, capsys):
        from argklass.argformat import show_parsing_tree

        parser = ArgumentParser(prog="test", description="A test parser")
        parser.add_arguments(FmtSimple, create_group=True)
        show_parsing_tree(parser)

    def test_argument_formater_help_action(self, capsys):
        from argklass.argformat import ArgumentFormater

        parser = ArgumentParser(prog="test")
        parser.add_arguments(FmtVal, create_group=True)

        fmt = ArgumentFormater()
        fmt.depth_limit = 2
        fmt(parser, 0)
        fmt.show()

        out = capsys.readouterr().out
        assert "help" in out.lower() or "val" in out

    def test_argument_formater_store_true(self, capsys):
        from argklass.argformat import ArgumentFormater

        parser = ArgumentParser(prog="test")
        parser.add_arguments(FmtFlag, create_group=True)

        fmt = ArgumentFormater()
        fmt(parser, 0)
        fmt.show()

        out = capsys.readouterr().out
        assert "bool" in out or "flag" in out

    def test_argument_formater_with_choices(self, capsys):
        from argklass.argformat import ArgumentFormater

        parser = ArgumentParser(prog="test")
        parser.add_arguments(FmtChoices, create_group=True)

        fmt = ArgumentFormater()
        fmt(parser, 0)
        fmt.show()

    def test_argument_formater_long_default(self, capsys):
        from argklass.argformat import ArgumentFormater

        parser = ArgumentParser(prog="test")
        parser.add_arguments(FmtLongDefault, create_group=True)

        fmt = ArgumentFormater()
        fmt(parser, 0)
        fmt.show()

        out = capsys.readouterr().out
        assert "..." in out or "path" in out

    def test_argument_formater_optional(self, capsys):
        from argklass.argformat import ArgumentFormater

        parser = ArgumentParser(prog="test")
        parser.add_arguments(FmtOptional, create_group=True)

        fmt = ArgumentFormater()
        fmt(parser, 0)
        fmt.show()

    def test_argument_formater_list(self, capsys):
        from argklass.argformat import ArgumentFormater

        parser = ArgumentParser(prog="test")
        parser.add_arguments(FmtList, create_group=True)

        fmt = ArgumentFormater()
        fmt(parser, 0)
        fmt.show()

    def test_argument_formater_with_subparsers(self, capsys):
        from argklass.argformat import ArgumentFormater

        parser = ArgumentParser(prog="test")
        parser.add_arguments(FmtSubMain, create_group=True)

        fmt = ArgumentFormater()
        fmt.depth_limit = 3
        fmt(parser, 0)
        fmt.show()

    def test_normalize(self):
        from argklass.argformat import normalize

        ns = argparse.Namespace(**{"a.b": 1, "a.c": 2, "d": 3})
        result = normalize(ns)
        assert result.d == 3
        assert result.a.b == 1
        assert result.a.c == 2

    def test_help_action_with_exception(self):
        from argklass.argformat import HelpAction, HelpActionException

        parser = ArgumentParser(prog="test", add_help=False)
        parser.add_argument("-h", "--help", action=HelpAction.with_exception, help="show help")
        parser.add_arguments(FmtSimple, create_group=True)

        with pytest.raises(HelpActionException):
            argparse.ArgumentParser.parse_args(parser, ["-h"])

    def test_help_action_with_exit(self):
        from argklass.argformat import HelpAction

        parser = ArgumentParser(prog="test", add_help=False)
        parser.add_argument("-h", "--help", action=HelpAction.with_exit, help="show help")
        parser.add_arguments(FmtSimple, create_group=True)

        with pytest.raises(SystemExit):
            argparse.ArgumentParser.parse_args(parser, ["-h"])

    def test_dump_parser_action(self):
        from argklass.argformat import DumpParserAction

        parser = ArgumentParser(prog="test", add_help=False)
        parser.add_argument("-d", "--dump", action=DumpParserAction, help="dump tree")
        parser.add_argument("--val", type=int, default=0, help="a value")

        with pytest.raises(SystemExit):
            argparse.ArgumentParser.parse_args(parser, ["-d"])


class TestArgFormatFailureModes:
    def test_iterator_depth_limit_exceeded(self, capsys):
        from argklass.argformat import ArgumentParserIterator

        parser = ArgumentParser(prog="test", description="test")
        parser.add_arguments(FmtSubMain, create_group=True)

        fmt = ArgumentParserIterator()
        fmt.depth_limit = 1
        fmt(parser, 0)
        fmt.show()

    def test_iterator_no_groups(self, capsys):
        from argklass.argformat import ArgumentParserIterator

        parser = ArgumentParser(prog="test", description="test")
        parser.add_arguments(FmtSimple, create_group=True)

        fmt = ArgumentParserIterator()
        fmt.show_groups = False
        fmt(parser, 0)
        fmt.show()

    def test_formater_no_groups(self, capsys):
        from argklass.argformat import ArgumentFormater

        parser = ArgumentParser(prog="test")
        parser.add_arguments(FmtSimple, create_group=True)

        fmt = ArgumentFormater()
        fmt.show_groups = False
        fmt(parser, 0)
        fmt.show()

    def test_normalize_nested_existing_key(self):
        from argklass.argformat import normalize

        ns = argparse.Namespace(**{"a.b": 1, "a.c": 2, "a.d": 3})
        result = normalize(ns)
        assert result.a.b == 1
        assert result.a.c == 2
        assert result.a.d == 3

    def test_formater_action_no_help(self, capsys):
        from argklass.argformat import ArgumentFormater

        parser = ArgumentParser(prog="test", add_help=False)
        parser.add_argument("--val", type=int, default=0)

        fmt = ArgumentFormater()
        fmt(parser, 0)
        fmt.show()

    def test_formater_depth_above_zero(self, capsys):
        from argklass.argformat import ArgumentFormater

        parser = ArgumentParser(prog="test")
        parser.add_arguments(FmtSimple, create_group=True)

        fmt = ArgumentFormater()
        for g in parser._action_groups:
            fmt.format_group(g, depth=2)

    def test_formater_group_all_help_actions(self, capsys):
        from argklass.argformat import ArgumentFormater

        parser = ArgumentParser(prog="test", add_help=True)

        fmt = ArgumentFormater()
        fmt(parser, 0)
        fmt.show()

    def test_newline_between_groups(self, capsys):
        from argklass.argformat import ArgumentParserIterator

        parser = ArgumentParser(prog="test", description="test desc")
        parser.add_arguments(FmtSimple, create_group=True)

        fmt = ArgumentParserIterator()
        fmt.newline_between_groups = True
        fmt(parser, 0)
        fmt.show()

    def test_group_increase_indent(self, capsys):
        from argklass.argformat import ArgumentParserIterator

        parser = ArgumentParser(prog="test", description="test desc")
        parser.add_arguments(FmtSimple, create_group=True)

        fmt = ArgumentParserIterator()
        fmt.group_increase_indent = True
        fmt(parser, 0)
        fmt.show()
