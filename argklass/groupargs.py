import argparse
from dataclasses import fields, is_dataclass
from typing import Any

from .argformat import ArgumentFormaterBase


def _getattr(obj, name, default):
    value = default

    if hasattr(obj, name):
        return getattr(obj, name) or default

    return value


def _as_mapping(value):
    """The fields of an already-built group, back as a plain dict.

    A group can be re-entered -- ``--a.b.x`` and ``--a.b.y`` both pass through
    ``a.b`` -- and by then it may be a dict (built by an earlier action), or a
    Namespace / dataclass instance (converted by an earlier ``pop_group``).
    All three have to keep accumulating into the same group.
    """
    if isinstance(value, dict):
        return value

    if is_dataclass(value) and not isinstance(value, type):
        # A copy, not __dict__: it gets re-converted on the way out, and a
        # dataclass declared with __slots__ has no __dict__ at all.
        return {f.name: getattr(value, f.name) for f in fields(value)}

    return vars(value)


class GroupArguments(ArgumentFormaterBase):
    def __init__(self, args, dataclass=argparse.Namespace, path_dataclasses=None):
        super().__init__()

        self.args = args
        self.root = dict()
        self.stack = [(self.root, None, dataclass)]
        # Dotted path -> the dataclass it was generated from, for arguments
        # named by path (``pathname=True``) rather than grouped. Those create
        # no argparse group, so this is the only record of what each level of
        # ``--outer.inner.field`` was built from.
        self.path_dataclasses = path_dataclasses or {}
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
            newgroup = _as_mapping(newgroup)
        else:
            newgroup = dict()

        self.current[name] = newgroup
        self.stack.append((newgroup, name, dataclass))

    def pop_group(self):
        group, name, pushed = self.stack.pop()

        dataclass = None
        if pushed is not None:
            dataclass = self.dest_to_dataclass.get(name)
            # Fall back to what new_group was handed, but only if it is a real
            # dataclass: an un-registered path segment stays a plain dict, as
            # it always has, and is rebuilt in convert() instead.
            if dataclass is None and is_dataclass(pushed):
                dataclass = pushed

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

        # Path-named arguments accumulate as nested dicts, because they create
        # no argparse group to hang a type on. Rebuild them here, innermost
        # first, so a parent is built from children that are already instances.
        group = self._rebuild_paths(group, ())

        dataclass = dataclass or dataclass_default

        if dataclass is not None:
            group = dataclass(**group)

        return group

    def _rebuild_paths(self, node, prefix: tuple):
        """Turn the dict tree under *prefix* back into the dataclasses it came
        from, bottom-up. Anything not registered is left exactly as it is."""
        if not isinstance(node, dict):
            return node

        rebuilt = {
            key: self._rebuild_paths(value, prefix + (key,))
            for key, value in node.items()
        }

        dataclass = self.path_dataclasses.get(".".join(prefix))
        if dataclass is None:
            return rebuilt

        try:
            return dataclass(**rebuilt)
        except TypeError as exc:
            # Naming the path and the class beats argparse's own report, which
            # would only say a keyword was unexpected.
            raise TypeError(
                f"could not rebuild {'.'.join(prefix) or '<root>'} as "
                f"{dataclass.__name__}: {exc}"
            ) from exc

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
                and not getattr(group, "_pathname_only", False)
                and self.group_by_dataclass
            ):
                if parent_path:
                    for ancestor in parent_path:
                        self.new_group(ancestor)
                        parent_pops += 1

                assert dest is not None
                self.new_group(dest, dataclass)
                pop_group = True

            if not getattr(group, "_pathname_only", False):
                # A cosmetic group (help formatting for path-named arguments)
                # must not claim the name its dotted dests rebuild under, or
                # the section is converted to a Namespace before _rebuild_paths
                # can make it the dataclass it came from.
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

            if (
                self.group_by_dataclass
                and dest not in self.ignore_groups
                and not getattr(nested, "_pathname_only", False)
            ):
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
    gp = GroupArguments(
        args, dataclass, path_dataclasses=getattr(parser, "_pathname_dataclasses", None)
    )
    gp.group_by_parser = group_by_parser
    gp.group_by_dataclass = group_by_dataclass
    return gp.convert(parser)
