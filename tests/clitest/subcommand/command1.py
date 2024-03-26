from argklass.command import Command


class Command1(Command):
    """Command1 docstring

    Examples
    --------

    do this
    """

    name = "cmd1"

    @staticmethod
    def execute(args) -> int:
        print("subcmd1")


COMMANDS = Command1
