from .argformat import HelpAction, HelpActionException
from .arguments import ArgumentParser
from .config import apply_config, apply_defaults, save_as_config, save_defaults
from .plugin import CommandRegistry, discover_module_commands


class CommandLineInterface:
    def __init__(self, module, *args, **kwargs):
        kwargs.setdefault("add_help", False)
        self.args = None
        self.parser = ArgumentParser(*args, **kwargs)

        if kwargs["add_help"] is False:
            self.parser.add_argument(
                "-h",
                "--help",
                action=HelpAction.with_exception,
                help="show this help message and exit",
            )

        subparsers = self.parser.add_subparsers(dest="command")
        self.commands = discover_module_commands(module)

        if isinstance(self.commands, CommandRegistry):
            values = self.commands.found_commands.values()
        else:
            values = self.commands.values()

        for cmd in values:
            cmd.arguments(subparsers)

    def save_defaults(self, path):
        """Save parser defaults to a configuration file"""
        save_defaults(self.parser, path)

    def load_defaults(self, path):
        """Apply a configuration file to the parser, updating the default configs"""
        apply_defaults(self.parser, path)

    def apply_config(self, path):
        """Apply a configuration file on parsed arguments overriding them"""
        apply_config(self.parser, self.args, path)

    def save_config(self, path):
        """Save current arguments in a configuration file"""
        save_as_config(self.parser, self.args, path)

    def parse_args(self, *args, **kwargs):
        self.args = self.parser.parse_args(*args, **kwargs)
        return self.args

    def execute(self, args):
        """Dispatch to the right command"""
        cmd = vars(args).pop("command")

        if cmd is None:
            self.parser.print_help()
            return

        commands = self.commands.found_commands
        return commands[cmd](args)

    def run(self, *argv, **kwargs):
        """Parse and dispatch"""
        try:
            args = self.parse_args(*argv, **kwargs)
        except HelpActionException:
            self.parser.exit()

        return self.execute(args)
