from argklass.command import Command


class Command2(Command):
    name = "cmd2"

    @staticmethod
    def execute(args) -> int:
        print("cmd2")


COMMANDS = Command2
