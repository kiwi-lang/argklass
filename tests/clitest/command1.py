from argklass.command import Command


class Command1(Command):
    name = "cmd1"

    @staticmethod
    def execute(args) -> int:
        print("cmd1")


COMMANDS = Command1
