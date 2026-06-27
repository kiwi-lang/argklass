"""Dataclass-driven configuration with environment variable support.

Define configuration as nested dataclasses, with values resolved
from (in priority order):

    1. Environment variables
    2. Config dict (per-context or global)
    3. Hardcoded defaults

Simple (single-app) usage
-------------------------

.. code-block:: python

    from dataclasses import dataclass, field
    from argklass.sysconfig import configfield, load_config, save_config, set_env_prefix

    set_env_prefix("MYAPP")

    @dataclass
    class DatabaseConfig:
        host: str = configfield("db.host", str, "localhost")    # MYAPP_DB_HOST
        port: int = configfield("db.port", int, 5432)           # MYAPP_DB_PORT

    @dataclass
    class AppConfig:
        debug: bool = configfield("app.debug", bool, False)
        db: DatabaseConfig = field(default_factory=DatabaseConfig)

    config = load_config(AppConfig, "config.yaml")
    save_config(config, "config.yaml")

Multi-library usage
-------------------

When several independent libraries all use ``argklass`` in the same
process, use a :class:`ConfigContext` per library so their prefixes,
config dicts, and tracked options stay isolated:

.. code-block:: python

    from argklass.sysconfig import ConfigContext

    ctx = ConfigContext(prefix="MYLIB")

    @dataclass
    class MyLibConfig:
        rate: float = ctx.configfield("rate", float, 1.0)   # MYLIB_RATE

    ctx.set_config({"rate": 2.5})
    cfg = MyLibConfig()   # cfg.rate == 2.5

The module-level helpers (``configfield``, ``set_config``, …) simply
delegate to a default :class:`ConfigContext`.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import field, fields, is_dataclass
from typing import Any, Iterator, Type, TypeVar

T = TypeVar("T")


# ===================================================================
# ConfigContext — all state lives here
# ===================================================================


class ConfigContext:
    """Isolated configuration namespace.

    Each context owns its own prefix, config dict, and option tracker.
    Multiple libraries can coexist in the same process by using
    separate ``ConfigContext`` instances.

    Parameters
    ----------
    prefix
        Environment-variable prefix (e.g. ``"MYLIB"`` →
        ``MYLIB_SOME_OPTION``).  A trailing underscore is added
        automatically if missing.
    config
        Initial config dict.  Can be replaced later with
        :meth:`set_config`.
    """

    def __init__(self, prefix: str = "", config: dict | None = None):
        self._prefix = self._normalise_prefix(prefix)
        self._config: dict = config or {}
        self._tracked: dict[str, dict] = {}

    @staticmethod
    def _normalise_prefix(prefix: str) -> str:
        if prefix:
            return prefix.rstrip("_") + "_"
        return ""

    # -- prefix ----------------------------------------------------------

    @property
    def prefix(self) -> str:
        return self._prefix

    @prefix.setter
    def prefix(self, value: str) -> None:
        self._prefix = self._normalise_prefix(value)

    def as_environment_variable(self, name: str) -> str:
        """Convert a dotted name to ``PREFIX_UPPER_SNAKE``."""
        return self._prefix + "_".join(frag.upper() for frag in name.split("."))

    # -- config dict -----------------------------------------------------

    def set_config(self, config_dict: dict | None) -> None:
        """Replace the config dict."""
        self._config = config_dict or {}

    def get_config(self) -> dict:
        """Return the current config dict."""
        return self._config

    @contextmanager
    def apply_config(self, overrides: dict):
        """Temporarily merge *overrides* into the config dict.

        The previous state is restored on exit.
        """
        old = self._config
        merged = deepcopy(old) if old else {}
        _deep_merge(merged, overrides)
        self._config = merged
        try:
            yield
        finally:
            self._config = old

    # -- option resolution -----------------------------------------------

    def option(self, name: str, etype: type, default=None):
        """Resolve a single value: env → config dict → *default*."""
        frags = name.split(".")
        env_name = self.as_environment_variable(name)
        env_value = _getenv(env_name, etype)

        lookup: Any = self._config
        for frag in frags[:-1]:
            lookup = lookup.get(frag, {}) if isinstance(lookup, dict) else {}
        config_value = lookup.get(frags[-1]) if isinstance(lookup, dict) else None

        final = _select(env_value, config_value, default)

        self._tracked[name] = {
            "type": etype,
            "default": default,
            "value": final,
        }

        if final is None:
            return None
        try:
            if etype is bool and not isinstance(final, bool):
                if isinstance(final, str):
                    return final.lower() in ("1", "true", "yes", "on")
                return bool(final)
            return etype(final)
        except (TypeError, ValueError):
            return None

    def configfield(self, name: str, etype: type, default=None):
        """Dataclass field resolved via this context at instantiation time.

        Stores ``(name, etype, default, context)`` in the field metadata
        so introspection tools can enumerate all options.
        """
        _ctx, _name, _etype, _default = self, name, etype, default

        return field(
            default_factory=lambda: _ctx.option(_name, _etype, _default),
            metadata={
                "_kind": "config",
                "_config_name": _name,
                "_config_type": _etype,
                "_config_default": _default,
                "_config_ctx": _ctx,
            },
        )

    # -- file I/O --------------------------------------------------------

    def load_config(self, cls: Type[T], filepath: str, fmt: str | None = None) -> T:
        """Load a config file into a dataclass of *cls*."""
        data = _load_raw(filepath, fmt)
        return from_dict(cls, data)

    def save_config(
        self,
        instance,
        filepath: str,
        fmt: str | None = None,
        *,
        skip_none: bool = False,
    ) -> None:
        """Serialize a dataclass instance to a file."""
        data = to_dict(instance, skip_none=skip_none)
        _dump_raw(data, filepath, fmt)

    def load_and_apply(self, filepath: str, fmt: str | None = None) -> dict:
        """Load a config file and set it as this context's config dict."""
        data = _load_raw(filepath, fmt)
        self.set_config(data)
        return data

    # -- introspection ---------------------------------------------------

    def config_fields(self, cls) -> Iterator[tuple[str, type, Any, str]]:
        """Yield ``(dotted_name, type, default, env_var)`` for every
        :meth:`configfield` in *cls* (including nested dataclasses).
        """
        for f in fields(cls):
            meta = f.metadata
            if meta.get("_kind") == "config":
                name = meta["_config_name"]
                ctx = meta.get("_config_ctx", self)
                yield (
                    name,
                    meta["_config_type"],
                    meta["_config_default"],
                    ctx.as_environment_variable(name),
                )
            elif isinstance(f.type, type) and is_dataclass(f.type):
                yield from self.config_fields(f.type)

    def show_config(self, cls_or_instance, *, to_json: bool = False) -> None:
        """Print a summary of all config fields."""
        is_instance = is_dataclass(cls_or_instance) and not isinstance(
            cls_or_instance, type
        )
        if is_instance:
            cls = type(cls_or_instance)
            values = to_dict(cls_or_instance)
        else:
            cls = cls_or_instance
            values = {}

        entries: dict[str, dict] = {}
        for name, etype, default, env_name in self.config_fields(cls):
            frags = name.split(".")
            entry = {
                "type": etype.__name__,
                "default": default,
                "env_name": env_name,
            }
            if is_instance:
                lkp: Any = values
                for frag in frags:
                    lkp = lkp.get(frag) if isinstance(lkp, dict) else None
                    if lkp is None:
                        break
                entry["value"] = lkp
            else:
                entry["value"] = default

            dct: dict = entries
            for p in frags[:-1]:
                dct = dct.setdefault(p, {})
            dct[frags[-1]] = entry

        if to_json:
            print(json.dumps(entries, indent=2, default=str))
        else:
            _compact_print(entries, depth=0)

    def env_template(self, cls, *, commented: bool = True) -> str:
        """Generate a ``.env`` file template."""
        lines: list[str] = []
        pfx = "# " if commented else ""
        for _name, etype, default, env_name in self.config_fields(cls):
            if default is None:
                value = ""
            elif etype is bool:
                value = str(default).lower()
            else:
                value = str(default)
            lines.append(f"{pfx}{env_name}={value}")
        return "\n".join(lines) + "\n"

    def config_template(self, cls, fmt: str = "yaml") -> str:
        """Generate a config-file template with env-var comments."""
        lines: list[str] = []
        tree: dict = {}
        for name, etype, default, env_name in self.config_fields(cls):
            frags = name.split(".")
            node = tree
            for frag in frags[:-1]:
                node = node.setdefault(frag, {})
            node[frags[-1]] = (default, env_name, etype)

        def _walk(node: dict, depth: int = 0):
            indent = "  " * depth
            for key, val in node.items():
                if isinstance(val, tuple):
                    dflt, env, et = val
                    lines.append(f"{indent}# env: {env}  (type: {et.__name__})")
                    if dflt is None:
                        lines.append(f"{indent}{key}:")
                    elif et is bool:
                        lines.append(f"{indent}{key}: {str(dflt).lower()}")
                    elif isinstance(dflt, str):
                        lines.append(f'{indent}{key}: "{dflt}"')
                    else:
                        lines.append(f"{indent}{key}: {dflt}")
                else:
                    lines.append(f"{indent}{key}:")
                    _walk(val, depth + 1)

        _walk(tree)
        return "\n".join(lines) + "\n"

    def tracked_options(self) -> dict[str, dict]:
        """Snapshot of every option resolved through this context."""
        return {name: {**val} for name, val in self._tracked.items()}

    def overrides_snapshot(self) -> dict[str, Any]:
        """Options whose current value differs from the default."""
        return {
            name: val["value"]
            for name, val in self._tracked.items()
            if val["value"] != val["default"]
        }


# ===================================================================
# Default context + module-level convenience API
# ===================================================================

_default_ctx = ConfigContext()


def set_env_prefix(prefix: str) -> None:
    """Set the prefix on the default :class:`ConfigContext`."""
    _default_ctx.prefix = prefix


def get_env_prefix() -> str:
    """Return the prefix of the default :class:`ConfigContext`."""
    return _default_ctx.prefix


def as_environment_variable(name: str, prefix: str | None = None) -> str:
    """Convert a dotted config name to ``PREFIX_UPPER_SNAKE``.

    >>> as_environment_variable("db.host", prefix="MYAPP_")
    'MYAPP_DB_HOST'
    """
    if prefix is not None:
        return prefix + "_".join(frag.upper() for frag in name.split("."))
    return _default_ctx.as_environment_variable(name)


def set_config(config_dict: dict | None) -> None:
    """Replace the config dict on the default context."""
    _default_ctx.set_config(config_dict)


def get_config() -> dict:
    """Return the config dict of the default context."""
    return _default_ctx.get_config()


@contextmanager
def apply_config(overrides: dict):
    """Temporarily merge *overrides* into the default context's config."""
    with _default_ctx.apply_config(overrides):
        yield


def option(name: str, etype: type, default=None):
    """Resolve a value via the default context."""
    return _default_ctx.option(name, etype, default)


def configfield(name: str, etype: type, default=None):
    """Dataclass field resolved via the default context."""
    return _default_ctx.configfield(name, etype, default)


def load_config(cls: Type[T], filepath: str, fmt: str | None = None) -> T:
    """Load a config file and return a dataclass instance of *cls*."""
    return _default_ctx.load_config(cls, filepath, fmt)


def save_config(
    instance,
    filepath: str,
    fmt: str | None = None,
    *,
    skip_none: bool = False,
) -> None:
    """Serialize a dataclass instance to a config file."""
    _default_ctx.save_config(instance, filepath, fmt, skip_none=skip_none)


def load_and_apply(filepath: str, fmt: str | None = None) -> dict:
    """Load a config file and set it as the default context's config."""
    return _default_ctx.load_and_apply(filepath, fmt)


def config_fields(cls) -> Iterator[tuple[str, type, Any, str]]:
    """Yield ``(dotted_name, type, default, env_var)`` for configfields in *cls*."""
    yield from _default_ctx.config_fields(cls)


def show_config(cls_or_instance, *, to_json: bool = False) -> None:
    """Print config summary using the default context."""
    _default_ctx.show_config(cls_or_instance, to_json=to_json)


def env_template(cls, *, commented: bool = True) -> str:
    """Generate a ``.env`` template using the default context's prefix."""
    return _default_ctx.env_template(cls, commented=commented)


def config_template(cls, fmt: str = "yaml") -> str:
    """Generate a config-file template using the default context."""
    return _default_ctx.config_template(cls, fmt=fmt)


def tracked_options() -> dict[str, dict]:
    """Return tracked options from the default context."""
    return _default_ctx.tracked_options()


def overrides_snapshot() -> dict[str, Any]:
    """Return overrides from the default context."""
    return _default_ctx.overrides_snapshot()


# ===================================================================
# Pure helpers (no state)
# ===================================================================


def _getenv(name: str, expected_type: type):
    """Read an environment variable and coerce to *expected_type*."""
    raw = os.getenv(name)
    if raw is None:
        return None
    try:
        if expected_type is bool:
            return raw.lower() in ("1", "true", "yes", "on")
        return expected_type(raw)
    except (TypeError, ValueError):
        return None


def _select(*values):
    """Return the first non-``None`` value.

    Unlike a simple ``or`` chain, this correctly preserves falsy-but-valid
    values such as ``0``, ``False``, and ``""``.
    """
    for v in values:
        if v is not None:
            return v
    return None


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Recursively merge *overlay* into *base* (mutates *base*)."""
    for key, value in overlay.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = deepcopy(value)
    return base


# ===================================================================
# Dict ↔ dataclass conversion  (stateless)
# ===================================================================


def to_dict(instance, *, skip_none: bool = False) -> dict:
    """Recursively convert a dataclass instance to a plain dict."""
    if not is_dataclass(instance) or isinstance(instance, type):
        return instance

    result: dict = {}
    for f in fields(instance):
        value = getattr(instance, f.name)
        if skip_none and value is None:
            continue
        result[f.name] = _convert_value(value, skip_none=skip_none)
    return result


def _convert_value(value, *, skip_none: bool = False):
    if is_dataclass(value) and not isinstance(value, type):
        return to_dict(value, skip_none=skip_none)
    if isinstance(value, list):
        return [_convert_value(v, skip_none=skip_none) for v in value]
    if isinstance(value, dict):
        return {k: _convert_value(v, skip_none=skip_none) for k, v in value.items()}
    return value


def from_dict(cls: Type[T], data: dict) -> T:
    """Create a dataclass instance from a plain dict (recursive)."""
    if not is_dataclass(cls) or not isinstance(data, dict):
        return data  # type: ignore[return-value]

    kwargs: dict = {}
    hints = _resolve_field_types(cls)

    for f in fields(cls):
        if f.name not in data:
            continue

        value = data[f.name]
        ftype = hints.get(f.name, f.type)

        if isinstance(ftype, type) and is_dataclass(ftype) and isinstance(value, dict):
            kwargs[f.name] = from_dict(ftype, value)
        elif isinstance(value, list):
            item_type = _list_item_type(ftype)
            if item_type is not None and is_dataclass(item_type):
                kwargs[f.name] = [
                    from_dict(item_type, v) if isinstance(v, dict) else v
                    for v in value
                ]
            else:
                kwargs[f.name] = value
        else:
            kwargs[f.name] = value

    return cls(**kwargs)


def _resolve_field_types(cls) -> dict:
    try:
        from typing import get_type_hints
        return get_type_hints(cls)
    except Exception:
        return {f.name: f.type for f in fields(cls)}


def _list_item_type(ftype):
    origin = getattr(ftype, "__origin__", None)
    if origin is list:
        args = getattr(ftype, "__args__", None)
        return args[0] if args else None
    return None


# ===================================================================
# File I/O  (stateless)
# ===================================================================

_FORMAT_MAP = {
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".hjson": "hjson",
}


def _detect_format(filepath: str) -> str:
    ext = os.path.splitext(filepath)[1].lower()
    return _FORMAT_MAP.get(ext, "yaml")


def _load_raw(filepath: str, fmt: str | None = None) -> dict:
    fmt = fmt or _detect_format(filepath)
    with open(filepath) as fh:
        if fmt == "yaml":
            import yaml
            return yaml.safe_load(fh) or {}
        if fmt == "json":
            return json.load(fh)
        if fmt == "hjson":
            import hjson
            return hjson.load(fh)
        raise ValueError(f"Unknown config format: {fmt!r}")


def _dump_raw(data: dict, filepath: str, fmt: str | None = None) -> None:
    fmt = fmt or _detect_format(filepath)
    with open(filepath, "w") as fh:
        if fmt == "yaml":
            import yaml
            yaml.dump(data, fh, default_flow_style=False, sort_keys=False)
        elif fmt == "json":
            json.dump(data, fh, indent=2, default=str)
            fh.write("\n")
        elif fmt == "hjson":
            import hjson
            hjson.dump(data, fh)
        else:
            raise ValueError(f"Unknown config format: {fmt!r}")


def _compact_print(d: dict, depth: int) -> None:
    indent = "    " * depth
    for key, val in d.items():
        if "env_name" in val:
            current = val.get("value")
            default = val.get("default")
            env = val["env_name"]
            if current != default and current is not None:
                print(
                    f"{indent}{key:<{30 - len(indent)}}"
                    f": {str(current):<40} (default={default})"
                )
            else:
                print(f"{indent}{key:<{30 - len(indent)}}: {str(current):<40} {env}")
        else:
            print(f"{indent}{key}:")
            _compact_print(val, depth + 1)
