"""Tests for argklass/groupargs.py — argument grouping and conversion."""

import argparse
import dataclasses
from dataclasses import dataclass, field
from typing import Union

import pytest

from argklass.arguments import ArgumentParser, add_arguments, group, subparsers


@dataclass
class GrpSub:
    opt: int = 10  # option


@dataclass
class GrpArgs:
    name: str = "test"  # name
    sub: GrpSub = group(default=GrpSub, help="sub")


@dataclass
class GrpSubA:
    x: int = 0  # x val


@dataclass
class GrpSubB:
    y: str = ""  # y val


@dataclass
class GrpMain:
    cmd: Union[GrpSubA, GrpSubB] = subparsers(a=GrpSubA, b=GrpSubB)


@dataclass
class PnInner:
    val: int = 0  # inner val


class TestGroupArgs:
    def test_group_by_dataclass_conversion(self):
        from argklass.groupargs import group_by_dataclass

        parser = ArgumentParser(group_by_dataclass=True)
        parser.add_arguments(GrpArgs, create_group=True)

        raw_args = argparse.ArgumentParser.parse_args(
            parser, ["--name", "hello", "--opt", "42"]
        )
        result = group_by_dataclass(parser, raw_args, False, True, argparse.Namespace)
        assert hasattr(result, "GrpArgs")

    def test_group_by_parser_with_subparsers(self):
        parser = ArgumentParser(group_by_dataclass=True, group_by_parser=True)
        parser.add_arguments(GrpMain, create_group=True)
        args = parser.parse_args(["a", "--x", "5"])

    def test_groupargs_failed_conversion(self, capsys):
        from argklass.groupargs import GroupArguments

        ga = GroupArguments(argparse.Namespace(x=1), argparse.Namespace)
        ga.group_by_dataclass = True
        ga.new_group("test", argparse.Namespace)
        ga.dest_to_dataclass["test"] = int
        ga.current["x"] = 1
        ga.pop_group()

        out = capsys.readouterr().out
        assert "Could not convert" in out

    def test_dotted_dest_grouping(self):
        parser = ArgumentParser(group_by_dataclass=True)
        add_arguments(parser, PnInner, pathname=True, dest="outer", create_group=True)
        dests = [a.dest for g in parser._action_groups for a in g._group_actions]
        assert any("." in d for d in dests)


class TestGroupArgsFailureModes:
    def test_getattr_missing_attribute(self):
        from argklass.groupargs import _getattr

        obj = argparse.Namespace(a=1)
        assert _getattr(obj, "missing", "default") == "default"

    def test_getattr_falsy_returns_default(self):
        from argklass.groupargs import _getattr

        obj = argparse.Namespace(a=0, b="", c=None)
        assert _getattr(obj, "a", "default") == "default"
        assert _getattr(obj, "b", "default") == "default"
        assert _getattr(obj, "c", "default") == "default"

    def test_convert_with_no_dataclass(self):
        from argklass.groupargs import GroupArguments

        parser = ArgumentParser()
        parser.add_argument("--x", type=int, default=0)

        raw_args = argparse.ArgumentParser.parse_args(parser, ["--x", "5"])
        ga = GroupArguments(raw_args, None)
        result = ga.convert(parser, dataclass=argparse.Namespace)
        assert result.x == 5

    def test_pop_group_none_dataclass(self):
        from argklass.groupargs import GroupArguments

        ga = GroupArguments(argparse.Namespace(), argparse.Namespace)
        ga.new_group("test_group", None)
        ga.current["x"] = 1
        ga.pop_group()
        assert isinstance(ga.current["test_group"], dict)

    def test_format_action_dotted_name(self):
        from argklass.groupargs import GroupArguments

        args = argparse.Namespace(**{"outer.inner": 99})
        ga = GroupArguments(args, argparse.Namespace)

        action = argparse.Action(["--outer.inner"], "outer.inner", default=None)
        ga.format_action(action, 0)
        assert ga.current["outer"]["inner"] == 99

    def test_new_group_existing_namespace(self):
        from argklass.groupargs import GroupArguments

        ga = GroupArguments(argparse.Namespace(), argparse.Namespace)
        ga.current["existing"] = argparse.Namespace(x=1, y=2)
        ga.new_group("existing")
        assert ga.current.get("x") == 1

    def test_format_subparser_no_dest(self):
        from argklass.groupargs import GroupArguments

        ga = GroupArguments(argparse.Namespace(), argparse.Namespace)

        class FakeSubparsersAction:
            dest = "nonexistent"
            choices = {}

        assert ga.format_subparser(FakeSubparsersAction(), 0) is False

    def test_format_action_ignores_none_default(self):
        from argklass.groupargs import GroupArguments

        args = argparse.Namespace(myopt=None)
        ga = GroupArguments(args, argparse.Namespace)
        ga.ignore_default = True

        action = argparse.Action(["--myopt"], "myopt", default=None)
        ga.format_action(action, 0)
        assert "myopt" not in ga.current

    def test_format_action_keeps_value(self):
        from argklass.groupargs import GroupArguments

        args = argparse.Namespace(myopt=42)
        ga = GroupArguments(args, argparse.Namespace)
        ga.ignore_default = True

        action = argparse.Action(["--myopt"], "myopt", default=None)
        ga.format_action(action, 0)
        assert ga.current["myopt"] == 42


# ---------------------------------------------------------------------------
# pathname=True: nested dataclasses named by path, and rebuilt from it
# ---------------------------------------------------------------------------


@dataclass
class PnLeaf:
    a: int = 1  # a val
    b: str = "x"  # b val


@dataclass
class PnMid:
    leaf: PnLeaf = field(default_factory=PnLeaf)
    mid_only: float = 2.5  # mid val


@dataclass
class PnRoot:
    mid: PnMid = field(default_factory=PnMid)
    root_only: int = 7  # root val


def pathname_parser(dataclass=PnRoot, dest="cfg"):
    parser = ArgumentParser(group_by_dataclass=True)
    parser.add_arguments(dataclass, dest=dest, pathname=True)
    return parser


class TestPathnameRebuild:
    """A dataclass tree named by path has to come back as that tree.

    ``pathname=True`` creates no argument group per nested dataclass -- only
    dotted dests -- so the parser has to remember what each dotted prefix was
    built from, or the result can only ever be nested dicts.
    """

    def test_flags_are_named_by_path(self):
        parser = pathname_parser()
        flags = [a.option_strings[0] for a in parser._actions if a.option_strings]
        assert "--cfg.mid.leaf.a" in flags
        assert "--cfg.mid.mid_only" in flags
        assert "--cfg.root_only" in flags

    def test_rebuilds_the_whole_tree(self):
        args = pathname_parser().parse_args([])
        assert isinstance(args.cfg, PnRoot)
        assert isinstance(args.cfg.mid, PnMid)
        assert isinstance(args.cfg.mid.leaf, PnLeaf)

    def test_two_fields_in_one_group(self):
        """Re-entering a group used to raise: vars() on the dict left behind by
        the first field."""
        args = pathname_parser().parse_args([])
        assert (args.cfg.mid.leaf.a, args.cfg.mid.leaf.b) == (1, "x")

    def test_values_land_at_their_own_depth(self):
        args = pathname_parser().parse_args(
            [
                "--cfg.mid.leaf.a",
                "10",
                "--cfg.mid.leaf.b",
                "changed",
                "--cfg.mid.mid_only",
                "9.5",
                "--cfg.root_only",
                "3",
            ]
        )
        assert args.cfg.mid.leaf.a == 10
        assert args.cfg.mid.leaf.b == "changed"
        assert args.cfg.mid.mid_only == 9.5
        assert args.cfg.root_only == 3

    def test_untouched_fields_keep_their_defaults(self):
        args = pathname_parser().parse_args(["--cfg.mid.leaf.a", "10"])
        assert args.cfg.mid.leaf.b == "x"
        assert args.cfg.root_only == 7

    def test_post_init_runs_on_the_rebuilt_instance(self):
        @dataclass
        class Validated:
            value: int = 1

            def __post_init__(self):
                if self.value < 0:
                    raise ValueError("value must be >= 0")
                self.seen = True

        @dataclass
        class HasValidated:
            inner: Validated = field(default_factory=Validated)

        args = pathname_parser(HasValidated).parse_args(["--cfg.inner.value", "5"])
        assert args.cfg.inner.seen is True

        with pytest.raises(ValueError, match="value must be >= 0"):
            pathname_parser(HasValidated).parse_args(["--cfg.inner.value", "-1"])

    def test_no_dest_means_no_synthetic_prefix(self):
        """Without a dest the flags used to be prefixed with a literal "None."."""
        parser = ArgumentParser(group_by_dataclass=True)
        parser.add_arguments(PnRoot, pathname=True)
        flags = [a.option_strings[0] for a in parser._actions if a.option_strings]
        assert "--mid.leaf.a" in flags
        assert not any(f.startswith("--None") for f in flags)

    def test_no_dest_still_rebuilds_each_section(self):
        parser = ArgumentParser(group_by_dataclass=True)
        parser.add_arguments(PnRoot, pathname=True)
        args = parser.parse_args(["--mid.leaf.a", "4"])
        assert isinstance(args.mid, PnMid)
        assert args.mid.leaf.a == 4

    def test_an_unregistered_dotted_dest_stays_a_dict(self):
        """Hand-written dotted flags have no dataclass behind them, and must
        keep behaving as they did."""
        parser = ArgumentParser(group_by_dataclass=True)
        parser.add_argument("--plain.x", type=int, default=1)
        parser.add_argument("--plain.y", type=int, default=2)
        args = parser.parse_args([])
        assert args.plain == {"x": 1, "y": 2}


class TestAsMapping:
    def test_dict_passes_through_unchanged(self):
        from argklass.groupargs import _as_mapping

        d = {"a": 1}
        assert _as_mapping(d) is d

    def test_namespace_becomes_its_dict(self):
        from argklass.groupargs import _as_mapping

        assert _as_mapping(argparse.Namespace(a=1, b=2)) == {"a": 1, "b": 2}

    def test_dataclass_instance_becomes_its_fields(self):
        from argklass.groupargs import _as_mapping

        assert _as_mapping(PnLeaf(a=3, b="y")) == {"a": 3, "b": "y"}

    def test_slotted_dataclass_has_no_dict_but_still_works(self):
        from argklass.groupargs import _as_mapping

        @dataclass(slots=True)
        class Slotted:
            a: int = 1

        assert _as_mapping(Slotted(a=2)) == {"a": 2}


# ---------------------------------------------------------------------------
# pathname=True also gets one argument group per nested dataclass
# ---------------------------------------------------------------------------


@dataclass
class SecLeaf:
    """Leaf options."""

    a: int = 1  # a val


@dataclass
class SecMid:
    """Mid options."""

    leaf: SecLeaf = field(default_factory=SecLeaf)
    mid_only: int = 2  # mid val


@dataclass
class SecRoot:
    mid: SecMid = field(default_factory=SecMid)
    root_only: int = 3  # root val


class TestPathnameSections:
    """Path-named flags are readable only if they are grouped: one flat list of
    eighty is not a --help anyone reads. The groups are cosmetic -- the parsed
    shape still comes from the dotted dests."""

    def make(self):
        parser = ArgumentParser(group_by_dataclass=True)
        parser.add_arguments(SecRoot, pathname=True)
        return parser

    def test_a_group_per_nested_dataclass(self):
        titles = [g.title for g in self.make()._action_groups]
        assert "mid" in titles
        assert "mid.leaf" in titles

    def test_the_group_carries_the_class_docstring(self):
        groups = {g.title: g for g in self.make()._action_groups}
        assert groups["mid"].description == "Mid options."
        assert groups["mid.leaf"].description == "Leaf options."

    def test_each_flag_is_in_its_own_section(self):
        groups = {g.title: g for g in self.make()._action_groups}
        assert [a.dest for a in groups["mid.leaf"]._group_actions] == ["mid.leaf.a"]
        assert [a.dest for a in groups["mid"]._group_actions] == ["mid.mid_only"]

    def test_grouping_does_not_change_the_parsed_shape(self):
        args = self.make().parse_args(["--mid.leaf.a", "9"])
        assert isinstance(args.mid, SecMid), type(args.mid)
        assert isinstance(args.mid.leaf, SecLeaf)
        assert args.mid.leaf.a == 9
        assert args.mid.mid_only == 2
        assert args.root_only == 3

    def test_field_help_survives_the_nesting(self):
        """A nested dataclass is added with create_group=False, which reads the
        class docstring -- that used to leave the source cursor past every
        field, losing the help for all of them."""
        parser = self.make()
        helps = {a.dest: a.help for a in parser._actions if a.option_strings}
        assert helps["mid.leaf.a"] == "a val"
        assert helps["mid.mid_only"] == "mid val"
        assert helps["root_only"] == "root val"


@dataclass
class NoDocstring:
    value: int = 1  # the value

    def method(self):
        """A method docstring, which is not the class's."""
        return self.value


class TestDocstringCursor:
    def test_a_method_docstring_is_not_taken_for_the_class_docstring(self):
        from argklass.docstring import DocstringIterator

        it = DocstringIterator(NoDocstring)
        assert it.get_dataclass_docstring() is None

    def test_fields_are_still_found_afterwards(self):
        from argklass.docstring import DocstringIterator

        it = DocstringIterator(NoDocstring)
        it.get_dataclass_docstring()
        field_ = dataclasses.fields(NoDocstring)[0]
        assert it.find_field(field_) == "the value"

    def test_a_real_class_docstring_is_still_read(self):
        from argklass.docstring import DocstringIterator

        it = DocstringIterator(SecLeaf)
        assert it.get_dataclass_docstring() == "Leaf options."
        assert it.find_field(dataclasses.fields(SecLeaf)[0]) == "a val"
