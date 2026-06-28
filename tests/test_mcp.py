"""Tests for the argklass MCP server generator.

These tests exercise schema extraction, argv reconstruction, and tool
execution *without* requiring the ``mcp`` package (only ``MCPServer.run()``
needs it).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple

import pytest

from argklass import ArgumentParser, argument, choice, group
from argklass.command import Command, ParentCommand, commands
from argklass.mcp import (
    MCPServer,
    ToolDef,
    _action_to_property,
    _build_argv,
    _extract_tools,
    _has_subparsers,
    _parser_to_schema,
    _type_to_json_schema,
    ArgMeta,
    create_mcp_server,
)


# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_registry():
    commands().clear()


# -----------------------------------------------------------------------
# Inline command definitions for testing
# -----------------------------------------------------------------------


class Color(Enum):
    RED = 1
    GREEN = 2
    BLUE = 3


@dataclass
class GreetArgs:
    """Arguments for the greet command."""

    name: str  # Name to greet
    count: int = 1  # How many times
    loud: bool = False  # Shout it


class GreetCommand(Command):
    """Greet someone by name."""

    name = "greet"

    Arguments = GreetArgs

    @staticmethod
    def execute(args) -> int:
        msg = f"Hello, {args.name}!"
        if args.loud:
            msg = msg.upper()
        for _ in range(args.count):
            print(msg)
        return 0


@dataclass
class AddArgs:
    a: int  # First number
    b: int  # Second number


class AddCommand(Command):
    """Add two numbers."""

    name = "add"

    Arguments = AddArgs

    @staticmethod
    def execute(args) -> int:
        print(args.a + args.b)
        return 0


class NoArgsCommand(Command):
    """A command with no arguments."""

    name = "ping"

    @staticmethod
    def execute(args) -> int:
        print("pong")
        return 0


class FailingCommand(Command):
    """A command that always raises."""

    name = "fail"

    @staticmethod
    def execute(args) -> int:
        raise RuntimeError("something went wrong")


class StderrCommand(Command):
    """A command that writes to stderr."""

    name = "warn"

    @staticmethod
    def execute(args) -> int:
        import sys

        print("normal output")
        print("warning: watch out", file=sys.stderr)
        return 0


@dataclass
class RichArgs:
    """Arguments with various types."""

    path: str  # File path
    tags: List[str] = argument(default=["default"], help="Tags")  # Tags
    threshold: float = 0.5  # Threshold value
    verbosity: Optional[int] = None  # Verbosity level
    mode: str = choice("fast", "slow", "auto", default="auto")  # Processing mode


class RichCommand(Command):
    """A command with rich argument types."""

    name = "process"

    Arguments = RichArgs

    @staticmethod
    def execute(args) -> int:
        print(f"path={args.path}")
        print(f"tags={args.tags}")
        print(f"threshold={args.threshold}")
        print(f"verbosity={args.verbosity}")
        print(f"mode={args.mode}")
        return 0


@dataclass
class DisableArgs:
    no_cache: bool = argument(action="store_false", help="Disable caching")


class DisableCommand(Command):
    """Command with store_false flag."""

    name = "build"

    Arguments = DisableArgs

    @staticmethod
    def execute(args) -> int:
        print(f"cache={args.no_cache}")
        return 0


@dataclass
class ReturnValueArgs:
    code: int = 0  # exit code to return


class ReturnValueCommand(Command):
    """A command that returns a non-zero exit code."""

    name = "exitcode"

    Arguments = ReturnValueArgs

    @staticmethod
    def execute(args) -> int:
        print("ran")
        return args.code


# -----------------------------------------------------------------------
# Helper to build parsers inline
# -----------------------------------------------------------------------


def _make_parser(*cmd_classes):
    parser = ArgumentParser(prog="test", add_help=False)
    subparsers = parser.add_subparsers(dest="command")
    for cls in cmd_classes:
        cls.arguments(subparsers)
    return parser


def _make_server(*cmd_classes):
    """Build an MCPServer from inline command classes (no module discovery)."""
    parser = _make_parser(*cmd_classes)

    class FakeCLI:
        pass

    cli = FakeCLI()
    cli.parser = parser

    registry = {}
    for cls in cmd_classes:
        inst = cls()
        registry[inst.name] = inst

    class FakeCommands:
        found_commands = registry

    cli.commands = FakeCommands()

    def fake_run(argv):
        args = argparse.ArgumentParser.parse_args(parser, argv)
        cmd_name = vars(args).pop("command")
        cmd = registry[cmd_name]
        return cmd(args)

    cli.run = fake_run
    return MCPServer("test-server", cli)


# =======================================================================
# Unit tests: _type_to_json_schema
# =======================================================================


class TestTypeToJsonSchema:
    def test_str(self):
        assert _type_to_json_schema(str) == {"type": "string"}

    def test_int(self):
        assert _type_to_json_schema(int) == {"type": "integer"}

    def test_float(self):
        assert _type_to_json_schema(float) == {"type": "number"}

    def test_bool(self):
        assert _type_to_json_schema(bool) == {"type": "boolean"}

    def test_none_defaults_to_string(self):
        assert _type_to_json_schema(None) == {"type": "string"}

    def test_unknown_type_defaults_to_string(self):
        assert _type_to_json_schema(object) == {"type": "string"}


# =======================================================================
# Unit tests: _action_to_property
# =======================================================================


class TestActionToProperty:
    def test_skips_help_action(self):
        action = argparse._HelpAction(option_strings=["-h"], dest="help")
        dest, prop = _action_to_property(action)
        assert dest is None
        assert prop is None

    def test_skips_subparsers_action(self):
        parser = argparse.ArgumentParser()
        sp = parser.add_subparsers(dest="cmd")
        for action in parser._subparsers._group_actions:
            dest, prop = _action_to_property(action)
            assert dest is None

    def test_store_true(self):
        action = argparse._StoreTrueAction(
            option_strings=["--verbose"],
            dest="verbose",
            default=False,
            help="Be verbose",
        )
        dest, prop = _action_to_property(action)
        assert dest == "verbose"
        assert prop["type"] == "boolean"
        assert prop["default"] is False
        assert prop["description"] == "Be verbose"

    def test_store_false(self):
        action = argparse._StoreFalseAction(
            option_strings=["--no-cache"],
            dest="cache",
            default=True,
            help="Disable cache",
        )
        dest, prop = _action_to_property(action)
        assert dest == "cache"
        assert prop["type"] == "boolean"
        assert prop["default"] is True

    def test_string_arg(self):
        action = argparse._StoreAction(
            option_strings=["--name"],
            dest="name",
            type=str,
            default=None,
            help="Your name",
        )
        dest, prop = _action_to_property(action)
        assert dest == "name"
        assert prop["type"] == "string"
        assert prop["description"] == "Your name"
        assert "default" not in prop

    def test_integer_arg(self):
        action = argparse._StoreAction(
            option_strings=["--count"],
            dest="count",
            type=int,
            default=1,
            help="Repeat count",
        )
        dest, prop = _action_to_property(action)
        assert dest == "count"
        assert prop["type"] == "integer"
        assert prop["default"] == 1

    def test_float_arg(self):
        action = argparse._StoreAction(
            option_strings=["--rate"],
            dest="rate",
            type=float,
            default=0.5,
            help="Learning rate",
        )
        dest, prop = _action_to_property(action)
        assert prop["type"] == "number"
        assert prop["default"] == 0.5

    def test_choices(self):
        action = argparse._StoreAction(
            option_strings=["--color"],
            dest="color",
            type=str,
            default="red",
            choices=["red", "green", "blue"],
            help="Pick a color",
        )
        dest, prop = _action_to_property(action)
        assert prop["enum"] == ["red", "green", "blue"]
        assert prop["default"] == "red"

    def test_nargs_plus(self):
        action = argparse._StoreAction(
            option_strings=["--files"],
            dest="files",
            type=str,
            nargs="+",
            help="Input files",
        )
        dest, prop = _action_to_property(action)
        assert prop["type"] == "array"
        assert prop["items"] == {"type": "string"}
        assert prop["minItems"] == 1

    def test_nargs_star(self):
        action = argparse._StoreAction(
            option_strings=["--extras"],
            dest="extras",
            type=str,
            nargs="*",
            help="Extra args",
        )
        dest, prop = _action_to_property(action)
        assert prop["type"] == "array"
        assert prop["items"] == {"type": "string"}
        assert "minItems" not in prop

    def test_no_type_defaults_to_string(self):
        action = argparse._StoreAction(
            option_strings=["--val"],
            dest="val",
            type=None,
            default=None,
        )
        dest, prop = _action_to_property(action)
        assert prop["type"] == "string"

    def test_no_help_means_no_description(self):
        action = argparse._StoreAction(
            option_strings=["--quiet"],
            dest="quiet",
            type=str,
            default=None,
        )
        dest, prop = _action_to_property(action)
        assert "description" not in prop

    def test_suppressed_default_excluded(self):
        action = argparse._StoreAction(
            option_strings=["--x"],
            dest="x",
            type=int,
            default=argparse.SUPPRESS,
        )
        dest, prop = _action_to_property(action)
        assert "default" not in prop


# =======================================================================
# Unit tests: _parser_to_schema
# =======================================================================


class TestParserToSchema:
    def test_greet_schema(self):
        parser = _make_parser(GreetCommand)
        subparser = list(parser._subparsers._group_actions[0].choices.values())[0]

        schema, metas = _parser_to_schema(subparser)
        props = schema["properties"]

        assert "name" in props
        assert props["name"]["type"] == "string"

        assert "count" in props
        assert props["count"]["type"] == "integer"

        assert "loud" in props
        assert props["loud"]["type"] == "boolean"

        positional_dests = [m.dest for m in metas if m.positional]
        assert "name" in positional_dests

        assert "name" in schema.get("required", [])

    def test_no_args_command_has_empty_schema(self):
        parser = _make_parser(NoArgsCommand)
        subparser = list(parser._subparsers._group_actions[0].choices.values())[0]

        schema, metas = _parser_to_schema(subparser)
        assert schema == {}
        assert metas == []

    def test_required_vs_optional(self):
        parser = _make_parser(AddCommand)
        subparser = list(parser._subparsers._group_actions[0].choices.values())[0]

        schema, metas = _parser_to_schema(subparser)
        required = schema.get("required", [])
        assert "a" in required
        assert "b" in required

    def test_optional_arg_not_required(self):
        parser = _make_parser(GreetCommand)
        subparser = list(parser._subparsers._group_actions[0].choices.values())[0]

        schema, _ = _parser_to_schema(subparser)
        required = schema.get("required", [])
        assert "count" not in required
        assert "loud" not in required

    def test_rich_args_schema(self):
        parser = _make_parser(RichCommand)
        subparser = list(parser._subparsers._group_actions[0].choices.values())[0]

        schema, metas = _parser_to_schema(subparser)
        props = schema["properties"]

        assert props["path"]["type"] == "string"
        assert props["tags"]["type"] == "array"
        assert props["threshold"]["type"] == "number"
        assert "fast" in props["mode"]["enum"]
        assert "slow" in props["mode"]["enum"]
        assert "auto" in props["mode"]["enum"]

    def test_store_false_meta(self):
        parser = _make_parser(DisableCommand)
        subparser = list(parser._subparsers._group_actions[0].choices.values())[0]

        _, metas = _parser_to_schema(subparser)
        sf_metas = [m for m in metas if m.store_false]
        assert len(sf_metas) == 1
        assert sf_metas[0].dest == "no_cache"

    def test_schema_is_valid_json_schema_shape(self):
        parser = _make_parser(GreetCommand)
        subparser = list(parser._subparsers._group_actions[0].choices.values())[0]

        schema, _ = _parser_to_schema(subparser)
        assert schema["type"] == "object"
        assert isinstance(schema["properties"], dict)


# =======================================================================
# Unit tests: _has_subparsers
# =======================================================================


class TestHasSubparsers:
    def test_parser_without_subparsers(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("--foo")
        assert _has_subparsers(parser) is False

    def test_parser_with_subparsers(self):
        parser = argparse.ArgumentParser()
        parser.add_subparsers(dest="cmd")
        assert _has_subparsers(parser) is True


# =======================================================================
# Unit tests: _extract_tools
# =======================================================================


class TestExtractTools:
    def test_flat_commands(self):
        parser = _make_parser(GreetCommand, AddCommand, NoArgsCommand)
        tools = _extract_tools(parser)

        names = {t.name for t in tools}
        assert names == {"greet", "add", "ping"}

    def test_single_command(self):
        parser = _make_parser(GreetCommand)
        tools = _extract_tools(parser)
        assert len(tools) == 1
        assert tools[0].name == "greet"

    def test_tool_has_schema(self):
        parser = _make_parser(GreetCommand)
        tools = _extract_tools(parser)
        tool = tools[0]
        assert "name" in tool.schema["properties"]
        assert tool.schema["type"] == "object"

    def test_tool_has_argv_prefix(self):
        parser = _make_parser(GreetCommand)
        tools = _extract_tools(parser)
        assert tools[0].argv_prefix == ["greet"]

    def test_tool_has_description(self):
        parser = _make_parser(GreetCommand)
        tools = _extract_tools(parser)
        assert "Greet someone" in tools[0].description

    def test_tool_has_arg_metas(self):
        parser = _make_parser(GreetCommand)
        tools = _extract_tools(parser)
        metas = tools[0].arg_metas
        dests = {m.dest for m in metas}
        assert "name" in dests
        assert "count" in dests
        assert "loud" in dests

    def test_empty_parser_yields_no_tools(self):
        parser = ArgumentParser(prog="empty", add_help=False)
        tools = _extract_tools(parser)
        assert tools == []


# =======================================================================
# Unit tests: _build_argv
# =======================================================================


class TestBuildArgv:
    def test_positional_first(self):
        tool = ToolDef(
            name="greet",
            description="",
            schema={},
            argv_prefix=["greet"],
            arg_metas=[
                ArgMeta(dest="name", positional=True),
                ArgMeta(dest="count"),
            ],
        )
        argv = _build_argv(tool, {"name": "Alice", "count": 3})
        assert argv == ["greet", "Alice", "--count", "3"]

    def test_store_true_added_when_true(self):
        tool = ToolDef(
            name="greet",
            description="",
            schema={},
            argv_prefix=["greet"],
            arg_metas=[
                ArgMeta(dest="name", positional=True),
                ArgMeta(dest="loud", store_true=True),
            ],
        )
        argv = _build_argv(tool, {"name": "Alice", "loud": True})
        assert "--loud" in argv

    def test_store_true_omitted_when_false(self):
        tool = ToolDef(
            name="greet",
            description="",
            schema={},
            argv_prefix=["greet"],
            arg_metas=[
                ArgMeta(dest="name", positional=True),
                ArgMeta(dest="loud", store_true=True),
            ],
        )
        argv = _build_argv(tool, {"name": "Bob", "loud": False})
        assert "--loud" not in argv

    def test_store_false_added_when_false(self):
        tool = ToolDef(
            name="build",
            description="",
            schema={},
            argv_prefix=["build"],
            arg_metas=[
                ArgMeta(dest="no_cache", store_false=True),
            ],
        )
        argv = _build_argv(tool, {"no_cache": False})
        assert "--no_cache" in argv

    def test_store_false_omitted_when_true(self):
        tool = ToolDef(
            name="build",
            description="",
            schema={},
            argv_prefix=["build"],
            arg_metas=[
                ArgMeta(dest="no_cache", store_false=True),
            ],
        )
        argv = _build_argv(tool, {"no_cache": True})
        assert "--no_cache" not in argv

    def test_list_argument(self):
        tool = ToolDef(
            name="run",
            description="",
            schema={},
            argv_prefix=["run"],
            arg_metas=[ArgMeta(dest="files")],
        )
        argv = _build_argv(tool, {"files": ["a.py", "b.py"]})
        assert argv == ["run", "--files", "a.py", "b.py"]

    def test_nested_prefix(self):
        tool = ToolDef(
            name="sub_cmd1",
            description="",
            schema={},
            argv_prefix=["sub", "cmd1"],
            arg_metas=[],
        )
        argv = _build_argv(tool, {})
        assert argv == ["sub", "cmd1"]

    def test_empty_arguments(self):
        tool = ToolDef(
            name="ping",
            description="",
            schema={},
            argv_prefix=["ping"],
            arg_metas=[],
        )
        argv = _build_argv(tool, {})
        assert argv == ["ping"]

    def test_multiple_positionals_preserve_order(self):
        tool = ToolDef(
            name="add",
            description="",
            schema={},
            argv_prefix=["add"],
            arg_metas=[
                ArgMeta(dest="a", positional=True),
                ArgMeta(dest="b", positional=True),
            ],
        )
        argv = _build_argv(tool, {"a": 10, "b": 20})
        assert argv == ["add", "10", "20"]

    def test_missing_positional_skipped(self):
        tool = ToolDef(
            name="greet",
            description="",
            schema={},
            argv_prefix=["greet"],
            arg_metas=[
                ArgMeta(dest="name", positional=True),
                ArgMeta(dest="extra", positional=True),
            ],
        )
        argv = _build_argv(tool, {"name": "Alice"})
        assert argv == ["greet", "Alice"]

    def test_generic_bool_not_in_bool_map(self):
        tool = ToolDef(
            name="cmd",
            description="",
            schema={},
            argv_prefix=["cmd"],
            arg_metas=[ArgMeta(dest="flag")],
        )
        argv = _build_argv(tool, {"flag": True})
        assert "--flag" in argv

        argv = _build_argv(tool, {"flag": False})
        assert "--flag" not in argv


# =======================================================================
# Integration tests: MCPServer with inline commands
# =======================================================================


class TestMCPServerDiscovery:
    def test_discovers_all_tools(self):
        server = _make_server(GreetCommand, AddCommand, NoArgsCommand)
        names = {t.name for t in server.tools}
        assert names == {"greet", "add", "ping"}

    def test_tool_map_populated(self):
        server = _make_server(GreetCommand, AddCommand)
        assert "greet" in server._tool_map
        assert "add" in server._tool_map

    def test_server_name(self):
        server = _make_server(NoArgsCommand)
        assert server.name == "test-server"


class TestMCPServerCall:
    def test_no_args_command(self):
        server = _make_server(NoArgsCommand)
        result = server.call("ping")
        assert "pong" in result

    def test_positional_arg(self):
        server = _make_server(GreetCommand)
        result = server.call("greet", {"name": "World"})
        assert "Hello, World!" in result

    def test_optional_arg(self):
        server = _make_server(GreetCommand)
        result = server.call("greet", {"name": "Test", "count": 3})
        assert result.count("Hello, Test!") == 3

    def test_bool_flag_true(self):
        server = _make_server(GreetCommand)
        result = server.call("greet", {"name": "World", "loud": True})
        assert "HELLO, WORLD!" in result

    def test_bool_flag_false_uses_default(self):
        server = _make_server(GreetCommand)
        result = server.call("greet", {"name": "World", "loud": False})
        assert "Hello, World!" in result
        assert "HELLO" not in result

    def test_integer_positional_args(self):
        server = _make_server(AddCommand)
        result = server.call("add", {"a": 10, "b": 32})
        assert "42" in result

    def test_empty_arguments_dict(self):
        server = _make_server(NoArgsCommand)
        result = server.call("ping", {})
        assert "pong" in result

    def test_none_arguments(self):
        server = _make_server(NoArgsCommand)
        result = server.call("ping", None)
        assert "pong" in result

    def test_unknown_tool_raises_with_available(self):
        server = _make_server(NoArgsCommand, GreetCommand)
        with pytest.raises(ValueError, match="Unknown tool"):
            server.call("nonexistent")

    def test_unknown_tool_lists_available(self):
        server = _make_server(NoArgsCommand, GreetCommand)
        with pytest.raises(ValueError, match="greet"):
            server.call("nonexistent")


class TestMCPServerErrorHandling:
    def test_command_exception_captured(self):
        server = _make_server(FailingCommand)
        result = server.call("fail")
        assert "RuntimeError" in result
        assert "something went wrong" in result

    def test_stderr_captured(self):
        server = _make_server(StderrCommand)
        result = server.call("warn")
        assert "normal output" in result
        assert "warning: watch out" in result

    def test_done_for_silent_success(self):
        class SilentCommand(Command):
            """Does nothing visible."""

            name = "quiet"

            @staticmethod
            def execute(args) -> int:
                return 0

        server = _make_server(SilentCommand)
        result = server.call("quiet")
        assert result == "Done."


class TestMCPServerRichArgs:
    def test_choices_arg(self):
        server = _make_server(RichCommand)
        result = server.call("process", {"path": "/tmp/x", "mode": "fast"})
        assert "mode=fast" in result

    def test_list_arg(self):
        server = _make_server(RichCommand)
        result = server.call(
            "process", {"path": "/tmp/x", "tags": ["a", "b", "c"]}
        )
        assert "tags=['a', 'b', 'c']" in result

    def test_float_arg(self):
        server = _make_server(RichCommand)
        result = server.call("process", {"path": "/tmp/x", "threshold": 0.9})
        assert "threshold=0.9" in result

    def test_defaults_used_when_omitted(self):
        server = _make_server(RichCommand)
        result = server.call("process", {"path": "/tmp/x"})
        assert "mode=auto" in result
        assert "threshold=0.5" in result


class TestMCPServerStoreFlags:
    def test_store_false_flag_activated(self):
        server = _make_server(DisableCommand)
        result = server.call("build", {"no_cache": False})
        assert "cache=False" in result

    def test_store_false_flag_default(self):
        server = _make_server(DisableCommand)
        result = server.call("build", {})
        assert "cache=True" in result


# =======================================================================
# Integration tests: create_mcp_server with real clitest module
# =======================================================================


class TestCreateMCPServerIntegration:
    """Test create_mcp_server with the real clitest package used by other tests."""

    @pytest.fixture
    def server(self, clean_registry):
        import clitest

        return create_mcp_server(clitest, name="clitest-mcp")

    def test_discovers_leaf_commands(self, server):
        names = {t.name for t in server.tools}
        assert "cmd1" in names
        assert "cmd2" in names

    def test_nested_commands_use_prefix(self, server):
        names = {t.name for t in server.tools}
        assert "sub_cmd1" in names
        assert "sub_cmd2" in names
        assert "sub_cmd3" in names

    def test_no_parent_command_as_tool(self, server):
        names = {t.name for t in server.tools}
        assert "sub" not in names

    def test_total_tool_count(self, server):
        assert len(server.tools) == 6

    def test_execute_top_level_cmd1(self, server):
        result = server.call("cmd1", {"message": "hello"})
        assert "hello" in result

    def test_execute_top_level_cmd2(self, server):
        result = server.call("cmd2", {"input": "data.csv", "format": "json"})
        assert "input=data.csv" in result
        assert "format=json" in result

    def test_execute_nested_sub_cmd1(self, server):
        result = server.call("sub_cmd1", {"targets": ["foo", "bar"]})
        assert "foo" in result
        assert "bar" in result

    def test_execute_nested_sub_cmd2(self, server):
        result = server.call("sub_cmd2", {"name": "test"})
        assert "name=test" in result

    def test_execute_nested_sub_cmd3(self, server):
        result = server.call("sub_cmd3")
        assert "0" in result

    def test_execute_nested_sub_cmd4_no_args(self, server):
        result = server.call("sub_cmd4")
        assert "ok" in result

    def test_server_name(self, server):
        assert server.name == "clitest-mcp"

    def test_nested_tool_argv_prefix(self, server):
        tool = server._tool_map["sub_cmd1"]
        assert tool.argv_prefix == ["sub", "cmd1"]

    def test_leaf_tool_argv_prefix(self, server):
        tool = server._tool_map["cmd1"]
        assert tool.argv_prefix == ["cmd1"]

    def test_tool_descriptions_from_docstrings(self, server):
        tool = server._tool_map["cmd1"]
        assert "Command1 docstring" in tool.description


# =======================================================================
# Failure modes: SystemExit variants
# =======================================================================


class TestSystemExit:
    def test_sys_exit_zero(self):
        """sys.exit(0) is a clean exit, should be treated as success."""

        class ExitZero(Command):
            name = "exitok"

            @staticmethod
            def execute(args) -> int:
                import sys

                sys.exit(0)

        server = _make_server(ExitZero)
        result = server.call("exitok")
        assert "exit code" not in result

    def test_sys_exit_nonzero(self):
        """sys.exit(2) should report the exit code."""

        class ExitTwo(Command):
            name = "exit2"

            @staticmethod
            def execute(args) -> int:
                import sys

                sys.exit(2)

        server = _make_server(ExitTwo)
        result = server.call("exit2")
        assert "exit code: 2" in result

    def test_sys_exit_with_message(self):
        """sys.exit("msg") uses a string code — should surface the message."""

        class ExitMsg(Command):
            name = "exitmsg"

            @staticmethod
            def execute(args) -> int:
                import sys

                sys.exit("fatal: bad state")

        server = _make_server(ExitMsg)
        result = server.call("exitmsg")
        assert "fatal: bad state" in result


# =======================================================================
# Failure modes: argument validation
# =======================================================================


class TestArgumentValidation:
    def test_missing_required_positional(self):
        """Calling a tool without a required positional should not crash."""
        server = _make_server(GreetCommand)
        result = server.call("greet", {})
        assert "stderr" in result.lower() or "error" in result.lower() or "exit code" in result

    def test_invalid_type_value(self):
        """Passing a string where an int is expected triggers an argparse error."""
        server = _make_server(AddCommand)
        result = server.call("add", {"a": "not_a_number", "b": 1})
        assert "error" in result.lower() or "exit code" in result

    def test_invalid_choice_value(self):
        """An invalid choice value should be caught."""
        server = _make_server(RichCommand)
        result = server.call("process", {"path": "/x", "mode": "invalid"})
        assert "error" in result.lower() or "exit code" in result


# =======================================================================
# Failure modes: return values
# =======================================================================


class TestReturnValues:
    def test_nonzero_return_reported(self):
        """Non-zero return from execute() should appear in result."""
        server = _make_server(ReturnValueCommand)
        result = server.call("exitcode", {"code": 42})
        assert "ran" in result
        assert "exit code: 42" in result

    def test_zero_return_not_reported(self):
        """Return 0 should not show an exit code line."""
        server = _make_server(ReturnValueCommand)
        result = server.call("exitcode", {"code": 0})
        assert "ran" in result
        assert "exit code" not in result

    def test_stdout_plus_nonzero_exit(self):
        """Both stdout and a non-zero exit code should appear."""

        class PrintThenFail(Command):
            name = "printfail"

            @staticmethod
            def execute(args) -> int:
                print("some useful output")
                return 1

        server = _make_server(PrintThenFail)
        result = server.call("printfail")
        assert "some useful output" in result
        assert "exit code: 1" in result

    def test_none_return_is_silent(self):
        """execute returning None should be treated as success."""

        class ReturnsNone(Command):
            name = "noneret"

            @staticmethod
            def execute(args):
                print("ok")

        server = _make_server(ReturnsNone)
        result = server.call("noneret")
        assert "ok" in result
        assert "exit code" not in result


# =======================================================================
# Edge cases: _action_to_property
# =======================================================================


class TestActionToPropertyEdgeCases:
    def test_version_action_skipped(self):
        action = argparse._VersionAction(
            option_strings=["--version"],
            version="1.0",
        )
        dest, prop = _action_to_property(action)
        assert dest is None
        assert prop is None

    def test_enum_choices_extract_values(self):
        """Choices that are enum members should use .value for the schema."""
        action = argparse._StoreAction(
            option_strings=["--color"],
            dest="color",
            type=str,
            default=Color.RED,
            choices=list(Color),
        )
        dest, prop = _action_to_property(action)
        assert prop["enum"] == [1, 2, 3]
        assert prop["default"] == 1

    def test_nargs_plus_with_int_type(self):
        action = argparse._StoreAction(
            option_strings=["--nums"],
            dest="nums",
            type=int,
            nargs="+",
        )
        _, prop = _action_to_property(action)
        assert prop["type"] == "array"
        assert prop["items"]["type"] == "integer"

    def test_default_with_broken_value_attr(self):
        """A default whose .value property raises should be handled gracefully."""

        class Nasty:
            @property
            def value(self):
                raise TypeError("can't get value")

        action = argparse._StoreAction(
            option_strings=["--x"],
            dest="x",
            type=str,
            default=Nasty(),
        )
        dest, prop = _action_to_property(action)
        assert dest == "x"
        assert "default" not in prop

    def test_bool_flag_without_help(self):
        action = argparse._StoreTrueAction(
            option_strings=["--debug"],
            dest="debug",
            default=False,
        )
        _, prop = _action_to_property(action)
        assert "description" not in prop
        assert prop["type"] == "boolean"


# =======================================================================
# Edge cases: _parser_to_schema
# =======================================================================


class TestParserToSchemaEdgeCases:
    def test_explicitly_required_optional_arg(self):
        """An --option with required=True should appear in required."""
        parser = argparse.ArgumentParser()
        parser.add_argument("--key", type=str, required=True, help="API key")
        schema, metas = _parser_to_schema(parser)
        assert "key" in schema.get("required", [])

    def test_positional_with_default_not_required(self):
        """A positional with a default should NOT be in required."""
        parser = argparse.ArgumentParser()
        parser.add_argument("target", nargs="?", default="all", help="target")
        schema, metas = _parser_to_schema(parser)
        assert "target" not in schema.get("required", [])
        positionals = [m for m in metas if m.positional]
        assert len(positionals) == 1


# =======================================================================
# Edge cases: _build_argv
# =======================================================================


class TestBuildArgvEdgeCases:
    def test_empty_list_value(self):
        tool = ToolDef(
            name="cmd", description="", schema={}, argv_prefix=["cmd"],
            arg_metas=[ArgMeta(dest="items")],
        )
        argv = _build_argv(tool, {"items": []})
        assert argv == ["cmd", "--items"]

    def test_none_value_stringified(self):
        tool = ToolDef(
            name="cmd", description="", schema={}, argv_prefix=["cmd"],
            arg_metas=[ArgMeta(dest="opt")],
        )
        argv = _build_argv(tool, {"opt": None})
        assert argv == ["cmd", "--opt", "None"]

    def test_zero_value_not_dropped(self):
        """0 is falsy but should still be passed as an argument."""
        tool = ToolDef(
            name="cmd", description="", schema={}, argv_prefix=["cmd"],
            arg_metas=[ArgMeta(dest="count")],
        )
        argv = _build_argv(tool, {"count": 0})
        assert "--count" in argv
        assert "0" in argv

    def test_empty_string_value(self):
        tool = ToolDef(
            name="cmd", description="", schema={}, argv_prefix=["cmd"],
            arg_metas=[ArgMeta(dest="name")],
        )
        argv = _build_argv(tool, {"name": ""})
        assert argv == ["cmd", "--name", ""]

    def test_deeply_nested_prefix(self):
        tool = ToolDef(
            name="a_b_c_d", description="", schema={}, argv_prefix=["a", "b", "c", "d"],
            arg_metas=[],
        )
        argv = _build_argv(tool, {})
        assert argv == ["a", "b", "c", "d"]

    def test_positional_list_value(self):
        """A positional argument whose value is a list (nargs='+')."""
        tool = ToolDef(
            name="cmd", description="", schema={}, argv_prefix=["cmd"],
            arg_metas=[ArgMeta(dest="files", positional=True)],
        )
        argv = _build_argv(tool, {"files": ["a.txt", "b.txt", "c.txt"]})
        assert argv == ["cmd", "a.txt", "b.txt", "c.txt"]


# =======================================================================
# Edge cases: _extract_tools with nested parsers
# =======================================================================


class TestExtractToolsNested:
    def test_deeply_nested_parser(self):
        """Three levels of subparsers should produce correctly-prefixed tools."""
        root = argparse.ArgumentParser()
        l1 = root.add_subparsers(dest="l1")
        p1 = l1.add_parser("alpha")
        l2 = p1.add_subparsers(dest="l2")
        p2 = l2.add_parser("beta")
        l3 = p2.add_subparsers(dest="l3")
        p3 = l3.add_parser("gamma")
        p3.add_argument("--val", type=int)

        tools = _extract_tools(root)
        assert len(tools) == 1
        assert tools[0].name == "alpha_beta_gamma"
        assert tools[0].argv_prefix == ["alpha", "beta", "gamma"]
        assert "val" in tools[0].schema["properties"]

    def test_mixed_leaf_and_parent(self):
        """A level with both leaf commands and parent commands."""
        root = argparse.ArgumentParser()
        subs = root.add_subparsers(dest="cmd")

        leaf = subs.add_parser("leaf")
        leaf.add_argument("--x", type=int)

        parent = subs.add_parser("parent")
        child_subs = parent.add_subparsers(dest="child")
        child = child_subs.add_parser("child1")
        child.add_argument("--y", type=str)

        tools = _extract_tools(root)
        names = {t.name for t in tools}
        assert names == {"leaf", "parent_child1"}

    def test_multiple_sibling_subparsers(self):
        """Multiple leaf commands under the same parent are all discovered."""
        root = argparse.ArgumentParser()
        subs = root.add_subparsers(dest="cmd")
        for n in ("a", "b", "c", "d", "e"):
            subs.add_parser(n)

        tools = _extract_tools(root)
        assert len(tools) == 5
        assert {t.name for t in tools} == {"a", "b", "c", "d", "e"}


# =======================================================================
# Edge cases: MCPServer.call
# =======================================================================


class TestMCPServerCallEdgeCases:
    def test_extra_unknown_arg_ignored_by_parser(self):
        """Extra args not in the schema are passed to argparse, which rejects them."""
        server = _make_server(NoArgsCommand)
        result = server.call("ping", {"nonexistent_flag": "value"})
        assert "error" in result.lower() or "exit code" in result

    def test_command_with_both_stdout_and_stderr(self):
        server = _make_server(StderrCommand)
        result = server.call("warn")
        assert "normal output" in result
        assert "stderr:" in result
        assert "warning: watch out" in result

    def test_exception_type_and_message_both_appear(self):
        server = _make_server(FailingCommand)
        result = server.call("fail")
        assert "RuntimeError" in result
        assert "something went wrong" in result

    def test_keyboard_interrupt_captured(self):
        """KeyboardInterrupt during execute should be captured as an error."""

        class InterruptCommand(Command):
            name = "interrupt"

            @staticmethod
            def execute(args) -> int:
                raise KeyboardInterrupt()

        server = _make_server(InterruptCommand)
        result = server.call("interrupt")
        assert "KeyboardInterrupt" in result


# =======================================================================
# Edge cases: create_mcp_server
# =======================================================================


class TestCreateMCPServerEdgeCases:
    def test_name_defaults_to_module_name(self, clean_registry):
        import clitest

        server = create_mcp_server(clitest)
        assert server.name == "clitest"

    def test_explicit_name_used(self, clean_registry):
        import clitest

        server = create_mcp_server(clitest, name="custom-name")
        assert server.name == "custom-name"

    def test_run_calls_serve(self, clean_registry):
        """MCPServer.run() should call asyncio.run(_serve(...))."""
        import clitest

        server = create_mcp_server(clitest)
        called_with = []

        async def fake_serve(transport, host="127.0.0.1", port=8000):
            called_with.append(transport)

        server._serve = fake_serve
        server.run("stdio")
        assert called_with == ["stdio"]

    def test_unsupported_transport_raises(self, clean_registry):
        import clitest

        server = create_mcp_server(clitest)

        try:
            import mcp  # noqa: F401

            mcp_available = True
        except ImportError:
            mcp_available = False

        if mcp_available:
            with pytest.raises(ValueError, match="Unsupported transport"):
                import asyncio

                asyncio.run(server._serve("websocket"))
        else:
            with pytest.raises(ImportError, match="mcp"):
                import asyncio

                asyncio.run(server._serve("websocket"))


# =======================================================================
# _serve internals with mocked mcp package
# =======================================================================


class TestServeWithMockedMCP:
    """Exercise the _serve method by mocking the mcp package imports."""

    def test_serve_registers_and_runs_stdio(self, clean_registry):
        import asyncio
        import sys
        import types
        from unittest.mock import AsyncMock, MagicMock, patch

        server_obj = _make_server(NoArgsCommand)

        fake_mcp_server = MagicMock()
        fake_mcp_server.create_initialization_options.return_value = {}
        fake_mcp_server.run = AsyncMock()

        registered_handlers = {}

        def capture_decorator(handler_name):
            def decorator_factory():
                def decorator(fn):
                    registered_handlers[handler_name] = fn
                    return fn
                return decorator
            return decorator_factory

        fake_mcp_server.list_tools = capture_decorator("list_tools")
        fake_mcp_server.call_tool = capture_decorator("call_tool")

        mock_server_cls = MagicMock(return_value=fake_mcp_server)
        mock_tool = MagicMock()
        mock_text_content = MagicMock()

        mock_read = MagicMock()
        mock_write = MagicMock()

        class FakeStdioCtx:
            async def __aenter__(self):
                return (mock_read, mock_write)
            async def __aexit__(self, *args):
                pass

        mock_stdio_server = MagicMock(return_value=FakeStdioCtx())

        mcp_server_mod = types.ModuleType("mcp.server.lowlevel")
        mcp_server_mod.Server = mock_server_cls

        mcp_types_mod = types.ModuleType("mcp.types")
        mcp_types_mod.Tool = mock_tool
        mcp_types_mod.TextContent = mock_text_content

        mcp_stdio_mod = types.ModuleType("mcp.server.stdio")
        mcp_stdio_mod.stdio_server = mock_stdio_server

        with patch.dict(sys.modules, {
            "mcp": types.ModuleType("mcp"),
            "mcp.server": types.ModuleType("mcp.server"),
            "mcp.server.lowlevel": mcp_server_mod,
            "mcp.types": mcp_types_mod,
            "mcp.server.stdio": mcp_stdio_mod,
        }):
            asyncio.run(server_obj._serve("stdio"))

        mock_server_cls.assert_called_once_with("test-server")
        assert "list_tools" in registered_handlers
        assert "call_tool" in registered_handlers
        fake_mcp_server.run.assert_awaited_once_with(mock_read, mock_write, {})

    def test_serve_list_tools_returns_all(self, clean_registry):
        import asyncio
        import sys
        import types
        from unittest.mock import AsyncMock, MagicMock, patch

        server_obj = _make_server(GreetCommand, AddCommand)

        fake_mcp_server = MagicMock()
        fake_mcp_server.create_initialization_options.return_value = {}
        fake_mcp_server.run = AsyncMock()

        registered_handlers = {}

        def capture_decorator(handler_name):
            def decorator_factory():
                def decorator(fn):
                    registered_handlers[handler_name] = fn
                    return fn
                return decorator
            return decorator_factory

        fake_mcp_server.list_tools = capture_decorator("list_tools")
        fake_mcp_server.call_tool = capture_decorator("call_tool")

        mock_server_cls = MagicMock(return_value=fake_mcp_server)

        collected_tools = []

        class FakeTool:
            def __init__(self, **kwargs):
                collected_tools.append(kwargs)

        mock_text_content = MagicMock()

        class FakeStdioCtx:
            async def __aenter__(self):
                return (MagicMock(), MagicMock())
            async def __aexit__(self, *args):
                pass

        mcp_server_mod = types.ModuleType("mcp.server.lowlevel")
        mcp_server_mod.Server = mock_server_cls
        mcp_types_mod = types.ModuleType("mcp.types")
        mcp_types_mod.Tool = FakeTool
        mcp_types_mod.TextContent = mock_text_content
        mcp_stdio_mod = types.ModuleType("mcp.server.stdio")
        mcp_stdio_mod.stdio_server = MagicMock(return_value=FakeStdioCtx())

        with patch.dict(sys.modules, {
            "mcp": types.ModuleType("mcp"),
            "mcp.server": types.ModuleType("mcp.server"),
            "mcp.server.lowlevel": mcp_server_mod,
            "mcp.types": mcp_types_mod,
            "mcp.server.stdio": mcp_stdio_mod,
        }):
            asyncio.run(server_obj._serve("stdio"))

        list_tools_handler = registered_handlers["list_tools"]
        result = asyncio.run(list_tools_handler())
        names = {t["name"] for t in collected_tools}
        assert "greet" in names
        assert "add" in names

    def test_serve_call_tool_executes(self, clean_registry):
        import asyncio
        import sys
        import types
        from unittest.mock import AsyncMock, MagicMock, patch

        server_obj = _make_server(NoArgsCommand)

        fake_mcp_server = MagicMock()
        fake_mcp_server.create_initialization_options.return_value = {}
        fake_mcp_server.run = AsyncMock()

        registered_handlers = {}

        def capture_decorator(handler_name):
            def decorator_factory():
                def decorator(fn):
                    registered_handlers[handler_name] = fn
                    return fn
                return decorator
            return decorator_factory

        fake_mcp_server.list_tools = capture_decorator("list_tools")
        fake_mcp_server.call_tool = capture_decorator("call_tool")

        mock_server_cls = MagicMock(return_value=fake_mcp_server)

        text_content_calls = []

        class FakeTextContent:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                text_content_calls.append(kwargs)

        class FakeStdioCtx:
            async def __aenter__(self):
                return (MagicMock(), MagicMock())
            async def __aexit__(self, *args):
                pass

        mcp_server_mod = types.ModuleType("mcp.server.lowlevel")
        mcp_server_mod.Server = mock_server_cls
        mcp_types_mod = types.ModuleType("mcp.types")
        mcp_types_mod.Tool = MagicMock()
        mcp_types_mod.TextContent = FakeTextContent
        mcp_stdio_mod = types.ModuleType("mcp.server.stdio")
        mcp_stdio_mod.stdio_server = MagicMock(return_value=FakeStdioCtx())

        with patch.dict(sys.modules, {
            "mcp": types.ModuleType("mcp"),
            "mcp.server": types.ModuleType("mcp.server"),
            "mcp.server.lowlevel": mcp_server_mod,
            "mcp.types": mcp_types_mod,
            "mcp.server.stdio": mcp_stdio_mod,
        }):
            asyncio.run(server_obj._serve("stdio"))

        call_tool_handler = registered_handlers["call_tool"]
        result = asyncio.run(call_tool_handler("ping", {}))
        assert len(result) == 1
        assert "pong" in text_content_calls[-1]["text"]

    def test_serve_unsupported_transport(self, clean_registry):
        """Unsupported transport raises ValueError when mcp is available."""
        import asyncio
        import sys
        import types
        from unittest.mock import MagicMock, patch

        server_obj = _make_server(NoArgsCommand)

        mcp_server_mod = types.ModuleType("mcp.server.lowlevel")
        mcp_server_mod.Server = MagicMock(return_value=MagicMock(
            list_tools=lambda: lambda fn: fn,
            call_tool=lambda: lambda fn: fn,
        ))
        mcp_types_mod = types.ModuleType("mcp.types")
        mcp_types_mod.Tool = MagicMock()
        mcp_types_mod.TextContent = MagicMock()

        with patch.dict(sys.modules, {
            "mcp": types.ModuleType("mcp"),
            "mcp.server": types.ModuleType("mcp.server"),
            "mcp.server.lowlevel": mcp_server_mod,
            "mcp.types": mcp_types_mod,
        }):
            with pytest.raises(ValueError, match="Unsupported transport"):
                asyncio.run(server_obj._serve("websocket"))

    def test_serve_fallback_import_path(self, clean_registry):
        """When mcp.server.lowlevel doesn't exist, fall back to mcp.server."""
        import asyncio
        import sys
        import types
        from unittest.mock import AsyncMock, MagicMock, patch

        server_obj = _make_server(NoArgsCommand)

        fake_mcp_server = MagicMock()
        fake_mcp_server.create_initialization_options.return_value = {}
        fake_mcp_server.run = AsyncMock()
        fake_mcp_server.list_tools = lambda: lambda fn: fn
        fake_mcp_server.call_tool = lambda: lambda fn: fn

        mock_server_cls = MagicMock(return_value=fake_mcp_server)

        class FakeStdioCtx:
            async def __aenter__(self):
                return (MagicMock(), MagicMock())
            async def __aexit__(self, *args):
                pass

        mcp_server_mod = types.ModuleType("mcp.server")
        mcp_server_mod.Server = mock_server_cls
        mcp_types_mod = types.ModuleType("mcp.types")
        mcp_types_mod.Tool = MagicMock()
        mcp_types_mod.TextContent = MagicMock()
        mcp_stdio_mod = types.ModuleType("mcp.server.stdio")
        mcp_stdio_mod.stdio_server = MagicMock(return_value=FakeStdioCtx())

        # Make lowlevel import fail, so it falls back to mcp.server.Server
        patched = {
            "mcp": types.ModuleType("mcp"),
            "mcp.server": mcp_server_mod,
            "mcp.types": mcp_types_mod,
            "mcp.server.stdio": mcp_stdio_mod,
        }
        # Remove lowlevel from modules if present
        with patch.dict(sys.modules, patched):
            sys.modules.pop("mcp.server.lowlevel", None)
            asyncio.run(server_obj._serve("stdio"))

        mock_server_cls.assert_called_once()


# =======================================================================
# Integration tests: ToolDef dataclass
# =======================================================================


class TestToolDef:
    def test_defaults(self):
        td = ToolDef(
            name="t", description="d", schema={}, argv_prefix=["t"]
        )
        assert td.arg_metas == []

    def test_equality(self):
        td1 = ToolDef(name="t", description="d", schema={}, argv_prefix=["t"])
        td2 = ToolDef(name="t", description="d", schema={}, argv_prefix=["t"])
        assert td1 == td2


class TestArgMeta:
    def test_defaults(self):
        m = ArgMeta(dest="x")
        assert m.positional is False
        assert m.store_true is False
        assert m.store_false is False

    def test_positional(self):
        m = ArgMeta(dest="x", positional=True)
        assert m.positional is True

    def test_store_flags_exclusive(self):
        m = ArgMeta(dest="x", store_true=True)
        assert m.store_true is True
        assert m.store_false is False


# =======================================================================
# Thread safety
# =======================================================================


class TestThreadSafety:
    def test_concurrent_calls(self):
        import threading

        server = _make_server(GreetCommand, AddCommand)
        results = {}
        errors = []

        def call_greet():
            try:
                results["greet"] = server.call(
                    "greet", {"name": "Thread1", "count": 1}
                )
            except Exception as e:
                errors.append(e)

        def call_add():
            try:
                results["add"] = server.call("add", {"a": 5, "b": 7})
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=call_greet)
        t2 = threading.Thread(target=call_add)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors
        assert "Hello, Thread1!" in results["greet"]
        assert "12" in results["add"]

    def test_many_concurrent_calls(self):
        """Hammer the server with many threads to stress the lock."""
        import threading

        server = _make_server(AddCommand)
        results = []
        errors = []

        def call_add(i):
            try:
                result = server.call("add", {"a": i, "b": i})
                results.append((i, result))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=call_add, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(results) == 20
        for i, result in results:
            assert str(i * 2) in result


# =======================================================================
# Multi-module / add_module
# =======================================================================


class TestMultiModule:
    def test_cli_property(self):
        """The .cli property returns the first CLI for backward compatibility."""
        server = _make_server(GreetCommand)
        assert server.cli is server._clis[0]

    def _make_second_server(self):
        """Build a fake CLI module with a different command for multi-module tests."""
        import types

        parser2 = _make_parser(AddCommand)

        class FakeCLI2:
            pass

        cli2 = FakeCLI2()
        cli2.parser = parser2

        def fake_run(argv):
            args = argparse.ArgumentParser.parse_args(parser2, argv)
            cmd_name = vars(args).pop("command")
            return {"add": AddCommand()}[cmd_name](args)

        cli2.run = fake_run

        mod = types.ModuleType("fakecli2")
        mod.__name__ = "fakecli2"
        mod.COMMANDS = None
        return mod, cli2

    def test_add_module_no_prefix(self, clean_registry):
        """add_module without prefix keeps original tool names."""
        import clitest

        server = _make_server(GreetCommand)
        before = len(server.tools)
        server.add_module(clitest)
        assert len(server.tools) > before
        # Tools from clitest should not be prefixed
        clitest_names = [t.name for t in server.tools[before:]]
        assert all(not n.startswith("clitest_") for n in clitest_names)

    def test_add_module_with_prefix(self, clean_registry):
        """add_module prefixes tool names when prefix is given."""
        import clitest

        server = _make_server(GreetCommand)
        server.add_module(clitest, prefix="ct")
        prefixed = [t.name for t in server.tools if t.name.startswith("ct_")]
        assert len(prefixed) > 0

    def test_add_module_duplicate_raises(self, clean_registry):
        """add_module raises ValueError on duplicate tool names."""
        # Use _make_server so the CLI is fake and doesn't hit ParentCommand dispatch
        server = _make_server(GreetCommand, AddCommand)

        # Build a second fake CLI that also has "greet"
        parser2 = _make_parser(GreetCommand)

        class FakeCLI2:
            pass

        cli2 = FakeCLI2()
        cli2.parser = parser2
        cli2.run = lambda argv: None

        import types

        mod2 = types.ModuleType("fakecli2")
        from unittest.mock import patch

        with patch("argklass.cli.CommandLineInterface", return_value=cli2):
            with pytest.raises(ValueError, match="Duplicate tool name"):
                server.add_module(mod2)

    def test_add_module_tool_routes_to_correct_cli(self, clean_registry):
        """Each tool is dispatched to the CLI it came from."""
        import clitest

        server = _make_server(AddCommand)
        server.add_module(clitest, prefix="ct")
        # The add tool should still work via the original CLI
        result = server.call("add", {"a": 3, "b": 4})
        assert "7" in result

    def test_create_mcp_server_single_module(self, clean_registry):
        """create_mcp_server with a single module works."""
        import clitest

        server = create_mcp_server(clitest, name="multi")
        original_names = {t.name for t in server.tools}
        assert server.name == "multi"
        assert len(original_names) > 0


# =======================================================================
# Transport error paths
# =======================================================================


class TestTransportErrors:
    def test_stdio_bad_stdout(self, clean_registry):
        """stdio transport raises RuntimeError when stdout is not a real file."""
        import asyncio
        import sys
        import types
        from unittest.mock import MagicMock, patch

        server = _make_server(NoArgsCommand)

        # Set up minimal mcp mocks so _serve gets past the import
        fake_mcp_server = MagicMock()
        fake_mcp_server.list_tools = lambda: lambda fn: fn
        fake_mcp_server.call_tool = lambda: lambda fn: fn
        mock_server_cls = MagicMock(return_value=fake_mcp_server)

        mcp_ll_mod = types.ModuleType("mcp.server.lowlevel")
        mcp_ll_mod.Server = mock_server_cls
        mcp_types_mod = types.ModuleType("mcp.types")
        mcp_types_mod.Tool = MagicMock()
        mcp_types_mod.TextContent = MagicMock()

        patched_mods = {
            "mcp": types.ModuleType("mcp"),
            "mcp.server": types.ModuleType("mcp.server"),
            "mcp.server.lowlevel": mcp_ll_mod,
            "mcp.types": mcp_types_mod,
        }

        fake_stdout = MagicMock(spec=[])  # no attributes at all
        with patch.dict(sys.modules, patched_mods):
            with patch("sys.stdout", fake_stdout):
                with pytest.raises(RuntimeError, match="stdio transport requires"):
                    asyncio.run(server._serve("stdio"))

    def test_sse_import_error(self, clean_registry):
        """SSE transport raises ImportError when starlette/uvicorn missing."""
        import asyncio
        import sys
        import types
        from unittest.mock import MagicMock, patch

        server = _make_server(NoArgsCommand)

        fake_mcp_server = MagicMock()
        fake_mcp_server.list_tools = lambda: lambda fn: fn
        fake_mcp_server.call_tool = lambda: lambda fn: fn
        mock_server_cls = MagicMock(return_value=fake_mcp_server)

        mcp_ll_mod = types.ModuleType("mcp.server.lowlevel")
        mcp_ll_mod.Server = mock_server_cls
        mcp_types_mod = types.ModuleType("mcp.types")
        mcp_types_mod.Tool = MagicMock()
        mcp_types_mod.TextContent = MagicMock()

        patched_mods = {
            "mcp": types.ModuleType("mcp"),
            "mcp.server": types.ModuleType("mcp.server"),
            "mcp.server.lowlevel": mcp_ll_mod,
            "mcp.types": mcp_types_mod,
        }
        with patch.dict(sys.modules, patched_mods):
            # Remove starlette so the import inside _serve_sse fails
            with patch.dict(sys.modules, {"starlette": None, "starlette.applications": None}):
                with pytest.raises(ImportError, match="starlette"):
                    asyncio.run(server._serve("sse"))

    def test_streamable_http_import_error(self, clean_registry):
        """streamable-http transport raises ImportError when deps missing."""
        import asyncio
        import sys
        import types
        from unittest.mock import MagicMock, patch

        server = _make_server(NoArgsCommand)

        fake_mcp_server = MagicMock()
        fake_mcp_server.list_tools = lambda: lambda fn: fn
        fake_mcp_server.call_tool = lambda: lambda fn: fn
        mock_server_cls = MagicMock(return_value=fake_mcp_server)

        mcp_ll_mod = types.ModuleType("mcp.server.lowlevel")
        mcp_ll_mod.Server = mock_server_cls
        mcp_types_mod = types.ModuleType("mcp.types")
        mcp_types_mod.Tool = MagicMock()
        mcp_types_mod.TextContent = MagicMock()

        patched_mods = {
            "mcp": types.ModuleType("mcp"),
            "mcp.server": types.ModuleType("mcp.server"),
            "mcp.server.lowlevel": mcp_ll_mod,
            "mcp.types": mcp_types_mod,
        }
        with patch.dict(sys.modules, patched_mods):
            with patch.dict(sys.modules, {
                "mcp.server.streamable_http": None,
            }):
                with pytest.raises(ImportError, match="starlette.*uvicorn"):
                    asyncio.run(server._serve("streamable-http"))

    def test_mcp_not_installed(self):
        """Both mcp import paths fail -> ImportError with install hint."""
        import asyncio
        import sys
        from unittest.mock import patch

        server = _make_server(NoArgsCommand)
        with patch.dict(sys.modules, {
            "mcp": None,
            "mcp.server": None,
            "mcp.server.lowlevel": None,
        }):
            with pytest.raises(ImportError, match="pip install"):
                asyncio.run(server._serve("stdio"))


# =======================================================================
# _main entry point
# =======================================================================


class TestMainEntryPoint:
    def test_main_runs_server(self, clean_registry, monkeypatch):
        """_main parses args, imports modules, and calls server.run()."""
        from unittest.mock import patch

        from argklass.mcp import _main

        called_with = {}

        def fake_run(self_arg, transport, host, port):
            called_with["transport"] = transport
            called_with["host"] = host
            called_with["port"] = port

        monkeypatch.setattr(
            "sys.argv",
            ["argklass.mcp", "clitest", "--transport", "sse", "--port", "9999"],
        )
        with patch.object(MCPServer, "run", fake_run):
            _main()

        assert called_with["transport"] == "sse"
        assert called_with["port"] == 9999

    def test_main_default_transport(self, clean_registry, monkeypatch):
        """_main defaults to stdio transport."""
        from unittest.mock import patch

        from argklass.mcp import _main

        called_with = {}

        def fake_run(self_arg, transport, host, port):
            called_with["transport"] = transport

        monkeypatch.setattr("sys.argv", ["argklass.mcp", "clitest"])
        with patch.object(MCPServer, "run", fake_run):
            _main()

        assert called_with["transport"] == "stdio"

    def test_main_host_port(self, clean_registry, monkeypatch):
        """_main forwards --host and --port."""
        from unittest.mock import patch

        from argklass.mcp import _main

        called_with = {}

        def fake_run(self_arg, transport, host, port):
            called_with["host"] = host
            called_with["port"] = port

        monkeypatch.setattr(
            "sys.argv",
            ["argklass.mcp", "clitest", "--host", "0.0.0.0", "--port", "3000"],
        )
        with patch.object(MCPServer, "run", fake_run):
            _main()

        assert called_with["host"] == "0.0.0.0"
        assert called_with["port"] == 3000


# =======================================================================
# Nested group recursion in _parser_to_schema
# =======================================================================


class TestNestedGroupSchema:
    @pytest.mark.skipif(
        sys.version_info >= (3, 14),
        reason="Python 3.14+ does not allow nested argument groups",
    )
    def test_args_from_nested_groups_appear_in_schema(self):
        """Arguments inside nested argument groups must appear in the schema."""
        from argklass.mcp import _parser_to_schema

        parser = argparse.ArgumentParser()
        outer = parser.add_argument_group("outer")
        outer.add_argument("--alpha", type=int, default=1)
        inner = outer.add_argument_group("inner")
        inner.add_argument("--beta", type=str, default="b")

        schema, metas = _parser_to_schema(parser)
        props = schema["properties"]
        assert "alpha" in props
        assert "beta" in props
        dests = {m.dest for m in metas}
        assert "alpha" in dests
        assert "beta" in dests

    def test_argklass_flat_groups_appear_in_schema(self):
        """Arguments from argklass flat-mode groups appear in the schema.

        This simulates the Python 3.14 flat-group layout where argklass
        adds groups as siblings on the root parser with _dataclass metadata.
        """
        from argklass.mcp import _parser_to_schema

        parser = argparse.ArgumentParser()
        # Simulate what argklass does in flat mode: groups as siblings
        outer = parser.add_argument_group("Outer")
        outer.add_argument("--alpha", type=int, default=1)
        setattr(outer, "_dataclass", type("Outer", (), {}))
        setattr(outer, "_dest", "outer")

        inner = parser.add_argument_group("Inner")
        inner.add_argument("--beta", type=str, default="b")
        setattr(inner, "_dataclass", type("Inner", (), {}))
        setattr(inner, "_dest", "inner")
        setattr(inner, "_parent_path", ["outer"])

        schema, metas = _parser_to_schema(parser)
        props = schema["properties"]
        assert "alpha" in props
        assert "beta" in props


# =======================================================================
# create_mcp_server multi-module path
# =======================================================================


class TestCreateMCPServerMulti:
    def test_extra_modules_loop(self, clean_registry):
        """create_mcp_server with extra_modules calls add_module for each."""
        import clitest
        import types
        from unittest.mock import patch, MagicMock

        # Create a fake second module with its own CLI
        parser2 = _make_parser(AddCommand)

        class FakeCLI2:
            pass

        cli2 = FakeCLI2()
        cli2.parser = parser2
        cli2.run = lambda argv: None

        mod2 = types.ModuleType("mod2")
        mod2.__name__ = "mod2"

        call_count = {"n": 0}
        original_init = MCPServer.add_module

        def counting_add(self_arg, module, prefix=None, **kwargs):
            call_count["n"] += 1

        with patch.object(MCPServer, "add_module", counting_add):
            create_mcp_server(clitest, mod2, name="multi")

        assert call_count["n"] == 1

    def test_extra_modules_prefix_true(self, clean_registry):
        """create_mcp_server with prefix=True derives prefix from module name."""
        import clitest
        import types
        from unittest.mock import patch

        mod2 = types.ModuleType("mytools")
        mod2.__name__ = "mytools"

        add_calls = []
        original_add = MCPServer.add_module

        def spy_add(self_arg, module, prefix=None, **kwargs):
            add_calls.append(prefix)

        with patch.object(MCPServer, "add_module", spy_add):
            create_mcp_server(clitest, mod2, prefix=True)

        assert add_calls == ["mytools"]


# =======================================================================
# Regression: tool list JSON snapshot
# =======================================================================


class TestToolListRegression:
    """Snapshot the MCP tool list as indented JSON.

    If the tool schema changes, the baseline file must be regenerated with:
        pytest --force-regen tests/test_mcp.py::TestToolListRegression
    """

    def test_inline_commands_tool_list(self, file_regression):
        """Regression for tool list built from inline test commands."""
        import json

        server = _make_server(
            GreetCommand,
            AddCommand,
            NoArgsCommand,
            RichCommand,
            DisableCommand,
            ReturnValueCommand,
        )
        tools = [
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.schema,
            }
            for t in server.tools
        ]
        output = json.dumps(tools, indent=2, sort_keys=True) + "\n"
        file_regression.check(output, extension=".json")

    def test_clitest_tool_list(self, file_regression, clean_registry):
        """Regression for tool list built from the clitest package."""
        import json

        import clitest

        server = create_mcp_server(clitest, name="clitest-mcp")
        tools = [
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.schema,
            }
            for t in server.tools
        ]
        output = json.dumps(tools, indent=2, sort_keys=True) + "\n"
        file_regression.check(output, extension=".json")
