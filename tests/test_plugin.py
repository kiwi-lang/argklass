"""Tests for argklass/plugin.py — discovery, registration, factories."""

import os
import types

import pytest


class TestCommandRegistry:
    def test_insert_list(self):
        from argklass.command import Command
        from argklass.plugin import CommandRegistry

        class CmdX(Command):
            name = "cmd_x"

            @staticmethod
            def execute(args):
                return 0

        class CmdY(Command):
            name = "cmd_y"

            @staticmethod
            def execute(args):
                return 0

        reg = CommandRegistry()
        reg.insert_commands([CmdX, CmdY])
        assert "cmd_x" in reg.found_commands
        assert "cmd_y" in reg.found_commands

    def test_duplicate_raises(self):
        from argklass.command import Command
        from argklass.plugin import CommandRegistry

        class CmdDup(Command):
            name = "dup_test"

            @staticmethod
            def execute(args):
                return 0

        reg = CommandRegistry()
        reg.insert_commands(CmdDup)
        with pytest.raises(AssertionError, match="Duplicate"):
            reg.insert_commands(CmdDup)

    def test_getstate_setstate(self):
        from argklass.command import Command
        from argklass.plugin import CommandRegistry

        class CmdS(Command):
            name = "cmd_s"

            @staticmethod
            def execute(args):
                return 0

        reg = CommandRegistry()
        reg.insert_commands(CmdS)
        state = reg.__getstate__()
        assert isinstance(state, list)

        reg2 = CommandRegistry()
        reg2.__setstate__(state)
        assert "cmd_s" in reg2.found_commands

    def test_fix_nondeterminism(self):
        from argklass.command import Command
        from argklass.plugin import CommandRegistry

        class CmdN(Command):
            name = "cmd_n"

            @staticmethod
            def execute(args):
                return 0

        reg = CommandRegistry()
        reg.insert_commands(CmdN)
        reg.fix_nondeterminism()
        assert "cmd_n" in reg.found_commands

    def test_whitespace_warning(self, capsys):
        from argklass.command import Command
        from argklass.plugin import CommandRegistry

        class BadName(Command):
            name = " trailing "

            @staticmethod
            def execute(args):
                return 0

        reg = CommandRegistry()
        reg.insert_commands(BadName)
        out = capsys.readouterr().out
        assert "white space" in out


class TestNormName:
    def test_with_list(self):
        from argklass.plugin import _norm_name
        assert _norm_name([1, 2, 3], "module.py") == [1, 2, 3]

    def test_sets_name(self):
        from argklass.plugin import _norm_name

        class NoName:
            pass

        _norm_name(NoName, "/path/to/mycommand.py")
        assert NoName.name == "mycommand"

    def test_preserves_existing(self):
        from argklass.plugin import _norm_name

        class HasName:
            name = "existing"

        _norm_name(HasName, "/path/to/other.py")
        assert HasName.name == "existing"


class TestCallRef:
    def test_call_ref(self):
        from argklass.plugin import CallRef

        def adder(a, b):
            return a + b

        ref = CallRef(adder)
        assert ref(3, 4) == 7


class TestDiscovery:
    def test_discover_plugins_simple(self):
        from argklass.plugin import discover_plugins_simple

        mod = types.ModuleType("test_empty_mod")
        mod.__path__ = []
        mod.__name__ = "test_empty_mod"

        result = discover_plugins_simple(mod)
        assert isinstance(result, dict)

    def test_discover_plugins_parallel(self):
        from argklass.plugin import discover_plugins_parallel

        mod = types.ModuleType("test_empty_mod_p")
        mod.__path__ = []
        mod.__name__ = "test_empty_mod_p"

        result = discover_plugins_parallel(mod)
        assert isinstance(result, dict)

    def test_fetch_factories_single(self):
        from argklass.plugin import CommandRegistry, fetch_factories_single

        registry = CommandRegistry()
        fetch_factories_single(registry, "argklass", __file__)

    def test_discover_from_plugins_commands(self):
        from argklass.plugin import CommandRegistry, discover_from_plugins_commands

        mod = types.ModuleType("test_no_plugins")
        mod.__path__ = []
        mod.__name__ = "test_no_plugins"

        registry = CommandRegistry()
        discover_from_plugins_commands(registry, mod)

    def test_discover_module_commands_no_cache(self):
        from argklass.plugin import CommandRegistry, discover_module_commands_no_cache

        mod = types.ModuleType("test_no_cmds")
        mod.__path__ = []
        mod.__name__ = "test_no_cmds"
        mod.__file__ = __file__

        result = discover_module_commands_no_cache(mod)
        assert isinstance(result, CommandRegistry)

    def test_discover_plugin_commands_no_cache_empty(self):
        from argklass.plugin import discover_plugin_commands_no_cache

        mod = types.ModuleType("test_no_plugins_cmds")
        mod.__path__ = []
        mod.__name__ = "test_no_plugins_cmds"

        result = discover_plugin_commands_no_cache(mod)
        assert result == []


class TestCacheLocation:
    def test_with_cache_location(self):
        from argklass.plugin import (
            discover_module_commands,
            discover_plugin_commands,
            with_cache_location,
        )

        old_module_call = discover_module_commands.call
        old_plugin_call = discover_plugin_commands.call

        with with_cache_location("test_loc"):
            assert discover_module_commands.call is not old_module_call
            assert discover_plugin_commands.call is not old_plugin_call

        assert discover_module_commands.call is old_module_call
        assert discover_plugin_commands.call is old_plugin_call


class TestResolveFactoryModule:
    def test_same_file(self):
        from argklass.plugin import _resolve_factory_module
        result = _resolve_factory_module("base.py", "some.module", "COMMANDS", "/path/to/base.py")
        assert result is None

    def test_import_error(self):
        from argklass.plugin import _resolve_factory_module
        result = _resolve_factory_module(
            "base.py", "nonexistent.module.path", "COMMANDS", "/path/to/other.py"
        )
        assert result is None

    def test_no_commands_attr(self):
        from argklass.plugin import _resolve_factory_module
        result = _resolve_factory_module(
            "base.py", "argklass", "NONEXISTENT_ATTR", os.path.abspath(__file__)
        )
        assert result is None
