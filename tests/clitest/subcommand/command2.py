from argklass.command import Command


class Command2(Command):
    """Command2 docstring"""

    name = "cmd2"

    @staticmethod
    def execute(args) -> int:
        print("subcmd2")


COMMANDS = Command2
