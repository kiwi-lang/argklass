"""Parity tests: ``argument(*args, **kwargs)`` vs ``ArgumentParser.add_argument``.

The field annotation API is meant to accept the same option strings and keyword
arguments as ``argparse.ArgumentParser.add_argument``, while still producing a
valid ``dataclasses.field`` (defaults, factories, field ordering).
"""

from __future__ import annotations

import argparse
from dataclasses import MISSING, dataclass, fields, is_dataclass
from typing import List, Optional

import pytest

from argklass import ArgumentParser, argument


def _action_by_dest(parser, dest):
    for action in parser._actions:
        if action.dest == dest:
            return action
    raise AssertionError(f"no action with dest={dest!r}")


def _parse_argparse(add_args, add_kwargs, argv):
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument(*add_args, **add_kwargs)
    return p.parse_args(argv)


class TestAddArgumentParity:
    """Same *args/**kwargs as add_argument → same parsed values."""

    def test_short_and_long_optional(self):
        args = ("-x", "--exclude")
        kwargs = dict(type=str, default=None, help="hosts to exclude")
        argv = ["-x", "node1,node2"]

        @dataclass
        class Spec:
            """exclude hosts"""
            exclude: Optional[str] = argument(*args, **kwargs)

        raw = _parse_argparse(args, {**kwargs, "dest": "exclude"}, argv)
        klass = ArgumentParser(dataclass=Spec).parse_args(argv)
        assert klass.exclude == raw.exclude == "node1,node2"

        action = _action_by_dest(ArgumentParser(dataclass=Spec), "exclude")
        assert "-x" in action.option_strings
        assert "--exclude" in action.option_strings

    def test_long_flag_only(self):
        args = ("--count",)
        kwargs = dict(type=int, default=0)
        argv = ["--count", "7"]

        @dataclass
        class Spec:
            """count"""
            count: int = argument(*args, **kwargs)

        raw = _parse_argparse(args, {**kwargs, "dest": "count"}, argv)
        klass = ArgumentParser(dataclass=Spec).parse_args(argv)
        assert klass.count == raw.count == 7

    def test_store_true(self):
        args = ("-v", "--verbose")
        kwargs = dict(action="store_true", help="verbose mode")

        @dataclass
        class Spec:
            """verbose"""
            verbose: bool = argument(*args, **kwargs)

        parser = ArgumentParser(dataclass=Spec)

        raw_off = _parse_argparse(args, {**kwargs, "dest": "verbose"}, [])
        assert parser.parse_args([]).verbose == raw_off.verbose is False

        raw_on = _parse_argparse(args, {**kwargs, "dest": "verbose"}, ["-v"])
        assert parser.parse_args(["-v"]).verbose == raw_on.verbose is True
        assert "-v" in _action_by_dest(parser, "verbose").option_strings

    def test_store_false(self):
        args = ("--no-cache",)
        kwargs = dict(action="store_false")

        @dataclass
        class Spec:
            """cache"""
            cache: bool = argument(*args, **kwargs)

        raw = _parse_argparse(args, {**kwargs, "dest": "cache"}, ["--no-cache"])
        klass = ArgumentParser(dataclass=Spec).parse_args(["--no-cache"])
        assert klass.cache == raw.cache is False

    def test_choices(self):
        args = ("--mode",)
        kwargs = dict(choices=["fast", "slow"], default="fast")
        argv = ["--mode", "slow"]

        @dataclass
        class Spec:
            """mode"""
            mode: str = argument(*args, **kwargs)

        raw = _parse_argparse(args, {**kwargs, "dest": "mode"}, argv)
        klass = ArgumentParser(dataclass=Spec).parse_args(argv)
        assert klass.mode == raw.mode == "slow"

    def test_nargs_star_with_list_default(self):
        args = ("--tags",)
        kwargs = dict(nargs="*", default=[], type=str)
        argv = ["--tags", "a", "b"]

        @dataclass
        class Spec:
            """tags"""
            tags: List[str] = argument(*args, **kwargs)

        raw = _parse_argparse(args, {**kwargs, "dest": "tags"}, argv)
        klass = ArgumentParser(dataclass=Spec).parse_args(argv)
        assert klass.tags == raw.tags == ["a", "b"]

        raw_default = _parse_argparse(args, {**kwargs, "dest": "tags"}, [])
        klass_default = ArgumentParser(dataclass=Spec).parse_args([])
        assert klass_default.tags == raw_default.tags == []

    def test_const_with_store_const(self):
        args = ("--quiet",)
        kwargs = dict(action="store_const", const=0, default=1)

        @dataclass
        class Spec:
            """level"""
            level: int = argument(*args, **kwargs)

        raw = _parse_argparse(args, {**kwargs, "dest": "level"}, ["--quiet"])
        klass = ArgumentParser(dataclass=Spec).parse_args(["--quiet"])
        assert klass.level == raw.level == 0

    def test_metavar_and_help_preserved(self):
        args = ("-f", "--file")
        kwargs = dict(type=str, default=None, metavar="PATH", help="input file")

        @dataclass
        class Spec:
            """file"""
            file: Optional[str] = argument(*args, **kwargs)

        action = _action_by_dest(ArgumentParser(dataclass=Spec), "file")
        assert action.metavar == "PATH"
        assert action.help == "input file"
        assert "-f" in action.option_strings
        assert "--file" in action.option_strings

    def test_explicit_dest_matches_field_name(self):
        """``dest`` defaults to the field name, like ``add_argument(..., dest=name)``."""
        args = ("-x", "--exclude")
        kwargs = dict(type=str, default=None)
        argv = ["--exclude", "a,b"]

        @dataclass
        class Spec:
            """exclude"""
            exclude: Optional[str] = argument(*args, **kwargs)

        raw = _parse_argparse(args, {**kwargs, "dest": "exclude"}, argv)
        klass = ArgumentParser(dataclass=Spec).parse_args(argv)
        assert raw.exclude == klass.exclude == "a,b"
        assert _action_by_dest(ArgumentParser(dataclass=Spec), "exclude").dest == "exclude"

    def test_dest_override_differs_from_dataclass_field(self):
        """argparse allows ``dest !=`` option name; dataclass rebuild needs the field name.

        Passing ``dest=`` that does not match the annotation name is therefore
        not supported end-to-end (add_argument accepts it; grouping cannot).
        """
        args = ("-x", "--exclude")
        kwargs = dict(type=str, default=None, dest="hosts")

        @dataclass
        class Spec:
            """exclude with custom dest"""
            exclude: Optional[str] = argument(*args, **kwargs)

        parser = ArgumentParser(dataclass=Spec)
        assert _action_by_dest(parser, "hosts").dest == "hosts"
        with pytest.raises(TypeError, match="hosts"):
            parser.parse_args(["--exclude", "a,b"])

    def test_remainder_positional(self):
        # Positional REMAINDER: no default on the field (dataclass + argparse).
        argv = ["python", "test", "--flag"]

        @dataclass
        class Spec:
            """remainder"""
            command: list[str] = argument(nargs=argparse.REMAINDER)

        raw = _parse_argparse(["command"], dict(nargs=argparse.REMAINDER), argv)
        klass = ArgumentParser(dataclass=Spec).parse_args(argv)
        assert klass.command == raw.command == ["python", "test", "--flag"]

    def test_append_action(self):
        args = ("-e", "--env")
        kwargs = dict(action="append", default=None)
        argv = ["-e", "A=1", "--env", "B=2"]

        @dataclass
        class Spec:
            """env vars"""
            env: Optional[List[str]] = argument(*args, **kwargs)

        raw = _parse_argparse(args, {**kwargs, "dest": "env"}, argv)
        klass = ArgumentParser(dataclass=Spec).parse_args(argv)
        assert klass.env == raw.env == ["A=1", "B=2"]


class TestDataclassFieldRequirements:
    """argument() must remain a valid dataclasses.field factory."""

    def test_returns_field(self):
        f = argument("-x", "--exclude", default=None)
        assert f.default is None
        assert f.metadata["args"] == ("-x", "--exclude")
        assert f.metadata["kwargs"]["_kind"] == "argument"

    def test_dataclass_fields_expose_argument_metadata(self):
        @dataclass
        class Spec:
            """both defaulted"""
            exclude: Optional[str] = argument("-x", default=None)
            verbose: bool = argument("-v", action="store_true")

        assert is_dataclass(Spec)
        names = [f.name for f in fields(Spec)]
        assert names == ["exclude", "verbose"]
        exclude_field = fields(Spec)[0]
        assert exclude_field.metadata["args"] == ("-x",)

    def test_default_after_default_is_valid(self):
        @dataclass
        class Spec:
            """both defaulted"""
            exclude: Optional[str] = argument("-x", default=None)
            verbose: bool = argument("-v", action="store_true")

        obj = Spec()
        assert obj.exclude is None
        assert obj.verbose is False

    def test_non_default_before_default(self):
        @dataclass
        class Spec:
            """positional then optional"""
            command: list[str] = argument(nargs=argparse.REMAINDER)
            exclude: Optional[str] = argument("-x", default=None)

        obj = Spec(command=["echo", "hi"])
        assert obj.command == ["echo", "hi"]
        assert obj.exclude is None

    def test_non_default_after_default_is_rejected(self):
        with pytest.raises(TypeError, match="non-default argument"):

            @dataclass
            class Spec:
                """invalid ordering"""
                exclude: Optional[str] = argument("-x", default=None)
                command: list[str] = argument(nargs=argparse.REMAINDER)

    def test_mutable_list_default_uses_factory(self):
        f = argument("--tags", default=[], nargs="*")
        assert f.default is MISSING
        assert f.default_factory is not MISSING
        assert f.default_factory() == []
        assert f.default_factory() is not f.default_factory()

    def test_mixed_with_plain_default_field(self):
        @dataclass
        class Spec:
            """plain + argument()"""
            name: str = "default"
            exclude: Optional[str] = argument("-x", "--exclude", default=None)

        ns = ArgumentParser(dataclass=Spec).parse_args(["-x", "n1"])
        assert ns.name == "default"
        assert ns.exclude == "n1"

    def test_combined_remainder_and_short_flag(self):
        """End-to-end shape matching milabench ``slurm srun``."""

        @dataclass
        class Spec:
            """srun-like"""
            command: list[str] = argument(nargs=argparse.REMAINDER)
            exclude: Optional[str] = argument("-x", "--exclude", default=None)

        # Raw argparse equivalent (options before remainder values).
        raw = argparse.ArgumentParser(add_help=False)
        raw.add_argument("-x", "--exclude", type=str, default=None)
        raw.add_argument("command", nargs=argparse.REMAINDER)
        raw_ns = raw.parse_args(["-x", "n1,n2", "python", "test", "--flag"])

        klass = ArgumentParser(dataclass=Spec).parse_args(
            ["-x", "n1,n2", "python", "test", "--flag"]
        )
        assert klass.exclude == raw_ns.exclude == "n1,n2"
        assert klass.command == raw_ns.command == ["python", "test", "--flag"]
