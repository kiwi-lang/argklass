"""Tests for argklass/arguments.py — parser, fields, type helpers, error handling."""

import argparse
import dataclasses
import sys
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple, Union

import pytest


from argklass.arguments import (
    ArgumentParser,
    ArgumentParsingError,
    _add_flag,
    _group,
    _option_strings,
    add_arguments,
    argument,
    argument_parser,
    cache_aware_parse_args,
    choice,
    cvt_type,
    deduceable,
    field,
    group,
    leaf_type,
    parse,
    parse_args,
    subparsers,
    to_enum,
    tuple_action,
)


@dataclass
class ParseSimple:
    x: int = 5  # x val


@dataclass
class DCArgs:
    val: int = 10  # val


@dataclass
class PnInner:
    val: int = 0  # inner val


@dataclass
class PnOuter:
    inner: PnInner = group(default=PnInner, help="inner group")


class TestFieldHelpers:
    def test_deduceable_with_value(self):
        f = deduceable(lambda: 42)
        assert f.default == 42

    def test_deduceable_with_none(self):
        f = deduceable(lambda: None)
        assert f.metadata.get("required") is True

    def test_deduceable_not_required(self):
        f = deduceable(lambda: None, required=False)
        assert f.metadata.get("required") is False

    def test_field_with_choices(self):
        f = field(default="a", choices=["a", "b", "c"])
        assert f.metadata["choices"] == ["a", "b", "c"]

    def test_field_with_type(self):
        f = field(default=0, type=int)
        assert f.metadata["type"] is int

    def test_subparser_field(self):
        f = subparsers()
        assert f.metadata["_kind"] == "subparsers"

    def test_group_field(self):
        g = group(default=DCArgs, help="my help")
        assert g.metadata["_kind"] == "group"
        assert g.metadata["description"] == "my help"

    def test_argument_store_true(self):
        f = argument(action="store_true")
        assert f.default is False

    def test_argument_store_false(self):
        f = argument(action="store_false")
        assert f.default is True

    def test_argument_with_list_default(self):
        f = argument(default=[1, 2, 3])
        assert f.default is not [1, 2, 3]

    def test_argument_with_dict_default(self):
        f = argument(default={"a": 1})
        assert f.default is not {"a": 1}

    def test_argument_with_metadata(self):
        f = argument(default=5, metadata={"custom": "val"})
        assert f.metadata["kwargs"].get("custom") == "val"

    def test_argument_custom_flags(self):
        f = argument("-x", "--exclude", default=None)
        assert f.metadata["args"] == ("-x", "--exclude")
        assert f.metadata["kwargs"]["_kind"] == "argument"
        assert f.default is None


class TestTypeHelpers:
    def test_cvt_type_optional(self):
        assert "Optional" in cvt_type("str | None")

    def test_cvt_type_normal(self):
        assert cvt_type("int") == "int"

    def test_cvt_type_non_string(self):
        assert cvt_type(int) is int

    def test_cvt_type_with_none_input(self):
        assert cvt_type(None) is None

    def test_leaf_type_optional(self):
        assert leaf_type(Optional[int]) is int

    def test_leaf_type_plain(self):
        assert leaf_type(int) is int

    def test_is_optional_non_optional(self):
        from argklass.arguments import is_optional
        assert is_optional(int, 0) is False

    def test_is_list_non_list(self):
        from argklass.arguments import is_list
        assert is_list(int, 0) is False

    def test_is_tuple_non_tuple(self):
        from argklass.arguments import is_tuple
        assert is_tuple(int, 0) == 0

    def test_is_enum_non_enum(self):
        from argklass.arguments import is_enum
        assert is_enum(int, 0) is False


class TestEnumConversion:
    def test_to_enum_by_name(self):
        class Color(Enum):
            RED = 1
            GREEN = 2

        cvt = to_enum(Color)
        assert cvt("RED") == Color.RED
        assert cvt("GREEN") == Color.GREEN

    def test_to_enum_by_index(self):
        class Color(Enum):
            RED = 1
            GREEN = 2

        cvt = to_enum(Color)
        assert cvt("0") == Color.RED
        assert cvt("1") == Color.GREEN

    def test_to_enum_by_value_fallback(self):
        class Status(Enum):
            OK = "ok"
            ERR = "err"

        cvt = to_enum(Status)
        assert cvt("ok") == Status.OK

    def test_to_enum_invalid_name(self):
        class Color(Enum):
            RED = 1
            GREEN = 2

        cvt = to_enum(Color)
        assert cvt("BLUE") is None

    def test_to_enum_negative_index(self):
        class Color(Enum):
            RED = 1
            GREEN = 2

        cvt = to_enum(Color)
        assert cvt("-1") == Color.GREEN

    def test_to_enum_out_of_range_index(self):
        class Color(Enum):
            RED = 1
            GREEN = 2

        cvt = to_enum(Color)
        assert cvt("999") is None


class TestTupleAction:
    def test_tuple_action_parsing(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("--pair", action=tuple_action(Tuple[int, int]), default=(0, 0))
        args = parser.parse_args(["--pair", "3,4"])
        assert args.pair == (3, 4)

    def test_tuple_action_separate_values(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("--pair", action=tuple_action(Tuple[int, int]), nargs="*", default=(0, 0))
        args = parser.parse_args(["--pair", "3", "4"])
        assert args.pair == (3, 4)

    def test_tuple_action_single_comma_string(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("--pair", action=tuple_action(Tuple[int, int]), nargs="*", default=(0, 0))
        args = parser.parse_args(["--pair", "10,20"])
        assert args.pair == (10, 20)


class TestArgumentParser:
    def test_with_dataclass(self):
        parser = ArgumentParser(dataclass=DCArgs)
        args = parser.parse_args([])
        assert args.val == 10

    def test_set_defaults(self):
        parser = ArgumentParser()
        parser.add_arguments(DCArgs, create_group=True)
        parser.set_defaults({"DCArgs": {"val": 99}})

    def test_save_defaults(self):
        parser = ArgumentParser()
        parser.add_arguments(DCArgs, create_group=True)
        result = parser.save_defaults({})
        assert isinstance(result, dict)

    def test_argument_parser_helper(self):
        p = argument_parser(ParseSimple)
        assert isinstance(p, ArgumentParser)

    def test_parse_function(self, monkeypatch):
        import sys
        monkeypatch.setattr(sys, "argv", ["prog"])
        result = parse(ParseSimple)
        assert result.x == 5

    def test_add_arguments_with_pathname(self):
        parser = ArgumentParser()
        add_arguments(parser, PnOuter, pathname=True, dest="outer")

    def test_option_strings_with_underscore(self):
        flags = _option_strings("my_option")
        assert "--my_option" in flags
        assert "--my-option" in flags

    def test_option_strings_no_underscore(self):
        assert _option_strings("simple") == ["--simple"]

    def test_option_strings_appends_custom_flags(self):
        flags = _option_strings("exclude", ["-x"])
        assert flags == ["--exclude", "-x"]

    def test_custom_short_flag_parsing(self):
        @dataclass
        class ExcludeArgs:
            """exclude args"""
            exclude: Optional[str] = argument("-x", default=None)  # hosts

        parser = ArgumentParser(dataclass=ExcludeArgs)
        args = parser.parse_args(["-x", "node1,node2"])
        assert args.exclude == "node1,node2"

        args = parser.parse_args(["--exclude", "node3"])
        assert args.exclude == "node3"

    def test_add_flag_true_default(self):
        grp = argparse.ArgumentParser().add_argument_group("test")
        f = dataclasses.field(default=True, metadata={})
        _add_flag(grp, f, "no_verbose", "disable verbose")

        action = None
        for a in grp._group_actions:
            if a.dest == "no_verbose":
                action = a
                break
        assert action is not None
        assert isinstance(action, argparse._StoreFalseAction)

    def test_group_helper(self):
        assert _group(object, title="t") == "t"
        assert _group(object, dest="d") == "d"

        class MyDC:
            pass
        assert _group(MyDC) == "MyDC"


class TestArgumentParserErrors:
    def test_error_with_exception(self):
        parser = ArgumentParser(use_exception=True)
        parser.add_argument("--x", type=int)

        with pytest.raises(ArgumentParsingError):
            argparse.ArgumentParser.parse_args(parser, ["--x", "not_a_number"])

    def test_error_fail(self):
        parser = ArgumentParser(use_exception=True)
        parser.add_argument("--x", type=int)

        with pytest.raises(ArgumentParsingError) as exc_info:
            argparse.ArgumentParser.parse_args(parser, ["--x", "bad"])

        with pytest.raises(SystemExit):
            exc_info.value.fail()

    def test_error_without_exception(self):
        parser = ArgumentParser(use_exception=False)
        parser.add_argument("--x", type=int)

        with pytest.raises(SystemExit):
            argparse.ArgumentParser.parse_args(parser, ["--x", "bad"])

    def test_parsing_error_message(self):
        parser = ArgumentParser()
        err = ArgumentParsingError(parser, "bad input")
        assert err.parser is parser
        assert err.message == "bad input"


class TestCacheAwareParseArgs:
    def test_success(self):
        parser = ArgumentParser()
        parser.add_argument("--x", type=int, default=0)
        args, p = cache_aware_parse_args(parser, ["--x", "5"])
        assert args.x == 5

    def test_fail_no_rebuild(self):
        parser = ArgumentParser(use_exception=True)
        parser.add_argument("pos", type=str)

        with pytest.raises(SystemExit):
            cache_aware_parse_args(parser, ["--totally-wrong-arg"])

    def test_fail_with_rebuild(self):
        from concurrent.futures import Future
        from unittest.mock import MagicMock

        from argklass.cache import CacheStatus, thread_futures

        parser = ArgumentParser(use_exception=True)
        parser.add_argument("pos", type=str)

        f = Future()
        f.set_result(CacheStatus.Updated)
        old_futures = dict(thread_futures)
        thread_futures["test_rebuild"] = f

        new_parser = ArgumentParser(use_exception=True)
        new_parser.add_argument("pos", type=str)
        rebuild_fn = MagicMock(return_value=new_parser)

        try:
            with pytest.raises(SystemExit):
                cache_aware_parse_args(parser, ["--totally-wrong-arg"], rebuild_parser=rebuild_fn)
            rebuild_fn.assert_called_once()
        finally:
            thread_futures.clear()
            thread_futures.update(old_futures)

    def test_rebuild_succeeds(self):
        from concurrent.futures import Future
        from unittest.mock import MagicMock

        from argklass.cache import CacheStatus, thread_futures

        parser = ArgumentParser(use_exception=True)
        parser.add_argument("pos", type=str)

        f = Future()
        f.set_result(CacheStatus.Updated)
        old_futures = dict(thread_futures)
        thread_futures["test_rebuild_ok"] = f

        new_parser = ArgumentParser(use_exception=True)
        new_parser.add_argument("--name", type=str, default="ok")
        rebuild_fn = MagicMock(return_value=new_parser)

        try:
            args, p = cache_aware_parse_args(parser, ["--name", "rebuilt"], rebuild_parser=rebuild_fn)
            assert args.name == "rebuilt"
            rebuild_fn.assert_called_once()
        finally:
            thread_futures.clear()
            thread_futures.update(old_futures)

    def test_parse_with_config(self):
        parser = ArgumentParser()
        parser.add_arguments(DCArgs, create_group=True)
        parse_args(parser, [], config={"DCArgs": {"val": 77}})


class TestFlatGroups:
    """Verify that nested_groups=False produces a working parser without nesting."""

    def test_flat_groups_no_nesting_warning(self, monkeypatch):
        import warnings

        from argklass.settings import settings

        monkeypatch.setattr(settings, "nested_groups", False)

        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)

            parser = ArgumentParser()
            parser.add_arguments(PnOuter, create_group=True)
            args = parser.parse_args(["--val", "42"])
            assert args.val == 42

    def test_flat_groups_parsing_works(self, monkeypatch):
        from argklass.settings import settings

        monkeypatch.setattr(settings, "nested_groups", False)

        parser = ArgumentParser()
        parser.add_arguments(PnOuter, create_group=True)
        args = parser.parse_args(["--val", "7"])
        assert args.val == 7

    def test_flat_groups_with_pathname(self, monkeypatch):
        import warnings

        from argklass.settings import settings

        monkeypatch.setattr(settings, "nested_groups", False)

        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            parser = ArgumentParser()
            add_arguments(parser, PnOuter, pathname=True, dest="outer")

    @pytest.mark.xfail(
        sys.version_info >= (3, 14),
        reason="argparse forbids nested argument groups since Python 3.14",
        raises=ValueError,
    )
    def test_nested_groups_still_works(self, monkeypatch):
        import warnings

        from argklass.settings import settings

        monkeypatch.setattr(settings, "nested_groups", True)

        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", "Nesting argument groups is deprecated")
            parser = ArgumentParser()
            parser.add_arguments(PnOuter, create_group=True)
            args = parser.parse_args(["--val", "99"])
            assert isinstance(args, argparse.Namespace)
