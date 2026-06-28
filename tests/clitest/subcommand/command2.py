from dataclasses import dataclass

from argklass.command import Command


@dataclass
class SubCmd2Args:
    """Arguments for sub cmd2."""

    name: str  # Name to look up
    format: str = "json"  # Output format


class Command2(Command):
    """Command2 docstring"""

    name = "cmd2"

    Arguments = SubCmd2Args

    @staticmethod
    def execute(args) -> int:
        print(f"name={args.name} format={args.format}")


COMMANDS = Command2
