from argklass.command import Command


class Command3(Command):
    name = "cmd3"

    @staticmethod
    def execute(args) -> int:
        print("subcmd3")


COMMANDS = Command3
