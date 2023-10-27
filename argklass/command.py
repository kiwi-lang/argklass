from __future__ import annotations

import argparse
import os
from contextlib import contextmanager

from .argformat import HelpAction
from .arguments import add_arguments
from .plugin import discover_plugin_commands


def newparser(subparsers: argparse._SubParsersAction, commandcls: Command):
    """Add a subparser to the parser for the command"""
    parser = subparsers.add_parser(
        commandcls.name,
        description=commandcls.help(),
        add_help=False,
    )
    parser.add_argument(
        "-h", "--help", action=HelpAction, help="show this help message and exit"
    )
    return parser


@contextmanager
def chdir(root):
    """change directory and revert back to previous directory"""
    old = os.getcwd()
    os.chdir(root)

    yield
    os.chdir(old)


#
# The problem with this way of registering command is that it is too
# automatic and it might be hard to guarantee that the command tree is going to be
# made correctly
#
#   What if the command is imported before the parent
#   and the parent does not call subcmd()
#
#   see gamekit/marketing/template
#   which is not correctly formed
#
class _Registry2:
    def __init__(self) -> None:
        self.commands = []
        self.cmdmap = dict()
        self.stack = []

    def add_command(self, cmd):
        return

        if not hasattr(cmd, "name"):
            return

        key = tuple([cmd.name for cmd in self.stack] + [cmd.name])
        self.commands.append(cmd)

        assert key not in self.cmdmap, f"{key} already exists"
        self.cmdmap[key] = cmd

    @contextmanager
    def subcmd(self, cmd):
        self.stack.append(cmd)
        yield
        self.stack.pop()

    @property
    def depth(self):
        return len(self.stack)


__registry = _Registry2()


def commands():
    return __registry


def register_command(cls):
    if cls.__module__ is __name__:
        return

    __registry.add_command(cls)


@contextmanager
def _nested_register(cls):
    with __registry.subcmd(cls):
        yield


def command(cls):
    register_command(cls)
    return cls


class CommandMeta(type):
    def __init__(cls, name, bases, attrs):
        super().__init__(name, bases, attrs)
        register_command(cls)


class Command(metaclass=CommandMeta):
    """Base class for all commands"""

    name: str

    @classmethod
    def help(cls) -> str:
        """Return the help text for the command"""
        return cls.__doc__ or ""

    @classmethod
    def argument_class(cls):
        return cls.Arguments

    def __call__(self, args) -> int:
        return self.execute(args)

    @classmethod
    def arguments(cls, subparsers):
        """Define the arguments of this command"""
        parser = newparser(subparsers, cls)
        add_arguments(parser, cls.argument_class())

    @staticmethod
    def execute(args) -> int:
        """Execute the command"""
        raise NotImplementedError()

    @staticmethod
    def examples() -> list[str]:
        """returns a list of examples"""
        return []


class ParentCommand(Command):
    """Loads child module as subcommands"""

    dispatch: dict = dict()
    depth: int = 0
    cmddepth: dict() = dict()

    @staticmethod
    def module():
        return None

    @classmethod
    def command_field(cls):
        depth = ParentCommand.cmddepth.get(cls)
        return f"cmd{depth}"

    @classmethod
    def arguments(cls, subparsers):
        ParentCommand.depth += 1
        ParentCommand.cmddepth[cls] = ParentCommand.depth

        parser = newparser(subparsers, cls)
        cls.shared_arguments(parser)
        subparsers = parser.add_subparsers(dest=cls.command_field(), help=cls.help())

        with _nested_register(cls):
            cmds = cls.fetch_commands()
            cls.register(cls, subparsers, cmds)

        ParentCommand.depth -= 1

    @classmethod
    def shared_arguments(cls, subparsers):
        pass

    @classmethod
    def fetch_commands(cls):
        """Fetch commands using importlib, assume each command is inside its own module"""
        module = cls.module()

        return discover_plugin_commands(module)

    @staticmethod
    def register(cls, subsubparsers, commands):
        name = cls.module().__name__
        for cmdcls in commands:
            cmdcls.arguments(subsubparsers)
            assert (name, cmdcls.name) not in cls.dispatch
            cls.dispatch[(name, cmdcls.name)] = cmdcls()

    @classmethod
    def __call__(cls, args) -> int:
        return cls.execute(args)

    @classmethod
    def execute(cls, args):
        cmd = cls.module().__name__
        subcmd = vars(args).pop(cls.command_field())

        cmd = cls.dispatch.get((cmd, subcmd), None)
        if cmd:
            return cmd.execute(args)

        raise RuntimeError(f"Subcommand {cls.name} {subcmd} is not defined")
