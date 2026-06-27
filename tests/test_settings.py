"""Tests for argklass/settings.py — self-configuration of argklass."""

import uuid

import pytest


class TestSettingsDefaults:
    def test_settings_is_dataclass(self):
        from dataclasses import is_dataclass

        from argklass.settings import Settings, settings

        assert is_dataclass(Settings)
        assert is_dataclass(settings)

    def test_default_values(self):
        from argklass.settings import settings

        assert settings.cache_enabled is True
        assert settings.cache_skip_editable is True
        assert settings.cache_async_update is True
        assert settings.parallel_max_workers is None
        assert settings.format_column_width == 50
        assert settings.format_description_width == 80

    def test_nested_groups_default_follows_python_version(self, monkeypatch):
        import os
        import sys

        from argklass.settings import Settings

        monkeypatch.delenv("ARGKLASS_NESTED_GROUPS", raising=False)
        fresh = Settings()
        assert fresh.nested_groups is (sys.version_info < (3, 14))

    def test_context_uses_argklass_prefix(self):
        from argklass.settings import ctx

        assert ctx.prefix == "ARGKLASS_"

    def test_settings_fields_have_config_metadata(self):
        from dataclasses import fields

        from argklass.settings import Settings

        for f in fields(Settings):
            assert f.metadata.get("_kind") == "config", f"{f.name} missing config metadata"
            assert "_config_name" in f.metadata


class TestIsEditableInstall:
    def test_returns_bool(self):
        from argklass.settings import is_editable_install

        assert isinstance(is_editable_install("argklass"), bool)

    def test_nonexistent_module_returns_false(self):
        from argklass.settings import is_editable_install

        is_editable_install.cache_clear()
        assert is_editable_install("no_such_module_xyz_12345") is False

    def test_builtin_module_returns_false(self):
        from argklass.settings import is_editable_install

        is_editable_install.cache_clear()
        assert is_editable_install("os") is False

    def test_stdlib_in_site_packages_returns_false(self):
        from argklass.settings import is_editable_install

        is_editable_install.cache_clear()
        assert is_editable_install("json") is False

    def test_editable_package_detected(self):
        """argklass itself is installed as editable in this dev environment."""
        from argklass.settings import is_editable_install

        is_editable_install.cache_clear()
        result = is_editable_install("argklass")
        assert isinstance(result, bool)
        # In this dev environment it should be True
        assert result is True

    def test_result_is_cached(self):
        from argklass.settings import is_editable_install

        is_editable_install.cache_clear()
        r1 = is_editable_install("argklass")
        r2 = is_editable_install("argklass")
        assert r1 == r2
        assert is_editable_install.cache_info().hits >= 1


class TestCacheDisabled:
    def test_cache_to_local_bypassed_when_disabled(self, monkeypatch):
        from argklass.settings import settings

        monkeypatch.setattr(settings, "cache_enabled", False)

        from argklass.cache import cache_to_local, thread_message

        call_count = 0
        key = f"test_disabled_{uuid.uuid4().hex[:8]}"

        @cache_to_local(key, __name__)
        def compute(x):
            nonlocal call_count
            call_count += 1
            return x * 2

        result = compute(5)
        assert result == 10
        assert call_count == 1
        assert thread_message[key] == "Caching disabled"

    def test_cache_to_local_calls_every_time_when_disabled(self, monkeypatch):
        from argklass.settings import settings

        monkeypatch.setattr(settings, "cache_enabled", False)

        from argklass.cache import cache_to_local

        call_count = 0
        key = f"test_nocache_{uuid.uuid4().hex[:8]}"

        @cache_to_local(key, __name__)
        def compute(x):
            nonlocal call_count
            call_count += 1
            return x + 1

        compute(1)
        compute(2)
        compute(3)
        assert call_count == 3


class TestCacheSkipEditable:
    def test_cache_skipped_for_editable_location(self, monkeypatch):
        """When location module is editable, caching is skipped."""
        import argklass.settings
        from argklass.settings import settings

        monkeypatch.setattr(settings, "cache_enabled", True)
        monkeypatch.setattr(settings, "cache_skip_editable", True)
        # Force is_editable_install to return True for our location
        monkeypatch.setattr(
            argklass.settings, "is_editable_install", lambda loc: True
        )

        from argklass.cache import cache_to_local, thread_message

        call_count = 0
        key = f"test_editable_{uuid.uuid4().hex[:8]}"

        @cache_to_local(key, "some.editable.module")
        def compute(x):
            nonlocal call_count
            call_count += 1
            return x * 10

        assert compute(3) == 30
        assert compute(3) == 30
        assert call_count == 2
        assert thread_message[key] == "Caching skipped (editable install)"

    def test_cache_not_skipped_when_skip_editable_false(self, tmp_path, monkeypatch):
        """When cache_skip_editable=False, editable installs are still cached."""
        import argklass.cache
        import argklass.settings
        from argklass.settings import settings

        monkeypatch.setattr(settings, "cache_enabled", True)
        monkeypatch.setattr(settings, "cache_skip_editable", False)
        monkeypatch.setattr(
            argklass.settings, "is_editable_install", lambda loc: True
        )
        monkeypatch.setattr(
            argklass.cache,
            "load_resource",
            lambda path, key: str(tmp_path / key),
        )

        from argklass.cache import cache_to_local, thread_message

        call_count = 0
        key = f"test_no_skip_{uuid.uuid4().hex[:8]}"

        @cache_to_local(key, "some.editable.module")
        def compute(x):
            nonlocal call_count
            call_count += 1
            return x + 100

        result = compute(1)
        assert result == 101
        assert call_count == 1
        # Should NOT say "skipped" — it actually cached
        assert "skipped" not in thread_message.get(key, "").lower()

    def test_cache_not_skipped_for_non_editable(self, tmp_path, monkeypatch):
        """Non-editable location should be cached normally."""
        import argklass.cache
        import argklass.settings
        from argklass.settings import settings

        monkeypatch.setattr(settings, "cache_enabled", True)
        monkeypatch.setattr(settings, "cache_skip_editable", True)
        monkeypatch.setattr(
            argklass.settings, "is_editable_install", lambda loc: False
        )
        monkeypatch.setattr(
            argklass.cache,
            "load_resource",
            lambda path, key: str(tmp_path / key),
        )

        from argklass.cache import cache_to_local, thread_message

        call_count = 0
        key = f"test_noneditable_{uuid.uuid4().hex[:8]}"

        @cache_to_local(key, "some.installed.module")
        def compute(x):
            nonlocal call_count
            call_count += 1
            return x + 200

        result = compute(5)
        assert result == 205
        assert call_count == 1
        assert "skipped" not in thread_message.get(key, "").lower()


class TestCacheAsyncDisabled:
    def test_sync_update_when_async_disabled(self, tmp_path, monkeypatch):
        import argklass.cache
        import argklass.settings
        from argklass.settings import settings

        monkeypatch.setattr(settings, "cache_enabled", True)
        monkeypatch.setattr(settings, "cache_skip_editable", False)
        monkeypatch.setattr(settings, "cache_async_update", False)
        monkeypatch.setattr(
            argklass.settings, "is_editable_install", lambda loc: False
        )
        monkeypatch.setattr(
            argklass.cache,
            "load_resource",
            lambda path, key: str(tmp_path / key),
        )

        from argklass.cache import cache_to_local

        key = f"test_sync_up_{uuid.uuid4().hex[:8]}"
        call_count = 0

        @cache_to_local(key, __name__)
        def compute(x):
            nonlocal call_count
            call_count += 1
            return x * 3

        result = compute(7)
        assert result == 21
        assert call_count == 1


class TestEnvVarOverrides:
    def test_cache_enabled_from_env(self, monkeypatch):
        monkeypatch.setenv("ARGKLASS_CACHE_ENABLED", "false")

        from argklass.settings import ctx

        val = ctx.option("cache.enabled", bool, default=True)
        assert val is False

    def test_cache_skip_editable_from_env(self, monkeypatch):
        monkeypatch.setenv("ARGKLASS_CACHE_SKIP_EDITABLE", "false")

        from argklass.settings import ctx

        val = ctx.option("cache.skip_editable", bool, default=True)
        assert val is False

    def test_format_column_width_from_env(self, monkeypatch):
        monkeypatch.setenv("ARGKLASS_FORMAT_COLUMN_WIDTH", "72")

        from argklass.settings import ctx

        val = ctx.option("format.column_width", int, default=50)
        assert val == 72

    def test_parallel_max_workers_from_env(self, monkeypatch):
        monkeypatch.setenv("ARGKLASS_PARALLEL_MAX_WORKERS", "4")

        from argklass.settings import ctx

        val = ctx.option("parallel.max_workers", int, default=None)
        assert val == 4


class TestSettingsWiring:
    def test_argformat_uses_column_width(self, monkeypatch):
        from argklass.settings import settings

        monkeypatch.setattr(settings, "format_column_width", 72)

        from argklass.argformat import ArgumentParserIterator

        it = ArgumentParserIterator()
        assert it.col == 72

    def test_argformat_formater_uses_description_width(self, monkeypatch):
        from argklass.settings import settings

        monkeypatch.setattr(settings, "format_description_width", 120)

        from argklass.argformat import ArgumentFormater

        fmt = ArgumentFormater()
        assert fmt.description_width == 120

    def test_parallel_uses_max_workers(self, monkeypatch):
        import argklass.parallel
        from argklass.settings import settings

        argklass.parallel._executor = None
        monkeypatch.setattr(settings, "parallel_max_workers", 2)

        executor = argklass.parallel.poolexecutor()
        assert executor._max_workers == 2

        argklass.parallel._executor = None


class TestConfigDict:
    def test_settings_via_config_dict(self):
        from argklass.settings import ctx

        old = ctx.get_config()
        ctx.set_config({"format": {"column_width": 60}})

        val = ctx.option("format.column_width", int, default=50)
        assert val == 60

        ctx.set_config(old)

    def test_reinstantiate_picks_up_config(self):
        from argklass.settings import Settings, ctx

        old = ctx.get_config()
        ctx.set_config({"cache": {"async_update": False}})

        fresh = Settings()
        assert fresh.cache_async_update is False

        ctx.set_config(old)
