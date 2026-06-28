from dataclasses import dataclass

from argklass.command import Command


@dataclass
class Command1Args:
    """Arguments for cmd1."""

    message: str  # Message to print
    repeat: int = 1  # Number of times to repeat
    uppercase: bool = False  # Print in uppercase


class Command1(Command):
    """Command1 docstring

    Examples
    --------

    do this
    """

    name = "cmd1"

    Arguments = Command1Args

    @staticmethod
    def execute(args) -> int:
        msg = args.message
        if args.uppercase:
            msg = msg.upper()
        for _ in range(args.repeat):
            print(msg)


COMMANDS = Command1
