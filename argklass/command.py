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


class Command:
    """Base class for all commands"""

    name: str

    @classmethod
    def help(cls) -> str:
        """Return the help text for the command"""
        return cls.__doc__ or ""

    @classmethod
    def argument_class(cls):
        return cls.Arguments

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
        for cmd in commands:
            cmd.arguments(subsubparsers)
            assert (name, cmd.name) not in cls.dispatch
            cls.dispatch[(name, cmd.name)] = cmd

    @classmethod
    def execute(cls, args):
        cmd = cls.module().__name__
        subcmd = vars(args).pop(cls.command_field())

        cmd = cls.dispatch.get((cmd, subcmd), None)
        if cmd:
            return cmd.execute(args)

        raise RuntimeError(f"Subcommand {cls.name} {subcmd} is not defined")
