"""Tests for argklass.sysconfig."""

import asyncio
import contextvars
import json
import os
import threading
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import pytest

from argklass.sysconfig import (
    ConfigContext,
    _deep_merge,
    _default_ctx,
    _select,
    apply_config,
    as_environment_variable,
    clear_config,
    config_fields,
    config_template,
    configfield,
    defer,
    defer_section,
    env_template,
    field_type,
    from_dict,
    get_config,
    get_path,
    load_and_apply,
    load_config,
    option,
    overlay,
    overrides_snapshot,
    parse_overrides,
    parse_scalar,
    push_config,
    register_root,
    reset_config,
    resolve_options,
    save_config,
    section,
    set_config,
    set_env_prefix,
    set_option,
    set_path,
    show_config,
    to_dict,
    tracked_options,
    use_config,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_global_state():
    """Reset the default context before each test."""
    _default_ctx.prefix = ""
    _default_ctx.set_config(None)
    _default_ctx._tracked.clear()
    yield
    _default_ctx.prefix = ""
    _default_ctx.set_config(None)
    _default_ctx._tracked.clear()


# ---------------------------------------------------------------------------
# Sample dataclasses
# ---------------------------------------------------------------------------


@dataclass
class DBConfig:
    host: str = configfield("db.host", str, "localhost")
    port: int = configfield("db.port", int, 5432)
    name: str = configfield("db.name", str, "testdb")


@dataclass
class ServerConfig:
    debug: bool = configfield("server.debug", bool, False)
    workers: int = configfield("server.workers", int, 4)
    db: DBConfig = field(default_factory=DBConfig)


@dataclass
class Flat:
    alpha: int = configfield("alpha", int, 1)
    beta: str = configfield("beta", str, "hello")


# ---------------------------------------------------------------------------
# as_environment_variable
# ---------------------------------------------------------------------------


class TestEnvVarNaming:
    def test_simple(self):
        assert as_environment_variable("host", prefix="") == "HOST"

    def test_dotted(self):
        assert as_environment_variable("db.host", prefix="MYAPP_") == "MYAPP_DB_HOST"

    def test_prefix_via_global(self):
        set_env_prefix("APP")
        assert as_environment_variable("db.port") == "APP_DB_PORT"

    def test_prefix_trailing_underscore(self):
        set_env_prefix("APP_")
        assert as_environment_variable("x") == "APP_X"


# ---------------------------------------------------------------------------
# option
# ---------------------------------------------------------------------------


class TestOption:
    def test_returns_default(self):
        assert option("missing.key", int, 42) == 42

    def test_reads_from_global_config(self):
        set_config({"db": {"host": "remote-host"}})
        assert option("db.host", str, "localhost") == "remote-host"

    def test_env_overrides_config(self, monkeypatch):
        set_config({"db": {"port": "1111"}})
        monkeypatch.setenv("DB_PORT", "2222")
        assert option("db.port", int, 5432) == 2222

    def test_bool_coercion(self):
        set_config({"app": {"debug": "true"}})
        assert option("app.debug", bool, False) is True

    def test_none_default(self):
        assert option("nonexistent", str, None) is None


# ---------------------------------------------------------------------------
# configfield
# ---------------------------------------------------------------------------


class TestConfigfield:
    def test_defaults(self):
        cfg = Flat()
        assert cfg.alpha == 1
        assert cfg.beta == "hello"

    def test_picks_up_global(self):
        set_config({"alpha": 99})
        cfg = Flat()
        assert cfg.alpha == 99

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("BETA", "world")
        cfg = Flat()
        assert cfg.beta == "world"

    def test_nested(self):
        set_config({"db": {"host": "db-host", "port": "3306"}})
        cfg = ServerConfig()
        assert cfg.db.host == "db-host"
        assert cfg.db.port == 3306


# ---------------------------------------------------------------------------
# apply_config context manager
# ---------------------------------------------------------------------------


class TestApplyConfig:
    def test_temporary_override(self):
        set_config({"db": {"host": "original"}})

        with apply_config({"db": {"host": "override"}}):
            assert option("db.host", str) == "override"

        assert option("db.host", str) == "original"

    def test_merge(self):
        set_config({"db": {"host": "h1", "port": "5432"}})

        with apply_config({"db": {"host": "h2"}}):
            assert option("db.host", str) == "h2"
            assert option("db.port", int) == 5432

    def test_nesting(self):
        with apply_config({"x": 1}):
            with apply_config({"y": 2}):
                cfg = get_config()
                assert cfg["x"] == 1
                assert cfg["y"] == 2
            cfg = get_config()
            assert cfg["x"] == 1
            assert "y" not in cfg


# ---------------------------------------------------------------------------
# to_dict / from_dict
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_round_trip_flat(self):
        cfg = Flat()
        d = to_dict(cfg)
        assert d == {"alpha": 1, "beta": "hello"}

        restored = from_dict(Flat, d)
        assert restored.alpha == 1
        assert restored.beta == "hello"

    def test_round_trip_nested(self):
        cfg = ServerConfig()
        d = to_dict(cfg)

        assert d["debug"] is False
        assert d["workers"] == 4
        assert d["db"]["host"] == "localhost"

        restored = from_dict(ServerConfig, d)
        assert restored.db.host == "localhost"
        assert restored.db.port == 5432

    def test_skip_none(self):
        @dataclass
        class Opt:
            a: int = configfield("opt.a", int, None)
            b: str = configfield("opt.b", str, "yes")

        d = to_dict(Opt(), skip_none=True)
        assert "a" not in d
        assert d["b"] == "yes"

    def test_from_dict_extra_keys_ignored(self):
        d = {"alpha": 10, "beta": "hi", "extra": "ignored"}
        cfg = from_dict(Flat, d)
        assert cfg.alpha == 10
        assert cfg.beta == "hi"

    def test_from_dict_missing_keys_use_defaults(self):
        cfg = from_dict(Flat, {"alpha": 7})
        assert cfg.alpha == 7
        assert cfg.beta == "hello"

    def test_list_of_dataclasses(self):
        @dataclass
        class Item:
            name: str = "x"
            value: int = 0

        @dataclass
        class Container:
            items: list[Item] = field(default_factory=list)

        data = {"items": [{"name": "a", "value": 1}, {"name": "b", "value": 2}]}
        c = from_dict(Container, data)
        assert len(c.items) == 2
        assert c.items[0].name == "a"
        assert c.items[1].value == 2


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


class TestFileIO:
    def test_json_round_trip(self, tmp_path):
        cfg = ServerConfig()
        path = str(tmp_path / "config.json")

        save_config(cfg, path)
        loaded = load_config(ServerConfig, path)

        assert loaded.debug is False
        assert loaded.workers == 4
        assert loaded.db.host == "localhost"
        assert loaded.db.port == 5432

    def test_hjson_round_trip(self, tmp_path):
        cfg = Flat()
        path = str(tmp_path / "config.hjson")

        save_config(cfg, path)
        loaded = load_config(Flat, path)

        assert loaded.alpha == 1
        assert loaded.beta == "hello"

    def test_yaml_round_trip(self, tmp_path):
        yaml = pytest.importorskip("yaml")

        cfg = ServerConfig()
        path = str(tmp_path / "config.yaml")

        save_config(cfg, path)
        loaded = load_config(ServerConfig, path)

        assert loaded.db.name == "testdb"

    def test_skip_none_on_save(self, tmp_path):
        @dataclass
        class Partial:
            a: int = configfield("p.a", int, 10)
            b: str = configfield("p.b", str, None)

        path = str(tmp_path / "partial.json")
        save_config(Partial(), path, skip_none=True)

        with open(path) as f:
            data = json.load(f)

        assert "a" in data
        assert "b" not in data


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------


class TestIntrospection:
    def test_config_fields(self):
        entries = list(config_fields(ServerConfig))
        names = [e[0] for e in entries]

        assert "server.debug" in names
        assert "server.workers" in names
        assert "db.host" in names
        assert "db.port" in names
        assert "db.name" in names

    def test_config_fields_env_names(self):
        set_env_prefix("TEST")
        entries = list(config_fields(ServerConfig))
        env_names = [e[3] for e in entries]

        assert "TEST_SERVER_DEBUG" in env_names
        assert "TEST_DB_HOST" in env_names

    def test_env_template(self):
        set_env_prefix("APP")
        tmpl = env_template(Flat)
        assert "APP_ALPHA=1" in tmpl
        assert "APP_BETA=hello" in tmpl

    def test_env_template_uncommented(self):
        tmpl = env_template(Flat, commented=False)
        assert tmpl.startswith("ALPHA=1")

    def test_config_template(self):
        set_env_prefix("X")
        tmpl = config_template(ServerConfig)
        assert "host:" in tmpl
        assert "port:" in tmpl
        assert "env:" in tmpl

    def test_show_config_class(self, capsys):
        set_env_prefix("T")
        show_config(Flat)
        out = capsys.readouterr().out
        assert "alpha" in out
        assert "beta" in out

    def test_show_config_instance(self, capsys):
        show_config(Flat())
        out = capsys.readouterr().out
        assert "alpha" in out
        assert "1" in out

    def test_show_config_json(self, capsys):
        show_config(Flat, to_json=True)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "alpha" in data

    def test_tracked_options(self):
        _ = Flat()
        opts = tracked_options()
        assert "alpha" in opts
        assert opts["alpha"]["default"] == 1

    def test_overrides_snapshot_empty(self):
        _ = Flat()
        snap = overrides_snapshot()
        assert snap == {}

    def test_overrides_snapshot_with_override(self):
        set_config({"alpha": 99})
        _ = Flat()
        snap = overrides_snapshot()
        assert snap == {"alpha": 99}


# ---------------------------------------------------------------------------
# _deep_merge
# ---------------------------------------------------------------------------


class TestDeepMerge:
    def test_flat(self):
        base = {"a": 1, "b": 2}
        _deep_merge(base, {"b": 3, "c": 4})
        assert base == {"a": 1, "b": 3, "c": 4}

    def test_nested(self):
        base = {"x": {"a": 1, "b": 2}}
        _deep_merge(base, {"x": {"b": 3, "c": 4}})
        assert base == {"x": {"a": 1, "b": 3, "c": 4}}

    def test_overlay_replaces_non_dict(self):
        base = {"x": 1}
        _deep_merge(base, {"x": {"nested": True}})
        assert base == {"x": {"nested": True}}


# ---------------------------------------------------------------------------
# ConfigContext isolation
# ---------------------------------------------------------------------------


class TestConfigContext:
    def test_separate_contexts_are_isolated(self):
        ctx_a = ConfigContext(prefix="LIBA")
        ctx_b = ConfigContext(prefix="LIBB")

        ctx_a.set_config({"rate": 10})
        ctx_b.set_config({"rate": 20})

        assert ctx_a.option("rate", int, 0) == 10
        assert ctx_b.option("rate", int, 0) == 20

    def test_prefix_isolation(self):
        ctx_a = ConfigContext(prefix="A")
        ctx_b = ConfigContext(prefix="B")

        assert ctx_a.as_environment_variable("x.y") == "A_X_Y"
        assert ctx_b.as_environment_variable("x.y") == "B_X_Y"

    def test_tracked_options_isolation(self):
        ctx_a = ConfigContext(prefix="A")
        ctx_b = ConfigContext(prefix="B")

        ctx_a.option("shared_name", int, 1)
        ctx_b.option("shared_name", int, 2)

        assert ctx_a.tracked_options()["shared_name"]["default"] == 1
        assert ctx_b.tracked_options()["shared_name"]["default"] == 2

    def test_configfield_bound_to_context(self):
        ctx = ConfigContext(prefix="CTX")
        ctx.set_config({"val": 42})

        @dataclass
        class Cfg:
            val: int = ctx.configfield("val", int, 0)

        assert Cfg().val == 42
        assert set_config is not ctx.set_config

    def test_apply_config_scoped_to_context(self):
        ctx = ConfigContext(prefix="X")
        ctx.set_config({"a": 1})

        with ctx.apply_config({"a": 2, "b": 3}):
            assert ctx.option("a", int) == 2
            assert ctx.option("b", int) == 3

        assert ctx.option("a", int) == 1
        with pytest.raises(KeyError):
            ctx.option("b", int)

    def test_context_does_not_affect_default(self):
        ctx = ConfigContext(prefix="OTHER")
        ctx.set_config({"k": 99})

        set_config({"k": 1})

        assert ctx.option("k", int) == 99
        assert option("k", int) == 1

    def test_env_template_uses_context_prefix(self):
        ctx = ConfigContext(prefix="LIB")

        @dataclass
        class Cfg:
            speed: float = ctx.configfield("speed", float, 1.0)

        tmpl = ctx.env_template(Cfg, commented=False)
        assert "LIB_SPEED=1.0" in tmpl

    def test_multi_library_simulation(self, monkeypatch):
        """Two libraries with different prefixes, env vars, and configs."""

        lib_a = ConfigContext(prefix="LIBA")
        lib_b = ConfigContext(prefix="LIBB")

        @dataclass
        class CfgA:
            timeout: int = lib_a.configfield("timeout", int, 30)

        @dataclass
        class CfgB:
            timeout: int = lib_b.configfield("timeout", int, 60)

        lib_a.set_config({"timeout": 5})
        monkeypatch.setenv("LIBB_TIMEOUT", "99")

        a = CfgA()
        b = CfgB()

        assert a.timeout == 5
        assert b.timeout == 99


# ===========================================================================
# Edge cases
# ===========================================================================


class TestSelectEdgeCases:
    """_select must pick the first non-None value, even if it is falsy."""

    def test_all_none(self):
        assert _select(None, None, None) is None

    def test_zero_is_valid(self):
        assert _select(0, 42) == 0

    def test_false_is_valid(self):
        assert _select(False, True) is False

    def test_empty_string_is_valid(self):
        assert _select("", "fallback") == ""

    def test_first_non_none_wins(self):
        assert _select(None, 0, 5) == 0

    def test_single_none(self):
        assert _select(None) is None

    def test_single_value(self):
        assert _select(7) == 7


class TestOptionEdgeCases:
    def test_falsy_zero_default(self):
        """default=0 should not be swallowed in favour of None."""
        assert option("zz.missing", int, 0) == 0

    def test_falsy_false_default(self):
        assert option("zz.flag", bool, False) is False

    def test_empty_string_default(self):
        assert option("zz.empty", str, "") == ""

    def test_deeply_nested_path(self):
        set_config({"a": {"b": {"c": {"d": 99}}}})
        assert option("a.b.c.d", int, 0) == 99

    def test_partially_missing_nested_path(self):
        set_config({"a": {"b": 10}})
        assert option("a.b.c.d", int, -1) == -1

    def test_config_has_non_dict_at_intermediate_level(self):
        set_config({"a": "scalar"})
        assert option("a.b", str, "fallback") == "fallback"

    def test_invalid_type_coercion_raises(self):
        """A value that cannot be read as the declared type is an error,
        not a silent None -- it is a mistake in the config file."""
        set_config({"val": "not_a_number"})
        with pytest.raises(ValueError, match="cannot read 'not_a_number' as int"):
            option("val", int, None)

    def test_invalid_env_coercion_raises(self, monkeypatch):
        """Same for a malformed environment variable: report it rather than
        falling back to the default and hiding the typo."""
        monkeypatch.setenv("BAD_INT", "xyz")
        with pytest.raises(ValueError, match="BAD_INT"):
            option("bad.int", int, 42)

    def test_bool_string_variants(self):
        for truthy in ("1", "true", "True", "TRUE", "yes", "YES", "on", "ON"):
            set_config({"b": truthy})
            assert option("b", bool, False) is True, f"Failed for {truthy!r}"

        for falsy in ("0", "false", "False", "no", "off", "other"):
            set_config({"b": falsy})
            assert option("b", bool, True) is False, f"Failed for {falsy!r}"

    def test_bool_from_int_in_config(self):
        set_config({"flag": 1})
        assert option("flag", bool, False) is True

        set_config({"flag": 0})
        assert option("flag", bool, True) is False

    def test_single_segment_name(self):
        set_config({"simple": "val"})
        assert option("simple", str) == "val"

    def test_env_takes_precedence_over_both(self, monkeypatch):
        set_config({"k": "from_config"})
        monkeypatch.setenv("K", "from_env")
        assert option("k", str, "default") == "from_env"

    def test_config_takes_precedence_over_default(self):
        set_config({"k": "from_config"})
        assert option("k", str, "default") == "from_config"

    def test_no_config_set(self):
        """option works with no config set at all (empty default context)."""
        assert option("anything", int, 7) == 7


class TestConfigfieldEdgeCases:
    def test_default_zero(self):
        @dataclass
        class C:
            count: int = configfield("count", int, 0)

        assert C().count == 0

    def test_default_false(self):
        @dataclass
        class C:
            flag: bool = configfield("flag", bool, False)

        assert C().flag is False

    def test_default_empty_string(self):
        @dataclass
        class C:
            label: str = configfield("label", str, "")

        assert C().label == ""

    def test_default_none(self):
        @dataclass
        class C:
            opt: str = configfield("opt_val", str, None)

        assert C().opt is None

    def test_constructor_overrides_configfield(self):
        """Explicitly passed kwargs must beat the default_factory."""
        cfg = Flat(alpha=999, beta="override")
        assert cfg.alpha == 999
        assert cfg.beta == "override"


class TestApplyConfigEdgeCases:
    def test_empty_overrides(self):
        set_config({"x": 1})
        with apply_config({}):
            assert get_config() == {"x": 1}

    def test_from_empty_base(self):
        with apply_config({"y": 2}):
            assert get_config() == {"y": 2}
        assert get_config() == {}

    def test_restores_on_exception(self):
        set_config({"a": 1})
        with pytest.raises(RuntimeError):
            with apply_config({"a": 2}):
                assert option("a", int) == 2
                raise RuntimeError("boom")
        assert option("a", int) == 1

    def test_deeply_nested_merge(self):
        set_config({"a": {"b": {"c": 1, "d": 2}}})
        with apply_config({"a": {"b": {"c": 99}}}):
            cfg = get_config()
            assert cfg["a"]["b"]["c"] == 99
            assert cfg["a"]["b"]["d"] == 2


# ===========================================================================
# Failure modes — File I/O
# ===========================================================================


class TestFileIOFailures:
    def test_load_nonexistent_file(self, tmp_path):
        missing = str(tmp_path / "nope.json")
        with pytest.raises(FileNotFoundError):
            load_config(Flat, missing)

    def test_load_invalid_json(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{invalid json!!")
        with pytest.raises(json.JSONDecodeError):
            load_config(Flat, str(bad))

    def test_load_unknown_format(self, tmp_path):
        f = tmp_path / "config.xyz"
        f.write_text("hello")
        with pytest.raises(ValueError, match="Unknown config format"):
            load_config(Flat, str(f), fmt="toml")

    def test_save_unknown_format(self, tmp_path):
        f = str(tmp_path / "out.xyz")
        with pytest.raises(ValueError, match="Unknown config format"):
            save_config(Flat(), f, fmt="toml")

    def test_load_empty_json(self, tmp_path):
        """An empty JSON object should produce a dataclass with defaults."""
        f = tmp_path / "empty.json"
        f.write_text("{}")
        cfg = load_config(Flat, str(f))
        assert cfg.alpha == 1
        assert cfg.beta == "hello"

    def test_load_and_apply(self, tmp_path):
        f = tmp_path / "la.json"
        f.write_text('{"alpha": 77}')
        data = load_and_apply(str(f))
        assert data == {"alpha": 77}
        assert get_config() == {"alpha": 77}
        assert option("alpha", int, 0) == 77


# ===========================================================================
# Failure modes — Serialization
# ===========================================================================


class TestSerializationEdgeCases:
    def test_to_dict_non_dataclass(self):
        assert to_dict("just a string") == "just a string"
        assert to_dict(42) == 42

    def test_to_dict_class_not_instance(self):
        result = to_dict(Flat)
        assert result is Flat

    def test_from_dict_non_dataclass(self):
        assert from_dict(str, "hello") == "hello"

    def test_from_dict_non_dict_data(self):
        assert from_dict(Flat, "not a dict") == "not a dict"

    def test_from_dict_empty_dict(self):
        cfg = from_dict(Flat, {})
        assert cfg.alpha == 1
        assert cfg.beta == "hello"

    def test_from_dict_coerces_to_the_declared_type(self):
        """A YAML/JSON scalar is coerced to the field's annotation."""
        cfg = from_dict(Flat, {"alpha": "123"})
        assert cfg.alpha == 123

    def test_from_dict_nested_non_dict_for_dataclass_field(self):
        """A dataclass field needs a mapping; anything else names the field."""
        data = {"debug": False, "workers": 2, "db": "not_a_dict"}
        with pytest.raises(TypeError, match="db: expected a mapping for DBConfig"):
            from_dict(ServerConfig, data)

    def test_round_trip_preserves_dict_field(self):
        @dataclass
        class HasDict:
            meta: dict = field(default_factory=lambda: {"key": "val"})

        original = HasDict()
        d = to_dict(original)
        assert d == {"meta": {"key": "val"}}

        restored = from_dict(HasDict, d)
        assert restored.meta == {"key": "val"}

    def test_round_trip_nested_list(self):
        @dataclass
        class Inner:
            x: int = 0

        @dataclass
        class Outer:
            items: list = field(default_factory=list)

        original = Outer(items=[Inner(1), Inner(2)])
        d = to_dict(original)
        assert d == {"items": [{"x": 1}, {"x": 2}]}


# ===========================================================================
# Edge cases — Introspection
# ===========================================================================


class TestIntrospectionEdgeCases:
    def test_config_fields_no_configfields(self):
        @dataclass
        class Plain:
            x: int = 0
            y: str = "hi"

        assert list(config_fields(Plain)) == []

    def test_config_fields_mixed(self):
        @dataclass
        class Mixed:
            a: int = configfield("m.a", int, 1)
            b: str = "plain"

        entries = list(config_fields(Mixed))
        assert len(entries) == 1
        assert entries[0][0] == "m.a"

    def test_env_template_none_default(self):
        @dataclass
        class C:
            val: str = configfield("val", str, None)

        tmpl = env_template(C, commented=False)
        assert "VAL=" in tmpl
        assert "VAL=None" not in tmpl

    def test_env_template_bool_default(self):
        @dataclass
        class C:
            flag: bool = configfield("flag", bool, True)

        tmpl = env_template(C, commented=False)
        assert "FLAG=true" in tmpl

    def test_config_template_none_default(self):
        @dataclass
        class C:
            opt: str = configfield("opt", str, None)

        tmpl = config_template(C)
        assert "opt:" in tmpl

    def test_show_config_with_override(self, capsys):
        set_config({"alpha": 42})
        instance = Flat()
        show_config(instance)
        out = capsys.readouterr().out
        assert "42" in out
        assert "default=" in out

    def test_tracked_options_returns_copy(self):
        _ = Flat()
        opts1 = tracked_options()
        opts1["alpha"]["value"] = 999
        opts2 = tracked_options()
        assert opts2["alpha"]["value"] != 999


# ===========================================================================
# Edge cases — _deep_merge
# ===========================================================================


class TestDeepMergeEdgeCases:
    def test_both_empty(self):
        base = {}
        _deep_merge(base, {})
        assert base == {}

    def test_overlay_empty(self):
        base = {"a": 1}
        _deep_merge(base, {})
        assert base == {"a": 1}

    def test_base_empty(self):
        base = {}
        _deep_merge(base, {"a": 1})
        assert base == {"a": 1}

    def test_deeply_nested(self):
        base = {"a": {"b": {"c": {"d": 1}}}}
        _deep_merge(base, {"a": {"b": {"c": {"e": 2}}}})
        assert base == {"a": {"b": {"c": {"d": 1, "e": 2}}}}

    def test_overlay_does_not_mutate_source(self):
        overlay = {"a": {"b": 1}}
        base = {}
        _deep_merge(base, overlay)
        overlay["a"]["b"] = 999
        assert base["a"]["b"] == 1

    def test_dict_replaces_scalar(self):
        base = {"a": 42}
        _deep_merge(base, {"a": {"nested": True}})
        assert base == {"a": {"nested": True}}

    def test_scalar_replaces_dict(self):
        base = {"a": {"nested": True}}
        _deep_merge(base, {"a": 42})
        assert base == {"a": 42}


# ===========================================================================
# Edge cases — Environment variable prefix
# ===========================================================================


class TestPrefixEdgeCases:
    def test_empty_prefix(self):
        set_env_prefix("")
        assert as_environment_variable("db.host") == "DB_HOST"

    def test_multiple_trailing_underscores(self):
        set_env_prefix("APP___")
        assert as_environment_variable("x") == "APP_X"

    def test_prefix_change_mid_run(self, monkeypatch):
        monkeypatch.setenv("A_VAL", "from_a")
        monkeypatch.setenv("B_VAL", "from_b")

        set_env_prefix("A")
        assert option("val", str) == "from_a"

        set_env_prefix("B")
        assert option("val", str) == "from_b"

    def test_context_prefix_set_after_construction(self):
        ctx = ConfigContext()
        assert ctx.as_environment_variable("x") == "X"
        ctx.prefix = "NEW"
        assert ctx.as_environment_variable("x") == "NEW_X"


# ===========================================================================
# Edge cases — ConfigContext
# ===========================================================================


class TestConfigContextEdgeCases:
    def test_set_config_none_resets(self):
        ctx = ConfigContext()
        ctx.set_config({"a": 1})
        assert ctx.option("a", int) == 1
        ctx.set_config(None)
        with pytest.raises(KeyError):
            ctx.option("a", int)

    def test_apply_config_exception_safety(self):
        ctx = ConfigContext()
        ctx.set_config({"x": 1})
        with pytest.raises(ValueError):
            with ctx.apply_config({"x": 2}):
                raise ValueError("oops")
        assert ctx.option("x", int) == 1

    def test_overrides_snapshot_after_reset(self):
        ctx = ConfigContext()
        ctx.set_config({"k": 5})
        ctx.option("k", int, 0)
        assert ctx.overrides_snapshot() == {"k": 5}
        ctx._tracked.clear()
        assert ctx.overrides_snapshot() == {}

    def test_load_save_via_context(self, tmp_path):
        ctx = ConfigContext(prefix="CTX")
        path = str(tmp_path / "ctx.json")

        ctx.save_config(Flat(), path)
        loaded = ctx.load_config(Flat, path)
        assert loaded.alpha == 1

    def test_load_and_apply_via_context(self, tmp_path):
        ctx = ConfigContext()
        path = tmp_path / "ctx2.json"
        path.write_text('{"key": "value"}')
        data = ctx.load_and_apply(str(path))
        assert data == {"key": "value"}
        assert ctx.get_config() == {"key": "value"}


# ===========================================================================
# Coverage gap tests
# ===========================================================================


class TestCoverageGaps:
    """Tests targeting specific uncovered lines/branches."""

    def test_get_env_prefix(self):
        """Covers get_env_prefix() module-level function (line 351)."""
        from argklass.sysconfig import get_env_prefix

        set_env_prefix("COV")
        assert get_env_prefix() == "COV_"

        set_env_prefix("")
        assert get_env_prefix() == ""

    def test_context_prefix_getter(self):
        """Covers ConfigContext.prefix getter property (line 107)."""
        ctx = ConfigContext(prefix="GETTER")
        assert ctx.prefix == "GETTER_"

    def test_getenv_bool_branch(self, monkeypatch):
        """Covers _getenv bool coercion path (line 455)."""
        from argklass.sysconfig import _getenv

        monkeypatch.setenv("BOOL_TEST", "true")
        assert _getenv("BOOL_TEST", bool) is True

        monkeypatch.setenv("BOOL_TEST", "0")
        assert _getenv("BOOL_TEST", bool) is False

    def test_show_config_nested_instance_with_missing_value(self, capsys):
        """Covers the break in show_config nested lookup (line 263)
        and the nested group header in _compact_print (lines 622-623).

        Uses a nested config where the dotted path has >1 segment so
        _compact_print recurses into a group header.
        """
        show_config(ServerConfig())
        out = capsys.readouterr().out
        assert "db" in out
        assert "host" in out
        assert "localhost" in out

    def test_show_config_nested_instance_value_becomes_none(self, capsys):
        """Covers the break when a nested lookup hits None mid-path (line 263)."""

        @dataclass
        class Inner:
            x: int = configfield("g.x", int, 10)

        @dataclass
        class Outer:
            inner: Inner = field(default_factory=Inner)

        instance = Outer()
        instance.inner = None  # force the nested lookup to fail
        show_config(instance)
        out = capsys.readouterr().out
        assert "x" in out

    def test_show_config_nested_dotted_name_grouping(self, capsys):
        """Covers the dct.setdefault path for dotted names (line 270)."""
        set_env_prefix("SC")
        show_config(ServerConfig)
        out = capsys.readouterr().out
        assert "server" in out or "db" in out

    def test_from_dict_list_of_plain_values(self):
        """Covers from_dict list branch where items are NOT dataclasses (line 537).

        Uses a typed list[int] so the list branch is entered but _list_item_type
        returns int (not a dataclass), falling through to the else.
        """

        @dataclass
        class WithList:
            nums: list[int] = field(default_factory=list)

        data = {"nums": [1, 2, 3]}
        cfg = from_dict(WithList, data)
        assert cfg.nums == [1, 2, 3]

    def test_resolve_field_types_fallback(self):
        """Covers _resolve_field_types except branch (lines 548-549).

        When get_type_hints raises (e.g. unresolvable forward ref),
        it should fall back to reading field.type directly.
        """
        from argklass.sysconfig import _resolve_field_types

        @dataclass
        class Broken:
            x: "CompletelyBogusType" = 0  # noqa: F821

        result = _resolve_field_types(Broken)
        assert isinstance(result, dict)
        assert "x" in result

    def test_list_item_type_non_list(self):
        """Covers _list_item_type returning None for non-list types (line 557)."""
        from argklass.sysconfig import _list_item_type

        assert _list_item_type(int) is None
        assert _list_item_type(str) is None
        assert _list_item_type(dict) is None

    def test_list_item_type_bare_list(self):
        """Covers _list_item_type when list has no __args__."""
        from argklass.sysconfig import _list_item_type

        assert _list_item_type(list) is None

    def test_compact_print_nested_groups(self, capsys):
        """Covers _compact_print nested group recursion (lines 622-623)."""
        from argklass.sysconfig import _compact_print

        nested = {
            "server": {
                "host": {
                    "type": "str",
                    "default": "localhost",
                    "env_name": "SERVER_HOST",
                    "value": "localhost",
                },
            },
        }
        _compact_print(nested, depth=0)
        out = capsys.readouterr().out
        assert "server:" in out
        assert "host" in out


# ===========================================================================
# Instance-first configuration
#
# The active config can be a dataclass instance, not just a dict: option()
# then reads the live object, loading coerces to the declared types, and an
# unknown key in a file is an error.
# ===========================================================================


class Backend(str, Enum):
    PYTORCH3D = "pytorch3d"
    GODOT = "godot"


@dataclass
class OptimConfig:
    lr: float = 3e-4
    momentum: float = 0.9


@dataclass
class LoggingConfig:
    output_dir: Path = Path("runs")
    metrics_db: Optional[Path] = None
    tags: tuple = ()


@dataclass
class DemoConfig:
    device: str = "cuda"
    steps: int = 1000
    amp: bool = True
    backend: Backend = Backend.PYTORCH3D
    # String annotations, as PEP 563 produces for every annotation in a module
    # using `from __future__ import annotations`.
    optim: "OptimConfig" = field(default_factory=OptimConfig)
    logging: "LoggingConfig" = field(default_factory=LoggingConfig)


def write_yaml(tmp_path, text, name="demo.yaml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


@pytest.fixture
def demo(tmp_path):
    """A loaded-and-activated DemoConfig, cleaned up afterwards."""
    cfg = load_and_apply(write_yaml(tmp_path, "steps: 50\n"), cls=DemoConfig)
    yield cfg
    clear_config()


class TestTypeCoercion:
    def test_load_hydrates_nested_dataclasses_and_types(self, tmp_path):
        path = write_yaml(
            tmp_path,
            """
            device: cpu
            steps: 50
            backend: godot
            optim:
              lr: 1e-3
            logging:
              output_dir: out/run1
              tags: [a, b]
            """,
        )
        cfg = load_config(DemoConfig, path)

        assert cfg.device == "cpu"
        assert cfg.steps == 50
        assert cfg.backend is Backend.GODOT
        assert isinstance(cfg.optim, OptimConfig)
        assert cfg.optim.lr == pytest.approx(1e-3)
        assert cfg.optim.momentum == 0.9  # untouched default
        assert cfg.logging.output_dir == Path("out/run1")
        assert cfg.logging.tags == ("a", "b")
        assert cfg.logging.metrics_db is None

    def test_optional_field_takes_a_value(self, tmp_path):
        cfg = load_config(
            DemoConfig, write_yaml(tmp_path, "logging:\n  metrics_db: m.db\n")
        )
        assert cfg.logging.metrics_db == Path("m.db")

    def test_bool_from_string(self, tmp_path):
        assert (
            load_config(DemoConfig, write_yaml(tmp_path, 'amp: "false"\n')).amp is False
        )

    def test_field_type_walks_dotted_names(self):
        assert field_type(DemoConfig, "optim.lr") is float
        assert field_type(DemoConfig, "steps") is int
        assert field_type(DemoConfig, "optim.nope") is None
        assert field_type(DemoConfig, "device.nope") is None


class TestStrictLoading:
    def test_unknown_key_names_the_file(self, tmp_path):
        path = write_yaml(tmp_path, "stpes: 10\n")
        with pytest.raises(ValueError) as err:
            load_config(DemoConfig, path)
        assert "stpes" in str(err.value)
        assert path in str(err.value)

    def test_unknown_nested_key_names_its_path(self, tmp_path):
        path = write_yaml(tmp_path, "optim:\n  learning_rate: 0.1\n")
        with pytest.raises(ValueError) as err:
            load_config(DemoConfig, path)
        assert "optim" in str(err.value)
        assert "learning_rate" in str(err.value)

    def test_strict_false_ignores_unknown_keys(self, tmp_path):
        path = write_yaml(tmp_path, "nope: 1\nsteps: 4\n")
        assert load_config(DemoConfig, path, strict=False).steps == 4

    def test_from_dict_is_lenient_by_default(self):
        """The raw dict primitive stays permissive; files are what get checked."""
        assert from_dict(DemoConfig, {"nope": 1, "steps": 4}).steps == 4


class TestInstanceConfig:
    def test_option_reads_the_live_instance(self, demo):
        assert option("steps") == 50
        assert option("optim.lr") == pytest.approx(3e-4)
        assert option("optim") is demo.optim

    def test_option_sees_mutation_after_load(self, demo):
        demo.steps = 999
        assert option("steps") == 999

    def test_option_default_only_for_missing_keys(self, demo):
        assert option("steps", 111) == 50
        assert option("optim.nesterov", False) is False
        assert option("optim.nesterov", None) is None

    def test_option_without_default_raises_on_typo(self, demo):
        with pytest.raises(KeyError):
            option("optim.lrr")

    def test_option_infers_the_type_from_annotations(self, demo, monkeypatch):
        monkeypatch.setenv("STEPS", "77")
        assert option("steps") == 77  # int, not "77"

    def test_register_root_types_env_vars_without_an_instance(self, monkeypatch):
        register_root(DemoConfig)
        monkeypatch.setenv("STEPS", "5")
        assert option("steps") == 5
        _default_ctx._root_type = None

    def test_set_option_writes_through_and_coerces(self, demo):
        set_option("logging.output_dir", "runs/xyz")
        assert demo.logging.output_dir == Path("runs/xyz")
        set_option("steps", "12")
        assert demo.steps == 12

    def test_set_option_rejects_unknown_key(self, demo):
        with pytest.raises(KeyError):
            set_option("optim.lrr", 1.0)

    def test_load_and_apply_without_cls_still_returns_a_dict(self, tmp_path):
        data = load_and_apply(write_yaml(tmp_path, "steps: 3\n"))
        assert data == {"steps": 3}
        assert get_config() == {"steps": 3}
        clear_config()

    def test_overrides_applied_after_the_file(self, tmp_path):
        path = write_yaml(tmp_path, "steps: 10\noptim:\n  lr: 0.1\n")
        cfg = load_config(DemoConfig, path, overrides=["optim.lr=1e-5", "steps=99"])
        assert cfg.optim.lr == pytest.approx(1e-5)
        assert cfg.steps == 99


class TestDottedAccess:
    def test_get_and_set_path(self):
        cfg = DemoConfig()
        assert get_path(cfg, "optim.momentum") == 0.9
        set_path(cfg, "optim.momentum", "0.5")
        assert cfg.optim.momentum == 0.5
        with pytest.raises(KeyError):
            get_path(cfg, "optim.missing")

    def test_get_path_works_on_dicts_too(self):
        assert get_path({"a": {"b": 1}}, "a.b") == 1

    def test_parse_scalar_reads_yaml_1_1_floats(self):
        assert parse_scalar("1e-3") == pytest.approx(1e-3)
        assert parse_scalar("false") is False
        assert parse_scalar("godot") == "godot"

    def test_parse_overrides(self):
        parsed = parse_overrides(["optim.lr=1e-3", "amp=false", "taus=[1, 7.5]"])
        assert parsed == {"optim.lr": 1e-3, "amp": False, "taus": [1, 7.5]}
        with pytest.raises(ValueError):
            parse_overrides(["no-equals-sign"])


class TestScoping:
    def test_overlay_is_scoped_and_leaves_the_original_alone(self, demo):
        with overlay({"optim.lr": 0.9}, steps=3) as scoped:
            assert option("optim.lr") == 0.9
            assert option("steps") == 3
            assert scoped is not demo
        assert option("optim.lr") == pytest.approx(3e-4)
        assert get_config() is demo

    def test_apply_config_works_on_an_instance(self, demo):
        with apply_config({"optim": {"lr": 0.5}}):
            assert option("optim.lr") == 0.5
        assert option("optim.lr") == pytest.approx(3e-4)
        assert demo.optim.lr == pytest.approx(3e-4)

    def test_use_config_restores_the_previous_config(self, demo):
        with use_config(DemoConfig(steps=2)):
            assert option("steps") == 2
        assert get_config() is demo

    def test_push_config_shadows_the_process_wide_one(self, demo):
        token = push_config(DemoConfig(steps=2))
        assert option("steps") == 2
        reset_config(token)
        assert get_config() is demo

    def test_push_none_masks_the_process_wide_config(self, demo):
        token = push_config(None)
        assert get_config() == {}
        reset_config(token)
        assert option("steps") == 50

    def test_set_config_returns_the_previous_config(self):
        first = DemoConfig(steps=1)
        set_config(first)
        assert set_config(DemoConfig(steps=2)) is first
        clear_config()

    def test_root_type_follows_a_context_local_config(self, demo):
        @dataclass
        class Other:
            steps: str = "text"

        assert _default_ctx.root_type is DemoConfig
        with use_config(Other()):
            assert _default_ctx.root_type is Other
        assert _default_ctx.root_type is DemoConfig


class TestThreadAndTaskSafety:
    def test_set_config_reaches_worker_threads(self, demo):
        """A bare ContextVar would fail here: threads start with an empty context."""
        seen = []
        thread = threading.Thread(target=lambda: seen.append(option("steps")))
        thread.start()
        thread.join()
        assert seen == [50]

    def test_scoped_override_does_not_leak_to_another_thread(self, demo):
        seen = []
        with overlay(steps=99):
            assert option("steps") == 99
            thread = threading.Thread(target=lambda: seen.append(option("steps")))
            thread.start()
            thread.join()
        assert seen == [50]  # the thread sees the process-wide config

    def test_scoped_override_can_be_carried_into_a_thread(self, demo):
        seen = []
        with overlay(steps=99):
            ctx = contextvars.copy_context()
            thread = threading.Thread(
                target=lambda: seen.append(ctx.run(option, "steps"))
            )
            thread.start()
            thread.join()
        assert seen == [99]

    def test_concurrent_tasks_keep_their_own_overlay(self, demo):
        async def worker(value):
            with overlay(steps=value):
                await asyncio.sleep(0)  # hand control to the other task mid-scope
                return option("steps")

        async def main():
            return await asyncio.gather(worker(1), worker(2))

        assert asyncio.run(main()) == [1, 2]
        assert option("steps") == 50

    def test_concurrent_tasks_keep_their_own_apply_config(self):
        set_config({"steps": 0})

        async def worker(value):
            with apply_config({"steps": value}):
                await asyncio.sleep(0)
                return option("steps", int)

        async def main():
            return await asyncio.gather(worker(1), worker(2))

        assert asyncio.run(main()) == [1, 2]
        clear_config()


class TestSerializationRoundTrip:
    def test_to_dict_is_yaml_friendly(self):
        cfg = DemoConfig(backend=Backend.GODOT, logging=LoggingConfig(tags=("a",)))
        data = to_dict(cfg)
        assert data["backend"] == "godot"
        assert data["logging"]["output_dir"] == "runs"
        assert data["logging"]["tags"] == ["a"]

    def test_save_then_load_round_trips(self, tmp_path):
        import yaml

        cfg = DemoConfig(steps=11, backend=Backend.GODOT)
        cfg.logging.metrics_db = Path("m.db")
        path = str(tmp_path / "cfg.yaml")
        save_config(cfg, path)

        # Plain safe_load must read it back: no python/object tags.
        with open(path) as fh:
            raw = yaml.safe_load(fh)
        assert raw["logging"]["output_dir"] == "runs"

        assert load_config(DemoConfig, path) == cfg


@dataclass
class AnnotatedInner:
    rate: float = configfield("inner.rate", float, 1.0)


@dataclass
class AnnotatedOuter:
    flag: bool = configfield("outer.flag", bool, False)
    inner: "AnnotatedInner" = field(default_factory=AnnotatedInner)


class TestConfigFieldIntrospection:
    def test_nested_dataclass_under_string_annotations(self):
        """PEP 563 turns every annotation into a string; nested sections must
        still be discovered."""
        names = [name for name, _t, _d, _e in config_fields(AnnotatedOuter)]
        assert names == ["outer.flag", "inner.rate"]

    def test_configfield_without_an_explicit_type(self):
        @dataclass
        class Sampler:
            batch: int = configfield("batch_size", 8)

        set_config({"batch_size": 64})
        assert Sampler().batch == 64
        assert Sampler(batch=2).batch == 2
        clear_config()

    def test_configfield_type_and_default_cannot_both_be_positional(self):
        with pytest.raises(TypeError):
            configfield("x", 1, 2)


class TestInstanceValuesAreNotRecoerced:
    """A dict config holds raw file values, so option() coerces them. A
    dataclass config was already coerced when it was built -- and its
    __post_init__ may deliberately hold a different runtime type than the
    annotation says, so option() must hand back what the instance holds."""

    def test_dict_values_are_coerced(self):
        set_config({"db": {"port": "1111"}})
        assert option("db.port", int) == 1111
        clear_config()

    def test_instance_values_are_returned_as_held(self):
        @dataclass
        class Holder:
            # __post_init__ stores a Path even though the annotation says str,
            # the way a config that normalises its own paths would.
            out: str = "runs"

            def __post_init__(self):
                self.out = Path(self.out)

        cfg = Holder()
        set_config(cfg)
        assert option("out") is cfg.out
        assert isinstance(option("out"), Path)
        clear_config()


# ===========================================================================
# section() -- a whole sub-config at once
# ===========================================================================


@dataclass
class InnerSection:
    depth: int = 1


@dataclass
class LoaderSection:
    batch_size: int = 4
    workers: int = 0
    shuffle: bool = True
    inner: "InnerSection" = field(default_factory=InnerSection)


@dataclass
class RootSection:
    name: str = "run"
    data: "LoaderSection" = field(default_factory=LoaderSection)


class TestSection:
    def test_returns_the_live_instance(self):
        cfg = RootSection()
        set_config(cfg)
        assert section("data") is cfg.data
        assert section("data", LoaderSection) is cfg.data
        clear_config()

    def test_hydrates_a_dict_subtree(self):
        set_config({"data": {"batch_size": "16", "shuffle": "false"}})
        loaded = section("data", LoaderSection)
        assert loaded == LoaderSection(batch_size=16, shuffle=False)
        clear_config()

    def test_dict_subtree_needs_a_class(self):
        set_config({"data": {"batch_size": 16}})
        with pytest.raises(TypeError, match="pass the dataclass"):
            section("data")
        clear_config()

    def test_dict_subtree_is_strict(self):
        set_config({"data": {"batch_sze": 16}})
        with pytest.raises(ValueError, match="batch_sze"):
            section("data", LoaderSection)
        clear_config()

    def test_wrong_class_is_reported(self):
        set_config(RootSection())
        with pytest.raises(TypeError, match="not InnerSection"):
            section("data", InnerSection)
        clear_config()

    def test_env_overrides_the_fields_inside(self, monkeypatch):
        cfg = RootSection()
        set_config(cfg)
        monkeypatch.setenv("DATA_BATCH_SIZE", "64")
        monkeypatch.setenv("DATA_INNER_DEPTH", "3")

        built = section("data")
        assert built.batch_size == 64  # int, not "64"
        assert built.inner.depth == 3
        assert built.workers == 0  # untouched field kept

        # Reading a section never mutates the active config.
        assert cfg.data.batch_size == 4
        assert built is not cfg.data
        clear_config()

    def test_env_can_be_turned_off(self, monkeypatch):
        cfg = RootSection()
        set_config(cfg)
        monkeypatch.setenv("DATA_BATCH_SIZE", "64")
        assert section("data", env=False) is cfg.data
        clear_config()

    def test_nested_dotted_name(self):
        set_config(RootSection())
        assert section("data.inner", InnerSection) == InnerSection(depth=1)
        clear_config()

    def test_missing_section_raises_even_with_a_class(self):
        """A class says how to BUILD the section, not that a missing one is
        fine -- quietly returning a default-constructed LoaderSection would
        look config-driven while ignoring the config entirely."""
        set_config(RootSection())
        with pytest.raises(KeyError, match="not set"):
            section("nope", LoaderSection)
        clear_config()

    def test_missing_section_without_a_class_raises(self):
        set_config(RootSection())
        with pytest.raises(KeyError):
            section("nope")
        clear_config()

    def test_error_points_at_import_time_reads_when_nothing_is_active(self):
        clear_config()
        with pytest.raises(KeyError, match="no config is active yet"):
            section("data", LoaderSection)
        with pytest.raises(KeyError, match="no config is active yet"):
            option("data.batch_size")

    def test_default_is_returned_for_a_missing_section(self):
        set_config(RootSection())
        sentinel = LoaderSection(batch_size=99)
        assert section("nope", LoaderSection, default=sentinel) is sentinel
        clear_config()

    def test_a_scalar_is_not_a_section(self):
        set_config(RootSection())
        with pytest.raises(TypeError, match="not a config section"):
            section("name", LoaderSection)
        clear_config()


# ===========================================================================
# Deferred defaults
#
# Python runs a default argument expression once, when the `def` executes --
# at import time, before any config is loaded. defer() records the lookup so
# @resolve_options can perform it per call instead.
# ===========================================================================


class TestDeferredDefaults:
    def test_option_as_a_default_is_frozen_at_def_time(self):
        """The problem defer() exists to solve, pinned so it stays visible."""
        set_config({"steps": 1})

        def eager(steps=option("steps", int, 0)):
            return steps

        set_config({"steps": 999})
        assert eager() == 1  # still the import-time value, not 999
        clear_config()

    def test_defer_reads_the_config_at_call_time(self):
        clear_config()

        @resolve_options
        def lazy(steps=defer("steps", int, 0)):
            return steps

        set_config({"steps": 1})
        assert lazy() == 1
        set_config({"steps": 999})
        assert lazy() == 999
        clear_config()

    def test_defining_it_needs_no_config(self):
        clear_config()

        @resolve_options
        def lazy(steps=defer("steps", int)):
            return steps

        # No config at definition time and no default: still fine until called.
        with pytest.raises(KeyError):
            lazy()
        set_config({"steps": 7})
        assert lazy() == 7
        clear_config()

    def test_explicit_arguments_win(self):
        set_config({"steps": 1})

        @resolve_options
        def lazy(steps=defer("steps", int, 0)):
            return steps

        assert lazy(5) == 5
        assert lazy(steps=6) == 6
        clear_config()

    def test_defer_section(self):
        clear_config()

        @resolve_options
        def build(cfg=defer_section("data", LoaderSection)):
            return cfg

        set_config({"data": {"batch_size": 16}})
        assert build() == LoaderSection(batch_size=16)
        set_config(RootSection())
        assert build() is get_config().data
        clear_config()

    def test_deferred_section_sees_env_overrides(self, monkeypatch):
        set_config(RootSection())

        @resolve_options
        def build(cfg=defer_section("data")):
            return cfg

        monkeypatch.setenv("DATA_BATCH_SIZE", "64")
        assert build().batch_size == 64
        clear_config()

    def test_other_defaults_are_untouched(self):
        set_config({"steps": 3})

        @resolve_options
        def lazy(steps=defer("steps", int, 0), tag="plain", *, flag=False):
            return steps, tag, flag

        assert lazy() == (3, "plain", False)
        assert lazy(tag="x", flag=True) == (3, "x", True)
        clear_config()

    def test_works_on_async_functions(self):
        set_config({"steps": 4})

        @resolve_options
        async def lazy(steps=defer("steps", int, 0)):
            return steps

        assert asyncio.run(lazy()) == 4
        clear_config()

    def test_decorator_without_deferred_defaults_is_an_error(self):
        with pytest.raises(TypeError, match="no defer"):

            @resolve_options
            def plain(x=1):
                return x

    def test_an_unresolved_deferred_says_what_is_missing(self):
        d = defer("optim.lr")
        assert "resolve_options" in repr(d)
        with pytest.raises(RuntimeError, match="resolve_options"):
            float(d)
        with pytest.raises(RuntimeError, match="resolve_options"):
            d.batch_size
        # Dunder lookups still behave, so copying and pickling protocols work.
        assert deepcopy(d).name == "optim.lr"
