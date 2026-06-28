from dataclasses import dataclass
from typing import List

from argklass.command import Command


@dataclass
class SubCmd1Args:
    """Arguments for sub cmd1."""

    targets: List[str]  # Build targets
    jobs: int = 4  # Number of parallel jobs
    dry_run: bool = False  # Show what would be done


class Command1(Command):
    """Command1 docstring

    Examples
    --------

    do this
    """

    name = "cmd1"

    Arguments = SubCmd1Args

    @staticmethod
    def execute(args) -> int:
        action = "Would build" if args.dry_run else "Building"
        for t in args.targets:
            print(f"{action} {t} (jobs={args.jobs})")


COMMANDS = Command1
