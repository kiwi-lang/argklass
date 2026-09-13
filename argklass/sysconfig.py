"""Dataclass-driven configuration with environment variable support.

Define configuration as nested dataclasses, load a file into them, install the
result as *the* active config, and read values anywhere by dotted key -- without
passing the instance around.

Values resolve in priority order:

    1. Environment variables  (``PREFIX_DOTTED_KEY``)
    2. The active config      (a dataclass instance, or a plain dict)
    3. The declared default

With no default, an unresolvable key raises :class:`KeyError` rather than
silently returning ``None`` -- that is what catches typos before a long run.

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

Instance-first usage
--------------------

The active config can be a dataclass *instance*, not just a dict. Then
``option()`` reads the live object, so whatever ``__post_init__`` computed or
validated is what you read back:

.. code-block:: python

    from argklass.sysconfig import load_and_apply, option, section, set_option

    cfg = load_and_apply("train.yaml", cls=TrainConfig)  # loads AND activates
    option("optim.lr")                 # from the file, or the dataclass default
    option("optim.nesterov", False)    # default for a key the tree lacks
    set_option("optim.lr", 1e-5)       # writes through to the instance
    section("optim", OptimConfig)      # the whole sub-config, as a dataclass

``option`` reads one value as the config holds it; ``section`` assembles a
component's config -- hydrating a dict subtree into the dataclass and applying
the ``PREFIX_SECTION_FIELD`` environment variables for the fields inside it --
so it can be handed straight to whatever takes that dataclass.

Loading is strict: a key in the file that no field declares is an error naming
the file and the key. Values are coerced to the field's annotated type --
``Path``, ``Enum``, ``X | None``, ``list[T]``, ``tuple[T, ...]``, ``dict[K, V]``
and the usual scalars.

Where the active config lives
-----------------------------

Two layers:

* :func:`set_config` (and ``load_and_apply``) installs it **process-wide**.
  Every thread and task sees it -- worker threads, ``run_in_executor``
  callbacks. A bare ContextVar would not survive that trip: a new thread starts
  with an empty context and would see no config at all.
* :func:`use_config`, :func:`push_config`, :func:`apply_config` and
  :func:`overlay` install a **context-local** config that shadows it. Scoped
  overrides stay inside their context, so concurrent asyncio tasks cannot
  corrupt each other's view.

:func:`get_config` and :func:`option` read the context-local value when the
current context has one, and fall back to the process-wide one otherwise. A
*scoped* override deliberately does not reach a thread spawned inside its block;
carry it over with ``contextvars.copy_context().run(fn)`` if you need to.

Multi-library usage
-------------------

When several independent libraries all use ``argklass`` in the same
process, use a :class:`ConfigContext` per library so their prefixes,
configs, and tracked options stay isolated:

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

import contextvars
import json
import os
import types
import typing
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import field, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterator, Type, TypeVar

T = TypeVar("T")


# ===================================================================
# Sentinels
# ===================================================================


class _Missing:
    """Sentinel distinguishing "no default given" from ``default=None``."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<missing>"

    def __bool__(self) -> bool:
        return False


#: Marker for "no default was supplied" -- an unresolvable option then raises.
MISSING = _Missing()

#: ContextVar default, distinct from ``None`` so a scope can deliberately mask
#: the process-wide config with "no config".
_UNSET = _Missing()


# ===================================================================
# Type introspection and coercion  (stateless)
# ===================================================================

_HINTS: dict[type, dict[str, Any]] = {}

_TRUE_VALUES = ("1", "true", "yes", "on")


def _resolve_field_types(cls) -> dict:
    """Resolved annotations for *cls*.

    ``from __future__ import annotations`` (PEP 563) turns every annotation in
    the defining module into a plain string, which would make ``is_dataclass``
    checks on ``field.type`` silently fail. Resolve them once, and fall back to
    the raw annotations when a forward reference cannot be resolved.
    """
    try:
        from typing import get_type_hints

        return get_type_hints(cls)
    except Exception:
        return {f.name: f.type for f in fields(cls)}


def _hints(cls: type) -> dict[str, Any]:
    """:func:`_resolve_field_types`, cached per class."""
    cached = _HINTS.get(cls)
    if cached is None:
        cached = _resolve_field_types(cls)
        _HINTS[cls] = cached
    return cached


def _is_union(origin: Any) -> bool:
    return origin is typing.Union or origin is types.UnionType


def _strip_optional(tp: Any) -> Any:
    """``X | None`` -> ``X``; anything else unchanged."""
    if _is_union(typing.get_origin(tp)):
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return tp


def _as_bool(value: Any) -> bool:
    """Lenient bool reading: anything outside the truthy set is ``False``."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_VALUES
    return bool(value)


def _type_name(tp: Any) -> str:
    """Printable name for a type, including typing generics."""
    if tp is None:
        return "Any"
    return getattr(tp, "__name__", None) or str(tp)


def _coerce(value: Any, tp: Any, where: str) -> Any:
    """Convert a raw file/env value to the type a field declares.

    Raises ``ValueError``/``TypeError`` when the value cannot be read as *tp*,
    naming *where* -- a bad value is a mistake worth reporting, not a silent
    ``None``.
    """
    if tp is None or tp is Any:
        return value

    origin = typing.get_origin(tp)
    args = typing.get_args(tp)

    if _is_union(origin):
        if value is None:
            return None
        for arg in args:
            if arg is type(None):
                continue
            try:
                return _coerce(value, arg, where)
            except (TypeError, ValueError):
                continue
        return value

    if is_dataclass(tp):
        if isinstance(value, tp):
            return value
        if isinstance(value, dict):
            return from_dict(tp, value, strict=True, where=where)
        raise TypeError(
            f"{where}: expected a mapping for {tp.__name__}, got {type(value).__name__}"
        )

    if isinstance(tp, type) and issubclass(tp, Enum):
        return value if isinstance(value, tp) else tp(value)

    if isinstance(tp, type) and issubclass(tp, Path):
        return value if isinstance(value, Path) else Path(value)

    if tp is bool:
        return _as_bool(value)

    if tp in (int, float, str):
        if type(value) is tp:
            return value
        try:
            return tp(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{where}: cannot read {value!r} as {tp.__name__}"
            ) from exc

    # A bare `list` / `tuple` / `set` / `dict` annotation: no element type to
    # apply, but the container itself still has to be the declared one -- YAML
    # gives a list for what a `tuple` field expects.
    if origin is None and tp in (list, tuple, set, frozenset, dict):
        return value if type(value) is tp else tp(value)

    if origin in (list, set, frozenset):
        elem = args[0] if args else None
        return origin(_coerce(v, elem, f"{where}[{i}]") for i, v in enumerate(value))

    if origin is tuple:
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(
                _coerce(v, args[0], f"{where}[{i}]") for i, v in enumerate(value)
            )
        if args:
            return tuple(
                _coerce(v, a, f"{where}[{i}]")
                for i, (v, a) in enumerate(zip(value, args))
            )
        return tuple(value)

    if origin is dict:
        kt, vt = (args[0], args[1]) if len(args) == 2 else (None, None)
        return {
            _coerce(k, kt, where): _coerce(v, vt, f"{where}.{k}")
            for k, v in value.items()
        }

    return value


def field_type(root_type: type, name: str) -> Any:
    """Declared type of the dotted *name* under *root_type*, or ``None``.

    Lets :meth:`ConfigContext.option` coerce an environment variable without
    being told the type at the call site.
    """
    tp: Any = root_type
    for frag in name.split("."):
        tp = _strip_optional(tp)
        if not (isinstance(tp, type) and is_dataclass(tp)):
            return None
        hints = _hints(tp)
        if frag not in hints:
            return None
        tp = hints[frag]
    return tp


# ===================================================================
# Dotted access  (stateless)
# ===================================================================


def _step(container: Any, frag: str, name: str) -> Any:
    if is_dataclass(container) and not isinstance(container, type):
        if frag in {f.name for f in fields(container)}:
            return getattr(container, frag)
    elif isinstance(container, dict) and frag in container:
        return container[frag]
    raise KeyError(f"{name}: no {frag!r} on {type(container).__name__}")


def get_path(obj: Any, name: str) -> Any:
    """Read a dotted *name* out of a config tree (dataclass or dict).

    Raises ``KeyError`` when any fragment is absent.
    """
    cursor = obj
    for frag in name.split("."):
        cursor = _step(cursor, frag, name)
    return cursor


def set_path(obj: Any, name: str, value: Any) -> None:
    """Write a dotted *name* into a config tree, coercing to the field's type."""
    *parents, leaf = name.split(".")
    cursor = obj
    for frag in parents:
        cursor = _step(cursor, frag, name)

    if is_dataclass(cursor) and not isinstance(cursor, type):
        if leaf not in {f.name for f in fields(cursor)}:
            raise KeyError(f"{name}: no {leaf!r} on {type(cursor).__name__}")
        hints = _hints(type(cursor))
        setattr(cursor, leaf, _coerce(value, hints.get(leaf), name))
    elif isinstance(cursor, dict):
        cursor[leaf] = value
    else:
        raise KeyError(f"{name}: cannot set {leaf!r} on {type(cursor).__name__}")


def flatten_dotted(data: dict, prefix: str = "") -> dict[str, Any]:
    """``{"a": {"b": 1}}`` -> ``{"a.b": 1}`` (leaves nested lists alone)."""
    flat: dict[str, Any] = {}
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and value:
            flat.update(flatten_dotted(value, path))
        else:
            flat[path] = value
    return flat


def parse_scalar(raw: str) -> Any:
    """Parse a command-line / env scalar the way YAML would, but numerically saner.

    PyYAML implements YAML 1.1, where ``1e-3`` is *not* a float (it wants
    ``1.0e-3``) -- which would quietly turn ``--set optim.lr=1e-3`` into the
    string ``"1e-3"`` for any field without a float annotation. Anything YAML
    leaves as a string gets one more int/float attempt here.
    """
    import yaml

    value = yaml.safe_load(raw)
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text)
        except ValueError:
            pass
        try:
            return float(text)
        except ValueError:
            pass
    return value


def parse_overrides(overrides: list[str] | tuple[str, ...]) -> dict[str, Any]:
    """Turn CLI ``key.path=value`` strings into a dotted dict.

    Values go through :func:`parse_scalar`, so ``lr=1e-3`` is a float,
    ``walls=false`` a bool and ``taus=[1, 7.5]`` a list. A value destined for a
    typed field is coerced again by :func:`set_path`, so a ``str`` field still
    receives a string.
    """
    parsed: dict[str, Any] = {}
    for item in overrides:
        if "=" not in item:
            raise ValueError(f"override {item!r} is not of the form key.path=value")
        key, _, raw = item.partition("=")
        parsed[key.strip()] = parse_scalar(raw)
    return parsed


def _split_option_args(etype: Any, default: Any) -> tuple[Any, Any]:
    """Allow both ``option(name, int, 42)`` and ``option(name, 42)``.

    The second positional is a *type* in the explicit form and a *default* in
    the annotation-driven one; anything that is not a type is read as a default
    -- ``None`` included, which is why the parameter itself defaults to
    :data:`MISSING` rather than ``None``.
    """
    if etype is MISSING:
        return None, default
    if isinstance(etype, type) or typing.get_origin(etype) is not None:
        return etype, default
    if default is not MISSING:
        raise TypeError(
            f"second argument {etype!r} is not a type, so it is read as the "
            f"default -- but a default {default!r} was also given"
        )
    return None, etype


# ===================================================================
# ConfigContext — all state lives here
# ===================================================================


class ConfigContext:
    """Isolated configuration namespace.

    Each context owns its own prefix, active config, and option tracker.
    Multiple libraries can coexist in the same process by using
    separate ``ConfigContext`` instances.

    The active config is either a plain dict or a dataclass instance, held in
    two layers -- process-wide (:meth:`set_config`) and context-local
    (:meth:`push`, :meth:`use`, :meth:`apply_config`, :meth:`overlay`).

    Parameters
    ----------
    prefix
        Environment-variable prefix (e.g. ``"MYLIB"`` →
        ``MYLIB_SOME_OPTION``).  A trailing underscore is added
        automatically if missing.
    config
        Initial config (dict or dataclass instance).  Can be replaced later
        with :meth:`set_config`.
    root_type
        Root config class, when it should be known before an instance exists.
    """

    def __init__(
        self, prefix: str = "", config: Any = None, root_type: type | None = None
    ):
        self._prefix = self._normalise_prefix(prefix)
        # Process-wide config: what every thread and task sees by default.
        self._default: Any = config
        # Context-local override, shadowing the process-wide one when set.
        self._var: contextvars.ContextVar[Any] = contextvars.ContextVar(
            f"argklass_config_{prefix or 'default'}", default=_UNSET
        )
        self._tracked: dict[str, dict] = {}
        self._root_type = root_type

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<ConfigContext prefix={self._prefix!r} root={_type_name(self.root_type)}>"
        )

    def __copy__(self) -> "ConfigContext":
        # A context is a service, not data: it owns a ContextVar (which can be
        # neither pickled nor deep-copied) and its identity is the whole point.
        # Anything copying a structure that references one -- overlay(), a
        # deepcopy of a Deferred -- keeps pointing at the same context.
        return self

    def __deepcopy__(self, memo: dict) -> "ConfigContext":
        return self

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

    # -- the active config -----------------------------------------------

    @property
    def root_type(self) -> type | None:
        """The config class in force: the active config's own type if it is a
        dataclass, else whatever :meth:`register` was given.

        Derived rather than stored, so a context-local config of a different
        class gets its own type used for env-var coercion instead of racing
        another context over one shared attribute.
        """
        cfg = self.get(required=False)
        if cfg is not None and is_dataclass(cfg) and not isinstance(cfg, type):
            return type(cfg)
        return self._root_type

    def register(self, cls: type) -> None:
        """Remember *cls* as the root config class, without instantiating one."""
        self._root_type = cls

    def set_config(self, config: Any) -> Any:
        """Install *config* process-wide, for every thread and task.

        Accepts a dict or a dataclass instance; ``None`` clears it. Returns the
        config it replaced. Use :meth:`push` or :meth:`use` instead when the
        override must stay inside one scope.
        """
        if is_dataclass(config) and not isinstance(config, type):
            self._root_type = type(config)
        previous = self._default
        self._default = config  # attribute assignment is atomic under the GIL
        return previous

    def push(self, config: Any) -> contextvars.Token:
        """Install *config* for the current context only.

        Shadows the process-wide config here without touching what any other
        thread or task sees. Returns a token for :meth:`reset`.
        """
        return self._var.set(config)

    def reset(self, token: contextvars.Token) -> None:
        """Undo a :meth:`push`."""
        self._var.reset(token)

    def clear(self) -> None:
        """Drop both layers -- the process-wide config and this context's
        override. Mostly for tests."""
        self._default = None
        self._var.set(_UNSET)

    def get(self, required: bool = False) -> Any:
        """The active config: context-local if this context has one, else the
        process-wide one.

        ``required=True`` raises when nothing is active instead of returning
        ``None``.
        """
        config = self._var.get()
        if config is _UNSET:
            config = self._default
        if config is None and required:
            raise RuntimeError(
                "no active config -- call set_config(cfg) or load_and_apply(path, cls=...) first"
            )
        return config

    def get_config(self) -> Any:
        """The active config, or ``{}`` when nothing is active."""
        config = self.get()
        return {} if config is None else config

    @contextmanager
    def use(self, config: Any):
        """Activate *config* for the duration of the block, in this context only."""
        token = self.push(config)
        try:
            yield config
        finally:
            self.reset(token)

    @contextmanager
    def apply_config(self, overrides: dict):
        """Temporarily merge *overrides* into the active config.

        A dict config is deep-merged; a dataclass config is deep-copied and the
        overrides written through :func:`set_path`, so the original instance is
        left alone. Either way the override is context-local and restored on
        exit.
        """
        active = self.get()
        if is_dataclass(active) and not isinstance(active, type):
            merged = deepcopy(active)
            for name, value in flatten_dotted(overrides).items():
                set_path(merged, name, value)
        else:
            merged = deepcopy(active) if active else {}
            _deep_merge(merged, overrides)

        token = self.push(merged)
        try:
            yield merged
        finally:
            self.reset(token)

    @contextmanager
    def overlay(self, values: dict[str, Any] | None = None, **kwargs: Any):
        """Activate a copy of the config with some *dotted* options overridden.

        Keyword form only reaches top-level fields (``lr=3e-4``); dotted keys
        go in the mapping (``{"optim.lr": 3e-4}``). The original is untouched.
        """
        merged = {**(values or {}), **kwargs}
        clone = deepcopy(self.get(required=True))
        for name, value in merged.items():
            set_path(clone, name, value)
        with self.use(clone) as config:
            yield config

    # -- option resolution -----------------------------------------------

    def option(self, name: str, etype: Any = MISSING, default: Any = MISSING) -> Any:
        """Resolve a single value: env → active config → *default*.

        ``option("db.port", int, 5432)`` states the type explicitly;
        ``option("db.port")`` and ``option("db.port", 5432)`` take it from the
        root config class's annotations. With no default at all, an
        unresolvable name raises ``KeyError``.
        """
        etype, default = _split_option_args(etype, default)
        return self._resolve(name, etype, default)

    def _resolve(self, name: str, etype: Any, default: Any) -> Any:
        """:meth:`option` with the arguments already split -- the entry point
        for callers that hold an explicit ``(etype, default)`` pair."""
        target = etype
        if target is None and self.root_type is not None:
            target = field_type(self.root_type, name)

        env_name = self.as_environment_variable(name)
        raw = os.getenv(env_name)
        if raw is not None:
            value = (
                _coerce(raw, target, env_name)
                if target is not None
                else parse_scalar(raw)
            )
            return self._track(name, target, default, value)

        config = self.get()
        if config is not None:
            try:
                value = get_path(config, name)
            except KeyError:
                value = MISSING
            if value is not MISSING:
                # A dict config holds raw file values, so coerce them. A
                # dataclass config was already coerced when it was built, and
                # its __post_init__ may deliberately hold a different runtime
                # type than the annotation -- re-coercing would hand back
                # something that is not what the instance actually holds.
                raw_container = not (
                    is_dataclass(config) and not isinstance(config, type)
                )
                if raw_container and value is not None and target is not None:
                    value = _coerce(value, target, name)
                return self._track(name, target, default, value)

        if default is MISSING:
            raise KeyError(
                f"option {name!r} is not set: no {env_name} environment variable, "
                f"and no default given. {self._no_config_hint()}"
            )
        return self._track(name, target, default, default)

    def _no_config_hint(self) -> str:
        """Why a lookup failed -- and the likeliest reason, when nothing is
        active at all."""
        if self.get() is None:
            return (
                "no config is active yet. If this runs at import time -- a "
                "function's default argument, a module-level constant -- it runs "
                "before the config is loaded, and whatever it returns is frozen "
                "there. Use configfield() for a dataclass field, or read the "
                "option inside the function body. "
            )
        return "not found in the active config. "

    def _track(self, name: str, etype: Any, default: Any, value: Any) -> Any:
        self._tracked[name] = {
            "type": etype,
            "default": None if default is MISSING else default,
            "value": value,
        }
        return value

    def set_option(self, name: str, value: Any) -> None:
        """Write a value into the active config, in place."""
        set_path(self.get(required=True), name, value)

    def section(
        self,
        name: str,
        cls: Type[T] | None = None,
        *,
        env: bool = True,
        default: Any = MISSING,
    ) -> T:
        """Resolve a whole sub-config at once, as a dataclass.

        Where :meth:`option` reads one value as the config holds it,
        ``section`` assembles a *component's* config: it hydrates a dict
        subtree into *cls* and applies the ``PREFIX_NAME_FIELD`` environment
        variables for the fields inside it. Hand the result straight to whatever
        takes that dataclass, instead of reading its fields one by one::

            loader = DataLoader(cfg_section := section("data", DataLoaderConfig))

        *cls* may be omitted when the subtree is already a dataclass instance;
        it is required for a dict subtree, and checked when both are present.

        The live instance is returned as-is unless an environment variable
        actually overrides one of its fields, in which case the result is a
        copy carrying that override -- reading a section never mutates the
        active config.

        Like :meth:`option`, a section the active config does not have raises
        ``KeyError`` unless a *default* is given -- ``section("data", Loader)``
        never quietly hands back a default-constructed ``Loader``. Ask for that
        explicitly with ``default=LoaderConfig()``.
        """
        config = self.get()
        value = MISSING
        if config is not None:
            try:
                value = get_path(config, name)
            except KeyError:
                value = MISSING

        if value is MISSING or value is None:
            if default is not MISSING:
                return default
            raise KeyError(
                f"section {name!r} is not set: {self._no_config_hint()}"
                f"pass default=... to fall back to one"
            )

        if is_dataclass(value) and not isinstance(value, type):
            if cls is not None and not isinstance(value, cls):
                raise TypeError(
                    f"section {name!r} holds {type(value).__name__}, not {cls.__name__}"
                )
            section = value
        elif isinstance(value, dict):
            if cls is None:
                raise TypeError(
                    f"section {name!r} is a mapping -- pass the dataclass to build: "
                    f"section({name!r}, MyConfig)"
                )
            section = from_dict(cls, value, strict=True, where=name)
        else:
            raise TypeError(
                f"section {name!r} is a {type(value).__name__}, not a config section"
            )

        if env:
            overrides = dict(self._env_overrides(type(section), name))
            if overrides:
                section = deepcopy(section)
                for subpath, raw in overrides.items():
                    set_path(section, subpath, raw)

        return section

    def _env_overrides(self, cls: type, prefix: str) -> Iterator[tuple[str, Any]]:
        """Yield ``(dotted_subpath, raw_value)`` for every environment variable
        that targets a field of *cls* (recursing into nested dataclasses)."""
        hints = _hints(cls)
        for f in fields(cls):
            ftype = _strip_optional(hints.get(f.name, f.type))
            subpath = f.name
            full = f"{prefix}.{f.name}" if prefix else f.name
            if isinstance(ftype, type) and is_dataclass(ftype):
                for nested, value in self._env_overrides(ftype, full):
                    yield f"{subpath}.{nested}", value
                continue
            raw = os.getenv(self.as_environment_variable(full))
            if raw is not None:
                yield subpath, _coerce(
                    raw, ftype, full
                ) if ftype is not None else parse_scalar(raw)

    def defer(self, name: str, etype: Any = MISSING, default: Any = MISSING) -> Any:
        """An :meth:`option` lookup to perform at call time. See :func:`defer`."""
        etype, default = _split_option_args(etype, default)
        return Deferred(name, etype, default, ctx=self)

    def defer_section(
        self,
        name: str,
        cls: type | None = None,
        *,
        env: bool = True,
        default: Any = MISSING,
    ) -> Any:
        """A :meth:`section` lookup to perform at call time. See
        :func:`defer_section`."""
        return Deferred(
            name, cls=cls, kind="section", env=env, default=default, ctx=self
        )

    def configfield(self, name: str, etype: Any = MISSING, default: Any = MISSING):
        """Dataclass field resolved via this context at instantiation time.

        Stores ``(name, etype, default, context)`` in the field metadata
        so introspection tools can enumerate all options.
        """
        etype, default = _split_option_args(etype, default)
        _ctx, _name, _etype, _default = self, name, etype, default

        return field(
            default_factory=lambda: _ctx._resolve(_name, _etype, _default),
            metadata={
                "_kind": "config",
                "_config_name": _name,
                "_config_type": _etype,
                "_config_default": _default,
                "_config_ctx": _ctx,
            },
        )

    # -- file I/O --------------------------------------------------------

    def load_config(
        self,
        cls: Type[T],
        filepath: str | dict,
        fmt: str | None = None,
        *,
        strict: bool = True,
        overrides: dict[str, Any] | list[str] | None = None,
        activate: bool = False,
    ) -> T:
        """Load a config file into a dataclass of *cls*.

        *filepath* may also be a mapping already in memory, which is handy for
        tests and for configs assembled in code.

        ``strict`` rejects keys no field declares, naming the file and the key.
        ``overrides`` accepts a dotted dict or CLI ``key=value`` strings, and is
        applied after the file. ``activate`` installs the result process-wide.
        """
        if isinstance(filepath, dict):
            data, where = filepath, ""
        else:
            data, where = _load_raw(filepath, fmt), str(filepath)
        config = from_dict(cls, data, strict=strict, where=where)
        return self._finish(config, overrides, activate)

    def _finish(self, config: T, overrides: Any, activate: bool) -> T:
        if overrides:
            if isinstance(overrides, (list, tuple)):
                overrides = parse_overrides(overrides)
            for name, value in overrides.items():
                set_path(config, name, value)
        if activate:
            self.set_config(config)
        return config

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

    def load_and_apply(
        self,
        filepath: str,
        fmt: str | None = None,
        *,
        cls: Type[T] | None = None,
        strict: bool = True,
        overrides: dict[str, Any] | list[str] | None = None,
    ) -> Any:
        """Load a config file and install it process-wide.

        Without *cls* the raw dict is applied (and returned). With *cls* the
        file is hydrated into that dataclass first, so ``option()`` then reads a
        typed, validated instance.
        """
        if cls is not None:
            return self.load_config(
                cls, filepath, fmt, strict=strict, overrides=overrides, activate=True
            )
        data = _load_raw(filepath, fmt)
        self._finish(data, overrides, activate=False)
        self.set_config(data)
        return data

    # -- introspection ---------------------------------------------------

    def config_fields(self, cls) -> Iterator[tuple[str, type, Any, str]]:
        """Yield ``(dotted_name, type, default, env_var)`` for every
        :meth:`configfield` in *cls* (including nested dataclasses).
        """
        hints = _hints(cls)
        for f in fields(cls):
            meta = f.metadata
            ftype = hints.get(f.name, f.type)
            if meta.get("_kind") == "config":
                name = meta["_config_name"]
                ctx = meta.get("_config_ctx", self)
                declared = meta["_config_type"] or _strip_optional(ftype)
                yield (
                    name,
                    declared,
                    meta["_config_default"],
                    ctx.as_environment_variable(name),
                )
            elif isinstance(ftype, type) and is_dataclass(ftype):
                yield from self.config_fields(ftype)

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
                "type": _type_name(etype),
                "default": None if default is MISSING else default,
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
                entry["value"] = entry["default"]

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
            if default is None or default is MISSING:
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
                    lines.append(f"{indent}# env: {env}  (type: {_type_name(et)})")
                    if dflt is None or dflt is MISSING:
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


def set_config(config: Any) -> Any:
    """Install *config* (dict or dataclass instance) process-wide."""
    return _default_ctx.set_config(config)


def get_config() -> Any:
    """The active config of the default context, or ``{}``."""
    return _default_ctx.get_config()


def push_config(config: Any) -> contextvars.Token:
    """Install *config* for the current context only; undo with
    :func:`reset_config`. Prefer :func:`use_config` where a block will do."""
    return _default_ctx.push(config)


def reset_config(token: contextvars.Token) -> None:
    """Undo a :func:`push_config`."""
    _default_ctx.reset(token)


def clear_config() -> None:
    """Drop the process-wide config and this context's override."""
    _default_ctx.clear()


def register_root(cls: type) -> None:
    """Remember *cls* as the root config class without instantiating one."""
    _default_ctx.register(cls)


def use_config(config: Any):
    """Context manager: activate *config* for the block, in this context only."""
    return _default_ctx.use(config)


@contextmanager
def apply_config(overrides: dict):
    """Temporarily merge *overrides* into the default context's config."""
    with _default_ctx.apply_config(overrides) as merged:
        yield merged


def overlay(values: dict[str, Any] | None = None, **kwargs: Any):
    """Context manager: a copy of the config with dotted options overridden."""
    return _default_ctx.overlay(values, **kwargs)


def option(name: str, etype: Any = MISSING, default: Any = MISSING) -> Any:
    """Resolve a value via the default context."""
    return _default_ctx.option(name, etype, default)


def set_option(name: str, value: Any) -> None:
    """Write a value into the active config, in place."""
    _default_ctx.set_option(name, value)


def section(
    name: str,
    cls: Type[T] | None = None,
    *,
    env: bool = True,
    default: Any = MISSING,
) -> T:
    """Resolve a whole sub-config at once, as a dataclass. See
    :meth:`ConfigContext.section`."""
    return _default_ctx.section(name, cls, env=env, default=default)


def configfield(name: str, etype: Any = MISSING, default: Any = MISSING):
    """Dataclass field resolved via the default context."""
    return _default_ctx.configfield(name, etype, default)


def load_config(
    cls: Type[T],
    filepath: str,
    fmt: str | None = None,
    *,
    strict: bool = True,
    overrides: dict[str, Any] | list[str] | None = None,
    activate: bool = False,
) -> T:
    """Load a config file and return a dataclass instance of *cls*."""
    return _default_ctx.load_config(
        cls, filepath, fmt, strict=strict, overrides=overrides, activate=activate
    )


def save_config(
    instance,
    filepath: str,
    fmt: str | None = None,
    *,
    skip_none: bool = False,
) -> None:
    """Serialize a dataclass instance to a config file."""
    _default_ctx.save_config(instance, filepath, fmt, skip_none=skip_none)


def load_and_apply(
    filepath: str,
    fmt: str | None = None,
    *,
    cls: Type[T] | None = None,
    strict: bool = True,
    overrides: dict[str, Any] | list[str] | None = None,
) -> Any:
    """Load a config file and install it as the default context's config."""
    return _default_ctx.load_and_apply(
        filepath, fmt, cls=cls, strict=strict, overrides=overrides
    )


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
# Deferred defaults
#
# Python evaluates a default argument expression once, when the `def`
# executes -- at import time, before any config is loaded. So
# `def train(lr=option("optim.lr", 1e-3))` does not read the config at
# call time: it captures whatever was resolvable at import and stays
# there forever, looking config-driven while ignoring the config.
#
# `defer()` records the lookup instead of performing it, and
# `@resolve_options` performs it on every call.
# ===================================================================


class Deferred:
    """A config lookup recorded now and performed at call time.

    Produced by :func:`defer` / :func:`defer_section`, resolved by the
    :func:`resolve_options` decorator. Reaching one of these at runtime means
    the decorator is missing -- so attribute access and numeric conversion say
    so rather than failing obscurely.
    """

    __slots__ = ("name", "etype", "default", "cls", "kind", "env", "ctx")

    def __init__(
        self,
        name: str,
        etype: Any = None,
        default: Any = MISSING,
        *,
        cls: type | None = None,
        kind: str = "option",
        env: bool = True,
        ctx: "ConfigContext | None" = None,
    ):
        self.name = name
        self.etype = etype
        self.default = default
        self.cls = cls
        self.kind = kind
        self.env = env
        self.ctx = ctx

    def resolve(self) -> Any:
        """Perform the lookup now, against the currently active config."""
        ctx = self.ctx if self.ctx is not None else _default_ctx
        if self.kind == "section":
            return ctx.section(self.name, self.cls, env=self.env, default=self.default)
        return ctx._resolve(self.name, self.etype, self.default)

    def __repr__(self) -> str:
        what = "section" if self.kind == "section" else "option"
        return f"<deferred {what} {self.name!r}; resolved by @resolve_options>"

    def _unresolved(self, what: str) -> RuntimeError:
        return RuntimeError(
            f"{self!r} was used as {what} without being resolved -- decorate the "
            f"function with @resolve_options, or call .resolve() yourself"
        )

    def __getattr__(self, item: str) -> Any:
        # Dunder lookups (copy, pickle, ...) must keep raising AttributeError so
        # the protocols they belong to can fall back normally.
        if item.startswith("__") and item.endswith("__"):
            raise AttributeError(item)
        raise self._unresolved(f"a config object (.{item})")

    def __float__(self) -> float:
        raise self._unresolved("a number")

    def __int__(self) -> int:
        raise self._unresolved("a number")

    def __index__(self) -> int:
        raise self._unresolved("an index")

    def __iter__(self):
        raise self._unresolved("an iterable")


def defer(name: str, etype: Any = MISSING, default: Any = MISSING) -> Any:
    """An :func:`option` lookup to perform at call time, not at import time.

    Use it for a function's default argument, and decorate the function with
    :func:`resolve_options`::

        @resolve_options
        def train(lr=defer("optim.lr"), steps=defer("steps", 1000)):
            ...

    ``train()`` then reads the config as it is *when called*; ``train(lr=1e-5)``
    still wins. Takes the same arguments as :func:`option`, and resolves against
    the default context -- use :meth:`ConfigContext.defer` for another one.
    """
    return _default_ctx.defer(name, etype, default)


def defer_section(
    name: str,
    cls: type | None = None,
    *,
    env: bool = True,
    default: Any = MISSING,
) -> Any:
    """A :func:`section` lookup to perform at call time. See :func:`defer`::

    @resolve_options
    def build_loader(cfg=defer_section("data", LoaderConfig)):
        ...
    """
    return _default_ctx.defer_section(name, cls, env=env, default=default)


def resolve_options(fn):
    """Resolve every :func:`defer` default of *fn* on each call.

    Arguments the caller passes explicitly are left alone, so a deferred default
    behaves like any other default -- except that it reads the config at call
    time instead of at import time.
    """
    import functools
    import inspect

    signature = inspect.signature(fn)
    deferred = [
        name
        for name, p in signature.parameters.items()
        if isinstance(p.default, Deferred)
    ]
    if not deferred:
        raise TypeError(
            f"@resolve_options: {fn.__qualname__} has no defer()/defer_section() "
            f"defaults to resolve"
        )

    def _resolved(args, kwargs):
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        for name, value in bound.arguments.items():
            if isinstance(value, Deferred):
                bound.arguments[name] = value.resolve()
        return bound

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            bound = _resolved(args, kwargs)
            return await fn(*bound.args, **bound.kwargs)

        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        bound = _resolved(args, kwargs)
        return fn(*bound.args, **bound.kwargs)

    return wrapper


# ===================================================================
# Pure helpers (no state)
# ===================================================================


def _getenv(name: str, expected_type: type):
    """Read an environment variable and coerce to *expected_type*."""
    raw = os.getenv(name)
    if raw is None:
        return None
    return _coerce(raw, expected_type, name)


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
    """Recursively convert a dataclass instance to plain, serializable data.

    ``Path`` becomes ``str``, ``Enum`` its value, and tuples/sets become lists,
    so the result survives a YAML/JSON round trip.
    """
    if not is_dataclass(instance) or isinstance(instance, type):
        return _convert_value(instance, skip_none=skip_none)

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
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return _convert_value(value.value, skip_none=skip_none)
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_convert_value(v, skip_none=skip_none) for v in value]
    if isinstance(value, dict):
        return {k: _convert_value(v, skip_none=skip_none) for k, v in value.items()}
    return value


def from_dict(cls: Type[T], data: dict, *, strict: bool = False, where: str = "") -> T:
    """Create a dataclass instance from a plain dict (recursive).

    Values are coerced to each field's annotated type. ``strict`` turns a key
    that no field declares into an error naming *where* and the key -- the
    cheapest way to catch a misspelled option before a long run. It stays off by
    default here (a dict may legitimately carry extra keys); file loading turns
    it on.
    """
    if not is_dataclass(cls) or not isinstance(data, dict):
        return data  # type: ignore[return-value]

    hints = _hints(cls)
    known = {f.name for f in fields(cls) if f.init}

    if strict:
        unknown = [k for k in data if k not in known]
        if unknown:
            prefix = f"{where}: " if where else ""
            raise ValueError(
                f"{prefix}unknown {cls.__name__} option(s) {sorted(unknown)} "
                f"-- known: {sorted(known)}"
            )

    kwargs: dict = {}
    for f in fields(cls):
        if f.name not in data or f.name not in known:
            continue
        path = f"{where}.{f.name}" if where else f.name
        kwargs[f.name] = _coerce(data[f.name], hints.get(f.name, f.type), path)

    return cls(**kwargs)


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

            data = yaml.safe_load(fh) or {}
        elif fmt == "json":
            data = json.load(fh)
        elif fmt == "hjson":
            import hjson

            data = hjson.load(fh)
        else:
            raise ValueError(f"Unknown config format: {fmt!r}")

    if not isinstance(data, dict):
        raise ValueError(f"{filepath}: expected a mapping at the top level")
    return data


def _dump_raw(data: dict, filepath: str, fmt: str | None = None) -> None:
    fmt = fmt or _detect_format(filepath)
    parent = os.path.dirname(os.path.abspath(filepath))
    os.makedirs(parent, exist_ok=True)
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
