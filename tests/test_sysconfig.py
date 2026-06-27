"""Tests for argklass.sysconfig."""

import json
import os
from dataclasses import dataclass, field
from typing import Optional

import pytest

from argklass.sysconfig import (
    ConfigContext,
    _deep_merge,
    _default_ctx,
    _select,
    apply_config,
    as_environment_variable,
    config_fields,
    config_template,
    configfield,
    env_template,
    from_dict,
    get_config,
    load_and_apply,
    load_config,
    option,
    overrides_snapshot,
    save_config,
    set_config,
    set_env_prefix,
    show_config,
    to_dict,
    tracked_options,
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
        assert ctx.option("b", int) is None

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

    def test_invalid_type_coercion_returns_none(self):
        set_config({"val": "not_a_number"})
        assert option("val", int, None) is None

    def test_invalid_env_coercion_returns_none(self, monkeypatch):
        monkeypatch.setenv("BAD_INT", "xyz")
        assert option("bad.int", int, 42) == 42

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

    def test_from_dict_wrong_value_types(self):
        """from_dict passes values as-is; the dataclass __init__ may coerce or fail."""
        cfg = from_dict(Flat, {"alpha": "123"})
        assert cfg.alpha == "123"

    def test_from_dict_nested_non_dict_for_dataclass_field(self):
        """If a dataclass field gets a non-dict value, pass it through raw."""
        data = {"debug": False, "workers": 2, "db": "not_a_dict"}
        cfg = from_dict(ServerConfig, data)
        assert cfg.db == "not_a_dict"

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
        assert ctx.option("a", int) is None

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
