import enum
import hashlib
import os
import pickle

import importlib_resources

from .parallel import as_completed, submit


def load_resource(path, key):
    return importlib_resources.files(path) / key


def _compute_version(data: bytes) -> str:
    sha = hashlib.sha256()
    sha.update(data)
    return sha.hexdigest()


thread_message = dict()
thread_futures = dict()


def get_cache_future(cache_key):
    return thread_futures.get(cache_key)


class CacheStatus(enum.Enum):
    NoCache = 0
    Updated = 1
    NoChange = 2


def wait_cache_update():
    futures = list(thread_futures.values())

    was_updated = False
    for result in as_completed(futures):
        if result.result() == CacheStatus.Updated:
            was_updated = True

    return was_updated


def get_cache_status(cache_key):
    return thread_message[cache_key]


def _load_cache(path):
    cached_result = None
    cached_version = None

    if os.path.exists(path):
        with open(path, "rb") as file:
            try:
                data = file.read()
                cached_version = _compute_version(data)
                cached_result = pickle.loads(data)
            except Exception:
                cached_result = None
                cached_version = None

    return cached_result, cached_version


def _save_cache(key, path, result, cached_version):
    global thread_message

    data = pickle.dumps(result)
    new_version = _compute_version(data)

    if cached_version is None or cached_version != new_version:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as file:
            file.write(data)

    if cached_version is None:
        thread_message[key] = "Generated command cache"
        return CacheStatus.NoCache

    elif cached_version != new_version:
        thread_message[key] = "Warning data was out of date"
        return CacheStatus.Updated

    else:
        thread_message[key] = "Command cache was up to date"
        return CacheStatus.NoChange


def cache_to_local(cache_key, location=__name__):
    """Cache function evaluation to the filesystem

    When the function is called again the cache is update async
    """
    global thread_message
    thread_message[cache_key] = "PENDING"

    def argkey(args, kwargs):
        key = hashlib.sha256()

        for arg in args:
            if arg is not None and hasattr(arg, "__name__"):
                key.update(str(arg.__name__).encode())

        for k, v in kwargs.items():
            key.update(str(k).encode())
            key.update(str(v).encode())

        return key.hexdigest()[:8]

    caches = dict()

    def _cache_to_local(fun):
        def wrapper(*args, **kwargs):
            nonlocal caches

            argk = argkey(args, kwargs)
            key = f"{cache_key}_{argk}"
            cached_result, cached_version = caches.get(key, (None, None))

            cache_file = load_resource(location, f"data/{key}.pkl")

            if cached_result is None:
                cached_result, cached_version = _load_cache(cache_file)
                caches[key] = cached_result, cached_version

            def _update_data():
                cached_result = fun(*args, **kwargs)
                status = _save_cache(
                    cache_key, cache_file, cached_result, cached_version
                )

                return cached_result, status

            def _safe_update():
                try:
                    _, status = _update_data()
                    return status
                except Exception as err:
                    thread_message[cache_key] = err
                    raise

            if cached_result is not None:
                global thread_futures

                # launch a async update
                future = submit(_safe_update)
                thread_futures[cache_key] = future

                # return current cache
                return cached_result
            else:
                # Have to do it sync
                result, _ = _update_data()
                return result

        return wrapper

    return _cache_to_local
