from .argformat import HelpAction
from .arguments import ArgumentParser
from .plugin import discover_module_commands


class CommandLineInterface:
    def __init__(self, module, *args, **kwargs):
        kwargs.setdefault("add_help", False)
        self.args = None
        self.parser = ArgumentParser(*args, **kwargs)

        if kwargs["add_help"] is False:
            self.parser.add_argument(
                "-h",
                "--help",
                action=HelpAction,
                help="show this help message and exit",
            )

        subparsers = self.parser.add_subparsers(dest="command")
        self.commands = discover_module_commands(module)

        for cmd in self.commands.values():
            cmd.arguments(subparsers)

    def parse_args(self, *args, **kwargs):
        self.args = self.parser.parse_args(*args, **kwargs)
        return self.args

    def execute(self, args):
        cmd = vars(args).pop("command")

        if cmd is None:
            self.parser.print_help()
            return

        return self.commands[cmd](args)
