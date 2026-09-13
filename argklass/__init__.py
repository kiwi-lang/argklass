__descr__ = "Argparse utility"
__license__ = "BSD 3-Clause License"
__author__ = "Pierre Delaunay"
__author_email__ = "pierre@delaunay.io"
__copyright__ = "2023 Pierre Delaunay"
__url__ = "https://github.com/kiwi-lang/argklass"

try:
    from ._version import version as __version__
except ImportError:
    try:
        from importlib.metadata import version

        __version__ = version("argklass")
    except Exception:
        __version__ = "0.0.0"


from .arguments import ArgumentParser, argument, choice, group, subparsers
from .sysconfig import (
    MISSING,
    ConfigContext,
    Deferred,
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

__all__ = [
    "argument",
    "ArgumentParser",
    "group",
    "subparsers",
    "choice",
    "MISSING",
    "ConfigContext",
    "Deferred",
    "apply_config",
    "as_environment_variable",
    "clear_config",
    "config_fields",
    "config_template",
    "configfield",
    "defer",
    "defer_section",
    "env_template",
    "field_type",
    "from_dict",
    "get_config",
    "get_path",
    "load_and_apply",
    "load_config",
    "option",
    "overlay",
    "overrides_snapshot",
    "parse_overrides",
    "parse_scalar",
    "push_config",
    "register_root",
    "reset_config",
    "resolve_options",
    "save_config",
    "section",
    "set_config",
    "set_env_prefix",
    "set_option",
    "set_path",
    "show_config",
    "to_dict",
    "tracked_options",
    "use_config",
    "create_mcp_server",
]


def create_mcp_server(module, name=None, **cli_kwargs):
    """Create an MCP server from a module that defines argklass commands.

    Lazy import so the ``mcp`` package is only required when actually used.
    See :func:`argklass.mcp.create_mcp_server` for full documentation.
    """
    from .mcp import create_mcp_server as _create

    return _create(module, name=name, **cli_kwargs)
