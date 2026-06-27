"""Tests for argklass/cache.py — caching, versioning, save/load."""

import os
import pickle
import tempfile
import uuid

import pytest

from argklass.cache import (
    CacheStatus,
    _compute_version,
    _load_cache,
    _save_cache,
    cache_to_local,
    get_cache_future,
    get_cache_status,
    load_resource,
    thread_futures,
    thread_message,
    wait_cache_update,
)


class TestCacheBasics:
    def test_get_cache_future_missing(self):
        assert get_cache_future("nonexistent_key") is None

    def test_cache_status_no_cache(self):
        with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as f:
            path = f.name
        os.unlink(path)

        status = _save_cache("test_key", path, {"data": 1}, None)
        assert status == CacheStatus.NoCache
        assert "Generated" in thread_message["test_key"]
        os.unlink(path)

    def test_cache_status_no_change(self):
        data = {"data": 1}
        pickled = pickle.dumps(data)
        version = _compute_version(pickled)

        with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as f:
            f.write(pickled)
            path = f.name

        status = _save_cache("test_nc", path, data, version)
        assert status == CacheStatus.NoChange
        os.unlink(path)

    def test_cache_status_updated(self):
        old_data = {"old": True}
        old_pickled = pickle.dumps(old_data)
        old_version = _compute_version(old_pickled)

        with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as f:
            f.write(old_pickled)
            path = f.name

        new_data = {"new": True}
        status = _save_cache("test_updated", path, new_data, old_version)
        assert status == CacheStatus.Updated
        os.unlink(path)

    def test_wait_cache_update_empty(self):
        old = dict(thread_futures)
        thread_futures.clear()
        result = wait_cache_update()
        assert result is False
        thread_futures.update(old)

    def test_get_cache_status(self):
        thread_message["test_status_key"] = "Some status"
        assert get_cache_status("test_status_key") == "Some status"

    def test_load_resource(self):
        result = load_resource("argklass", "sysconfig.py")
        assert result is not None


class TestLoadCache:
    def test_load_cache_valid(self):
        data = {"valid": True}
        pickled = pickle.dumps(data)

        with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as f:
            f.write(pickled)
            path = f.name

        result, version = _load_cache(path)
        assert result == {"valid": True}
        assert version is not None
        os.unlink(path)

    def test_load_cache_no_file(self):
        result, version = _load_cache("/tmp/nonexistent_cache_file.pkl")
        assert result is None
        assert version is None

    def test_load_cache_corrupt_file(self):
        with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as f:
            f.write(b"this is not valid pickle data")
            path = f.name

        result, version = _load_cache(path)
        assert result is None
        assert version is None
        os.unlink(path)


class TestCacheFailureModes:
    def test_wait_cache_update_with_updated_future(self):
        from concurrent.futures import Future

        old = dict(thread_futures)
        thread_futures.clear()

        f = Future()
        f.set_result(CacheStatus.Updated)
        thread_futures["test_wcu"] = f

        result = wait_cache_update()
        assert result is True

        thread_futures.clear()
        thread_futures.update(old)

    def test_wait_cache_update_with_nochange_future(self):
        from concurrent.futures import Future

        old = dict(thread_futures)
        thread_futures.clear()

        f = Future()
        f.set_result(CacheStatus.NoChange)
        thread_futures["test_wcu_nc"] = f

        result = wait_cache_update()
        assert result is False

        thread_futures.clear()
        thread_futures.update(old)

    def test_cache_to_local_sync_path(self, tmp_path, monkeypatch):
        import argklass.cache

        unique_key = f"test_sync_{uuid.uuid4().hex[:8]}"

        call_count = 0

        @cache_to_local(unique_key, __name__)
        def compute(x):
            nonlocal call_count
            call_count += 1
            return x * 2

        monkeypatch.setattr(
            argklass.cache,
            "load_resource",
            lambda path, key: str(tmp_path / key),
        )

        result = compute(5)
        assert result == 10
        assert call_count == 1

    def test_get_cache_status_missing_key(self):
        with pytest.raises(KeyError):
            get_cache_status("definitely_nonexistent_key_xyz")

    def test_save_cache_creates_directory(self, tmp_path):
        path = str(tmp_path / "subdir" / "deep" / "cache.pkl")
        status = _save_cache("dir_test", path, {"nested": True}, None)
        assert status == CacheStatus.NoCache
        assert os.path.exists(path)
