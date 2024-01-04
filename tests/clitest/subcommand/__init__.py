from argklass.command import ParentCommand


class SubCommand(ParentCommand):
    name = "sub"


COMMANDS = SubCommand
