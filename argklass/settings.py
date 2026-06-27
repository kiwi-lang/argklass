"""Internal configuration for the argklass package itself.

Uses :class:`~argklass.sysconfig.ConfigContext` so that settings are
resolved from environment variables (``ARGKLASS_*``), an optional
config dict, or built-in defaults.

"""

import importlib
import pathlib
import site
import sys
import sysconfig as _sc
from dataclasses import dataclass
from functools import lru_cache

from .sysconfig import ConfigContext

ctx = ConfigContext(prefix="ARGKLASS")


def _install_paths() -> list[str]:
    """Collect all paths where "properly installed" code lives.

    Includes site-packages **and** the stdlib directories, so that
    stdlib modules like ``os`` or ``json`` are never mistaken for
    editable installs.
    """
    paths = site.getsitepackages() + [site.getusersitepackages()]
    for key in ("purelib", "platlib", "stdlib"):
        p = _sc.get_path(key)
        if p:
            paths.append(p)
    return [str(pathlib.Path(p).resolve()) for p in paths]


_INSTALL_PATHS = _install_paths()


@lru_cache(maxsize=128)
def is_editable_install(module_path: str) -> bool:
    """Return True if *module_path* resolves to a location outside site-packages.

    This indicates the package is installed in editable / development mode.
    ``module_path`` is a dotted module name such as ``"mypackage"`` or
    ``"mypackage.submodule"`` — the same kind of string passed as
    ``location`` to :func:`~argklass.cache.cache_to_local`.
    """
    try:
        mod = importlib.import_module(module_path)
    except (ImportError, ModuleNotFoundError):
        return False

    mod_file = getattr(mod, "__file__", None)
    if mod_file is None:
        return False

    resolved = str(pathlib.Path(mod_file).resolve())
    return not any(resolved.startswith(sp) for sp in _INSTALL_PATHS)


_NESTING_SUPPORTED = sys.version_info < (3, 14)


@dataclass
class Settings:
    cache_enabled: bool = ctx.configfield("cache.enabled", bool, default=True)
    cache_skip_editable: bool = ctx.configfield("cache.skip_editable", bool, default=True)
    cache_async_update: bool = ctx.configfield("cache.async_update", bool, default=True)
    parallel_max_workers: int = ctx.configfield("parallel.max_workers", int, default=None)
    format_column_width: int = ctx.configfield("format.column_width", int, default=50)
    format_description_width: int = ctx.configfield("format.description_width", int, default=80)
    nested_groups: bool = ctx.configfield(
        "nested_groups", bool, default=_NESTING_SUPPORTED,
    )


settings = Settings()
