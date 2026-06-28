from dataclasses import dataclass
from typing import Optional

from argklass.arguments import group
from argklass.command import Command


@dataclass
class OutputOptions:
    """Output configuration."""

    destination: str = "/dev/stdout"  # Output destination path
    format: str = "text"  # Output format
    overwrite: bool = False  # Overwrite existing files


@dataclass
class Command2Args:
    """Arguments for cmd2."""

    input: str  # Input file path
    output: OutputOptions = group(default=OutputOptions())  # Output settings
    verbose: bool = False  # Enable verbose output


class Command2(Command):
    """Command2 docstring

    Examples
    --------

    do this
    """

    name = "cmd2"

    Arguments = Command2Args

    @staticmethod
    def execute(args) -> int:
        print(f"input={args.input}")
        out = getattr(args, "output", None)
        if out is not None:
            print(f"destination={out.destination}")
            print(f"format={out.format}")
            print(f"overwrite={out.overwrite}")
        else:
            print(f"destination={args.destination}")
            print(f"format={args.format}")
            print(f"overwrite={args.overwrite}")
        if args.verbose:
            print("verbose mode")


COMMANDS = Command2
