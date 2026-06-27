import concurrent.futures

_executor = None


def poolexecutor():
    global _executor
    if _executor is None:
        from .settings import settings

        _executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=settings.parallel_max_workers,
        )
    return _executor


def submit(fun, *args):
    return poolexecutor().submit(fun, *args)


def as_completed(futures):
    return concurrent.futures.as_completed(futures)


def shutdown():
    global _executor
    if _executor is not None:
        _executor.shutdown()
        _executor = None
