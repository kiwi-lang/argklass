"""Tests for argklass/command.py — Command, ParentCommand, Registry."""

import argparse
import os

import pytest


class TestCommand:
    def test_command_help(self):
        from argklass.command import Command

        class MyCmd(Command):
            """My command help text"""
            name = "mycmd"

        assert MyCmd.help() == "My command help text"

    def test_command_no_doc(self):
        from argklass.command import Command

        class NoDoc(Command):
            name = "nodoc"

        NoDoc.__doc__ = None
        assert NoDoc.help() == ""

    def test_command_argument_class_missing(self):
        from argklass.command import Command

        class NoArgs(Command):
            name = "noargs"

        assert NoArgs.argument_class() is None

    def test_command_execute_raises(self):
        from argklass.command import Command

        class Unimplemented(Command):
            name = "unimpl"

        with pytest.raises(NotImplementedError):
            Unimplemented().execute(None)

    def test_command_call(self):
        from argklass.command import Command

        class Callable(Command):
            name = "callable_cmd"

            @staticmethod
            def execute(args):
                return 42

        assert Callable()(None) == 42

    def test_command_examples_empty(self):
        from argklass.command import Command

        class NoExamples(Command):
            name = "no_examples"

        assert NoExamples.examples() == []


class TestCommandDecorator:
    def test_command_decorator(self):
        from argklass.command import command, commands

        @command
        class Decorated:
            name = "decorated"

        registry = commands()
        found = [c for c in registry.commands if hasattr(c, "name") and c.name == "decorated"]
        assert len(found) > 0


class TestChdir:
    def test_chdir(self):
        from argklass.command import chdir

        original = os.getcwd()
        with chdir("/tmp"):
            assert os.getcwd() == "/tmp"
        assert os.getcwd() == original


class TestRegistry:
    def test_registry_depth(self):
        from argklass.command import _Registry2
        reg = _Registry2()
        assert reg.depth == 0

    def test_registry_clear(self):
        from argklass.command import _Registry2

        reg = _Registry2()

        class FakeCmd:
            name = "fake"
            dispatch = {"a": 1}

        reg.add_command(FakeCmd)
        reg.clear()

    def test_registry_subcmd_context(self):
        from argklass.command import _Registry2

        reg = _Registry2()
        assert reg.depth == 0

        class FakeCmd:
            name = "parent"

        with reg.subcmd(FakeCmd):
            assert reg.depth == 1
        assert reg.depth == 0

    def test_registry_clear_no_dispatch(self):
        from argklass.command import _Registry2

        reg = _Registry2()

        class NoDispatch:
            name = "nodispatch"

        reg.add_command(NoDispatch)
        reg.clear()


class TestParentCommandFailures:
    def test_execute_undefined_subcmd(self):
        from argklass.command import ParentCommand

        class TestParent(ParentCommand):
            name = "test_parent_fail"
            dispatch = {}

            @classmethod
            def module(cls):
                import types
                mod = types.ModuleType("test_parent_mod")
                mod.__name__ = "test_parent_mod"
                return mod

        ParentCommand.cmddepth[TestParent] = 0
        args = argparse.Namespace(cmd0="nonexistent")

        with pytest.raises(RuntimeError, match="not defined"):
            TestParent.execute(args)
