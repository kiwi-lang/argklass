import argparse
from typing import Any

from .argformat import ArgumentFormaterBase


def _getattr(obj, name, default):
    value = default

    if hasattr(obj, name):
        return getattr(obj, name) or default

    return value


class GroupArguments(ArgumentFormaterBase):
    def __init__(self, args, dataclass=argparse.Namespace):
        super().__init__()

        self.args = args
        self.root = dict()
        self.stack = [(self.root, None, dataclass)]
        self.group_by_parser = False
        self.group_parser_name = "dest"
        self.group_by_dataclass = False
        self.ignore_default = False
        self.ignore_groups = {
            "positional arguments",
            "optional arguments",
            "options",
        }
        # because action names with . inside will get grouped
        # we do not know all the time if a group should be created or not
        self.dest_to_dataclass = dict()

    @property
    def current(self):
        return self.stack[-1][0]

    def new_group(self, name, dataclass=argparse.Namespace):
        newgroup = self.current.get(name)

        if newgroup is not None:
            newgroup = vars(newgroup)
        else:
            newgroup = dict()

        self.current[name] = newgroup
        self.stack.append((newgroup, name, dataclass))

    def pop_group(self):
        group, name, dataclass = self.stack.pop()

        if dataclass is not None:
            dataclass = self.dest_to_dataclass.get(name)

        if dataclass is not None:
            try:
                group = dataclass(**group)
            except TypeError:
                print(
                    f"Could not convert arguments `{name}` to dataclass {dataclass.__name__}, were some fields not grouped ?"
                )
                group = argparse.Namespace(**group)

            self.current[name] = group

    def convert(self, parser: argparse.ArgumentParser, dataclass=None):
        self(parser, depth=0)
        assert len(self.stack) == 1

        group, _, dataclass_default = self.stack.pop()

        dataclass = dataclass or dataclass_default

        if dataclass is not None:
            group = dataclass(**group)

        return group

    def __call__(self, parser: argparse.ArgumentParser, depth: int = 0) -> Any:
        for group in parser._action_groups:
            pop_group = False
            parent_pops = 0

            dataclass = _getattr(group, "_dataclass", argparse.Namespace)
            dest = _getattr(group, "_dest", group.title)
            parent_path = getattr(group, "_parent_path", None)

            if (
                isinstance(group, argparse._ArgumentGroup)
                and group.title not in self.ignore_groups
                and self.group_by_dataclass
            ):
                if parent_path:
                    for ancestor in parent_path:
                        self.new_group(ancestor)
                        parent_pops += 1

                assert dest is not None
                self.new_group(dest, dataclass)
                pop_group = True

            self.dest_to_dataclass[dest] = dataclass
            self.format_group(group, depth)

            if pop_group:
                self.pop_group()

            for _ in range(parent_pops):
                self.pop_group()

    def format_group(self, group: argparse._ArgumentGroup, depth: int):
        for action in group._group_actions:
            if isinstance(action, argparse._SubParsersAction):
                if self.format_subparser(action, depth):
                    return

            else:
                self.format_action(action, depth + 1)

        for nested in getattr(group, "_action_groups", []):
            if nested is group:
                continue

            dataclass = _getattr(nested, "_dataclass", argparse.Namespace)
            dest = _getattr(nested, "_dest", nested.title)
            pop_group = False

            if self.group_by_dataclass and dest not in self.ignore_groups:
                self.new_group(dest, dataclass)
                self.dest_to_dataclass[dest] = dataclass
                pop_group = True

            self.format_group(nested, depth + 1)

            if pop_group:
                self.pop_group()

    def format_subparser(self, action: argparse._SubParsersAction, depth: int):
        if not hasattr(self.args, action.dest):
            return False

        key = getattr(self.args, action.dest)

        assert self.group_parser_name in ("key", "dest")

        if self.group_parser_name == "key":
            group_name = key
        else:
            group_name = action.dest

        if self.group_by_parser:
            self.new_group(group_name)
        else:
            self.root[action.dest] = key

        choice = action.choices[key]
        self(choice, depth + 2)

        if self.group_by_parser:
            self.pop_group()

        return True

    def format_action(self, action: argparse.Action, depth: int, name=None):
        name = name or action.dest

        if hasattr(self.args, name):
            path = name.split(".")
            for p in path[:-1]:
                self.new_group(p)

            value = getattr(self.args, name)

            if not (self.ignore_default and value is None):
                # Check here if the value is the default
                self.current[path[-1]] = value

            for p in path[:-1]:
                self.pop_group()


def group_by_dataclass(
    parser, args, group_by_parser, group_by_dataclass, dataclass=argparse.Namespace
):
    gp = GroupArguments(args, dataclass)
    gp.group_by_parser = group_by_parser
    gp.group_by_dataclass = group_by_dataclass
    return gp.convert(parser)
