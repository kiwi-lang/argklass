from argklass.command import Command


class Command4(Command):
    """Status check with no arguments"""

    name = "cmd4"

    @staticmethod
    def execute(args) -> int:
        print("ok")


COMMANDS = Command4
