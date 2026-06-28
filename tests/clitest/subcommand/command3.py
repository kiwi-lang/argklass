from dataclasses import dataclass

from argklass.command import Command


@dataclass
class SubCmd3Args:
    """Arguments for sub cmd3."""

    count: int = 10  # Number of items
    reverse: bool = False  # Reverse order


class Command3(Command):
    """Command3 docstring"""

    name = "cmd3"

    Arguments = SubCmd3Args

    @staticmethod
    def execute(args) -> int:
        items = range(args.count)
        if args.reverse:
            items = reversed(items)
        for i in items:
            print(i)


COMMANDS = Command3
