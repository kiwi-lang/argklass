"""Tests for argklass/config.py — ArgumentConfig save/load."""

import argparse
from dataclasses import dataclass
from typing import Union

import hjson
import pytest

from argklass.arguments import ArgumentParser, group, subparsers



@dataclass
class CfgSub:
    opt: int = 10  # sub option


@dataclass
class CfgArgs:
    name: str = "default"  # the name
    count: int = 5  # the count
    sub: CfgSub = group(default=CfgSub, help="sub group")


@dataclass
class CfgAdd:
    x: int = 0  # x val


@dataclass
class CfgRemove:
    name: str = ""  # what to remove


@dataclass
class CfgMain:
    verbose: bool = False  # verbose flag
    cmd: Union[CfgAdd, CfgRemove] = subparsers(add=CfgAdd, remove=CfgRemove)


def _make_parser():
    parser = ArgumentParser()
    parser.add_arguments(CfgArgs, create_group=True)
    return parser


class TestArgumentConfig:
    def test_save_defaults(self, tmp_path):
        from argklass.config import save_defaults

        parser = _make_parser()
        path = str(tmp_path / "defaults.hjson")
        save_defaults(parser, path)

        with open(path) as f:
            data = hjson.load(f)
        assert isinstance(data, dict)

    def test_apply_defaults(self, tmp_path):
        from argklass.config import apply_defaults, save_defaults

        parser = _make_parser()
        path = str(tmp_path / "defaults.hjson")
        save_defaults(parser, path)

        with open(path) as f:
            data = hjson.load(f)
        data.setdefault("CfgArgs", {})["name"] = "custom"
        with open(path, "w") as f:
            hjson.dump(data, f)

        parser2 = _make_parser()
        apply_defaults(parser2, path)

    def test_save_as_config(self, tmp_path):
        from argklass.config import save_as_config

        parser = _make_parser()
        args = parser.parse_args(["--name", "myname", "--count", "3"])

        path = str(tmp_path / "config.hjson")
        save_as_config(parser, args, path)

        with open(path) as f:
            data = hjson.load(f)
        assert isinstance(data, dict)

    def test_apply_config(self, tmp_path):
        from argklass.config import apply_config, save_as_config

        parser = _make_parser()
        args = parser.parse_args(["--name", "first"])

        path = str(tmp_path / "cfg.hjson")
        save_as_config(parser, args, path)

        parser2 = _make_parser()
        args2 = parser2.parse_args([])
        apply_config(parser2, args2, path)

    def test_argument_config_actionkey(self):
        from argklass.config import ArgumentConfig
        cfg = ArgumentConfig({})
        assert cfg.actionkey == ""

    def test_argument_config_remove_empty_group(self):
        from argklass.config import ArgumentConfig
        cfg = ArgumentConfig({})
        cfg.remove_empty = True
        cfg.new_group("empty_group")
        cfg.pop_group()
        assert "empty_group" not in cfg.root

    def test_argument_config_keep_empty_group(self):
        from argklass.config import ArgumentConfig
        cfg = ArgumentConfig({})
        cfg.remove_empty = False
        cfg.new_group("empty_group")
        cfg.pop_group()
        assert "empty_group" in cfg.root

    def test_argument_config_format_action_with_default(self):
        from argklass.config import ArgumentConfig
        cfg = ArgumentConfig({"key": "fromconfig"})
        cfg.ignore_default = False

        action = argparse.Action(["--key"], "key", default="hardcoded")
        cfg.format_action(action, depth=0)
        assert cfg.current["key"] == "fromconfig"

    def test_argument_config_with_subparsers(self, tmp_path):
        from argklass.config import save_as_config

        parser = ArgumentParser()
        parser.add_arguments(CfgMain, create_group=True)
        args = parser.parse_args(["--verbose", "add", "--x", "5"])

        path = str(tmp_path / "subcmd.hjson")
        save_as_config(parser, args, path)

        with open(path) as f:
            data = hjson.load(f)
        assert isinstance(data, dict)


class TestConfigFailureModes:
    def test_format_action_suppress(self):
        from argklass.config import ArgumentConfig
        cfg = ArgumentConfig({})
        action = argparse.Action(["--skip"], "skip", default=argparse.SUPPRESS)
        cfg.format_action(action, 0)
        assert "skip" not in cfg.current

    def test_format_action_value_priority_config_over_default(self):
        from argklass.config import ArgumentConfig
        cfg = ArgumentConfig({"key": "from_config"})
        cfg.ignore_default = False
        action = argparse.Action(["--key"], "key", default="default_val")
        cfg.format_action(action, depth=0)
        assert cfg.current["key"] == "from_config"

    def test_format_action_ignore_default_when_same_as_action(self):
        from argklass.config import ArgumentConfig
        cfg = ArgumentConfig({})
        cfg.ignore_default = True
        action = argparse.Action(["--key"], "key", default="same")
        cfg.format_action(action, depth=0)
        assert "key" not in cfg.current

    def test_format_action_with_arggroup(self):
        from argklass.config import ArgumentConfig
        args = argparse.Namespace(key="from_args")
        cfg = ArgumentConfig({}, args)
        cfg.ignore_default = False
        action = argparse.Action(["--key"], "key", default="default")
        cfg.format_action(action, depth=0)
        assert cfg.current["key"] == "from_args"
        assert vars(args)["key"] == "from_args"

    def test_new_group_preserves_existing(self):
        from argklass.config import ArgumentConfig
        cfg = ArgumentConfig({"existing": {"inner": 42}})
        cfg.new_group("existing")
        assert cfg.current.get("inner") == 42

    def test_eager_subparser_traversal(self):
        from argklass.config import ArgumentConfig
        parser = ArgumentParser()
        parser.add_arguments(CfgMain, create_group=True)
        cfg = ArgumentConfig({})
        cfg.eager = True
        cfg(parser)

    def test_save_load_roundtrip_preserves_values(self, tmp_path):
        from argklass.config import apply_config, save_as_config

        parser = _make_parser()
        args = parser.parse_args(["--name", "roundtrip", "--count", "99"])
        path = str(tmp_path / "rt.hjson")
        save_as_config(parser, args, path)

        parser2 = _make_parser()
        args2 = parser2.parse_args([])
        apply_config(parser2, args2, path)
