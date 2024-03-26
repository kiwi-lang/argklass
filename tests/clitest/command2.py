from argklass.command import Command


class Command2(Command):
    """Command2 docstring

    Examples
    --------

    do this
    """

    name = "cmd2"

    @staticmethod
    def execute(args) -> int:
        print("cmd2")


COMMANDS = Command2
