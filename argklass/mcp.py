"""Generate an MCP server from an argklass CLI definition.

Traverses the argparser tree built by ``CommandLineInterface`` to discover all
leaf commands, converts their argparse actions to JSON Schema tool definitions,
and exposes them as MCP tools.

Requires the ``mcp`` package::

    pip install "argklass[mcp]"

Usage
-----

.. code-block:: python

    import mycommands
    from argklass.mcp import create_mcp_server

    server = create_mcp_server(mycommands, name="my-tools")
    server.run()
"""

from __future__ import annotations

import argparse
import io
import threading
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from typing import Any, List

from .arguments import argument


# ---------------------------------------------------------------------------
# Type mapping
# ---------------------------------------------------------------------------


_PYTHON_TYPE_TO_JSON = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _type_to_json_schema(python_type) -> dict:
    """Map a Python type to a JSON Schema type descriptor."""
    name = _PYTHON_TYPE_TO_JSON.get(python_type)
    if name:
        return {"type": name}
    if python_type is None:
        return {"type": "string"}
    return {"type": "string"}


# ---------------------------------------------------------------------------
# argparse action → JSON Schema property
# ---------------------------------------------------------------------------


def _action_to_property(action: argparse.Action):
    """Convert an argparse *Action* to a ``(dest, property_dict)`` pair.

    Returns ``(None, None)`` for actions that should be skipped (help,
    subparsers, version, …).
    """
    skip_types = (
        argparse._HelpAction,
        argparse._SubParsersAction,
        argparse._VersionAction,
    )
    if isinstance(action, skip_types):
        return None, None

    prop: dict[str, Any] = {}

    # Boolean flags
    if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
        prop["type"] = "boolean"
        prop["default"] = action.default
        if action.help:
            prop["description"] = action.help
        return action.dest, prop

    # Regular argument – determine base type
    if action.type is not None:
        prop.update(_type_to_json_schema(action.type))
    else:
        prop["type"] = "string"

    # nargs → array wrapping
    if action.nargs in ("+", "*"):
        inner_type = prop.get("type", "string")
        prop = {"type": "array", "items": {"type": inner_type}}
        if action.nargs == "+":
            prop["minItems"] = 1

    # Choices → enum constraint
    if action.choices and not isinstance(action.choices, dict):
        choices = []
        for c in action.choices:
            if hasattr(c, "value"):
                choices.append(c.value)
            else:
                choices.append(c)
        prop["enum"] = choices

    if action.help:
        prop["description"] = action.help

    if action.default is not None and action.default is not argparse.SUPPRESS:
        try:
            if hasattr(action.default, "value"):
                prop["default"] = action.default.value
            else:
                prop["default"] = action.default
        except (TypeError, ValueError):
            pass

    return action.dest, prop


# ---------------------------------------------------------------------------
# Parser → JSON Schema + metadata
# ---------------------------------------------------------------------------


@dataclass
class ArgMeta:
    """Metadata needed to reconstruct CLI argv for a single argument."""

    dest: str
    positional: bool = False
    store_true: bool = False
    store_false: bool = False


@dataclass
class ToolDef:
    """Everything needed to register and invoke one MCP tool."""

    name: str
    description: str
    schema: dict
    argv_prefix: list[str]
    arg_metas: list[ArgMeta] = field(default_factory=list)


def _parser_to_schema(parser: argparse.ArgumentParser):
    """Extract a JSON Schema *inputSchema* and arg metadata from *parser*.

    Walks all argument groups reachable from *parser*, including those
    created by argklass in flat mode (Python 3.14+).  Groups annotated
    with the ``_dataclass`` attribute by :func:`add_arguments` are
    followed regardless of whether they are physically nested or
    siblings on the root parser.
    """
    properties: dict[str, dict] = {}
    required: list[str] = []
    metas: list[ArgMeta] = []
    visited: set[int] = set()

    def _visit_group(group):
        gid = id(group)
        if gid in visited:
            return
        visited.add(gid)

        for action in group._group_actions:
            dest, prop = _action_to_property(action)
            if dest is None:
                continue

            properties[dest] = prop

            meta = ArgMeta(dest=dest)

            is_positional = not action.option_strings
            has_default = (
                action.default is not None and action.default is not argparse.SUPPRESS
            )

            if isinstance(action, argparse._StoreTrueAction):
                meta.store_true = True
            elif isinstance(action, argparse._StoreFalseAction):
                meta.store_false = True

            if is_positional:
                meta.positional = True
                if not has_default:
                    required.append(dest)
            elif getattr(action, "required", False):
                required.append(dest)

            metas.append(meta)

        for nested in getattr(group, "_action_groups", []):
            _visit_group(nested)

    for group in parser._action_groups:
        _visit_group(group)

    if not properties:
        return {}, metas

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required

    return schema, metas


# ---------------------------------------------------------------------------
# Recursive extraction of leaf commands
# ---------------------------------------------------------------------------


def _has_subparsers(parser: argparse.ArgumentParser) -> bool:
    for group in parser._action_groups:
        for action in group._group_actions:
            if isinstance(action, argparse._SubParsersAction):
                return True
    return False


def _extract_tools(
    parser: argparse.ArgumentParser,
    prefix_parts: list[str] | None = None,
) -> list[ToolDef]:
    """Walk the parser tree and return a :class:`ToolDef` for every leaf command."""
    if prefix_parts is None:
        prefix_parts = []

    tools: list[ToolDef] = []

    for group in parser._action_groups:
        for action in group._group_actions:
            if not isinstance(action, argparse._SubParsersAction):
                continue

            for name, subparser in sorted(action.choices.items()):
                new_prefix = prefix_parts + [name]

                if _has_subparsers(subparser):
                    tools.extend(_extract_tools(subparser, new_prefix))
                else:
                    tool_name = "_".join(new_prefix)
                    description = (subparser.description or "").strip()
                    schema, arg_metas = _parser_to_schema(subparser)
                    tools.append(
                        ToolDef(
                            name=tool_name,
                            description=description,
                            schema=schema,
                            argv_prefix=list(new_prefix),
                            arg_metas=arg_metas,
                        )
                    )

    return tools


# ---------------------------------------------------------------------------
# Reconstruct CLI argv from MCP arguments
# ---------------------------------------------------------------------------


def _build_argv(
    tool: ToolDef,
    arguments: dict[str, Any],
) -> list[str]:
    """Turn an MCP arguments dict back into a CLI argv list."""
    argv = list(tool.argv_prefix)

    positional_order = [m.dest for m in tool.arg_metas if m.positional]
    bool_map = {
        m.dest: m for m in tool.arg_metas if m.store_true or m.store_false
    }

    # Positional arguments first (in definition order)
    for dest in positional_order:
        if dest not in arguments:
            continue
        value = arguments[dest]
        if isinstance(value, list):
            argv.extend(str(v) for v in value)
        else:
            argv.append(str(value))

    # Named / optional arguments
    for key, value in arguments.items():
        if key in positional_order:
            continue

        flag = f"--{key}"

        if key in bool_map:
            meta = bool_map[key]
            if meta.store_true and value is True:
                argv.append(flag)
            elif meta.store_false and value is False:
                argv.append(flag)
            continue

        if isinstance(value, bool):
            if value:
                argv.append(flag)
            continue

        if isinstance(value, list):
            argv.append(flag)
            argv.extend(str(v) for v in value)
        else:
            argv.append(flag)
            argv.append(str(value))

    return argv


# ---------------------------------------------------------------------------
# MCPServer
# ---------------------------------------------------------------------------


class MCPServer:
    """An MCP server that exposes argklass CLI commands as tools.

    Parameters
    ----------
    name:
        Human-readable server name.
    cli:
        A :class:`~argklass.cli.CommandLineInterface` instance whose parser
        tree will be traversed to discover tools.
    """

    def __init__(self, name: str, cli):
        self.name = name
        self._clis: list = [cli]
        self.tools: list[ToolDef] = _extract_tools(cli.parser)
        self._tool_map: dict[str, ToolDef] = {t.name: t for t in self.tools}
        self._tool_cli: dict[str, Any] = {t.name: cli for t in self.tools}
        self._lock = threading.Lock()

    @property
    def cli(self):
        """The first (or only) CLI.  Kept for backward compatibility."""
        return self._clis[0]

    def add_module(self, module, prefix: str | None = None, **cli_kwargs):
        """Add tools from another CLI module to this server.

        Parameters
        ----------
        module:
            A Python module whose sub-modules export ``COMMANDS``.
        prefix:
            Optional prefix prepended to tool names to avoid collisions
            (e.g. ``"bench"`` turns ``run`` into ``bench_run``).
        **cli_kwargs:
            Forwarded to :class:`~argklass.cli.CommandLineInterface`.
        """
        from .cli import CommandLineInterface

        cli = CommandLineInterface(module, **cli_kwargs)
        self._clis.append(cli)

        new_tools = _extract_tools(cli.parser)

        for tool in new_tools:
            if prefix:
                tool = ToolDef(
                    name=f"{prefix}_{tool.name}",
                    description=tool.description,
                    schema=tool.schema,
                    argv_prefix=tool.argv_prefix,
                    arg_metas=tool.arg_metas,
                )
            if tool.name in self._tool_map:
                raise ValueError(
                    f"Duplicate tool name {tool.name!r}. "
                    "Use the 'prefix' parameter to disambiguate."
                )
            self.tools.append(tool)
            self._tool_map[tool.name] = tool
            self._tool_cli[tool.name] = cli

    # -- direct invocation (useful for testing) --

    def call(self, tool_name: str, arguments: dict[str, Any] | None = None) -> str:
        """Invoke a tool synchronously and return its text output."""
        arguments = arguments or {}
        tool = self._tool_map.get(tool_name)
        if tool is None:
            known = ", ".join(sorted(self._tool_map))
            raise ValueError(
                f"Unknown tool {tool_name!r}. Available: {known}"
            )

        cli = self._tool_cli[tool_name]
        argv = _build_argv(tool, arguments)

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        with self._lock:
            with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                try:
                    result = cli.run(argv)
                except SystemExit as exc:
                    code = exc.code
                    if isinstance(code, str):
                        stderr_buf.write(f"{code}\n")
                        result = 1
                    else:
                        result = code
                except KeyboardInterrupt:
                    stderr_buf.write("KeyboardInterrupt\n")
                    result = 130
                except Exception as exc:
                    stderr_buf.write(f"{type(exc).__name__}: {exc}\n")
                    result = 1

        parts: list[str] = []

        out = stdout_buf.getvalue()
        if out:
            parts.append(out.rstrip())

        err = stderr_buf.getvalue()
        if err:
            parts.append(f"stderr:\n{err.rstrip()}")

        if isinstance(result, int) and result not in (None, 0):
            parts.append(f"exit code: {result}")

        return "\n".join(parts) if parts else "Done."

    # -- MCP server lifecycle --

    def run(self, transport: str = "stdio", host: str = "127.0.0.1", port: int = 8000):
        """Start the MCP server.

        Parameters
        ----------
        transport:
            ``"stdio"`` (default), ``"sse"``, or ``"streamable-http"``.
        host:
            Bind address for SSE / streamable-http (default ``"127.0.0.1"``).
        port:
            Port for SSE / streamable-http (default ``8000``).
        """
        import asyncio

        asyncio.run(self._serve(transport, host, port))

    async def _serve(self, transport: str, host: str = "127.0.0.1", port: int = 8000):
        try:
            from mcp.server.lowlevel import Server as _Server
        except ImportError:
            try:
                from mcp.server import Server as _Server
            except ImportError:
                raise ImportError(
                    "The 'mcp' package is required. "
                    "Install it with:  pip install 'argklass[mcp]'"
                ) from None

        from mcp.types import TextContent, Tool

        server = _Server(self.name)
        tool_map = self._tool_map
        call = self.call

        @server.list_tools()
        async def _list_tools():
            return [
                Tool(
                    name=t.name,
                    description=t.description or t.name,
                    inputSchema=t.schema,
                )
                for t in self.tools
            ]

        @server.call_tool()
        async def _call_tool(name: str, arguments: dict | None = None):
            import asyncio

            loop = asyncio.get_running_loop()
            text = await loop.run_in_executor(
                None, call, name, arguments or {}
            )
            return [TextContent(type="text", text=text)]

        if transport == "stdio":
            import sys

            if not hasattr(sys.stdout, "buffer") or not hasattr(
                sys.stdout.buffer, "readable"
            ):
                raise RuntimeError(
                    "stdio transport requires real stdin/stdout file "
                    "descriptors (used when an MCP client spawns this "
                    "process). For interactive use, try:\n"
                    "  --transport sse        (HTTP + Server-Sent Events)\n"
                    "  --transport streamable-http"
                )

            from mcp.server.stdio import stdio_server

            async with stdio_server() as streams:
                read_stream, write_stream = streams
                init_opts = server.create_initialization_options()
                await server.run(read_stream, write_stream, init_opts)

        elif transport == "sse":
            await self._serve_sse(server, host, port)

        elif transport == "streamable-http":
            await self._serve_streamable_http(server, host, port)

        else:
            raise ValueError(
                f"Unsupported transport: {transport!r}. "
                "Use 'stdio', 'sse', or 'streamable-http'."
            )

    async def _serve_sse(self, server, host: str, port: int):
        try:
            from mcp.server.sse import SseServerTransport
            from starlette.applications import Starlette
            from starlette.responses import Response
            from starlette.routing import Mount, Route
            import uvicorn
        except ImportError as exc:
            raise ImportError(
                "SSE transport requires 'starlette' and 'uvicorn'. "
                "Install them with:  pip install starlette uvicorn"
            ) from exc

        sse = SseServerTransport("/messages/")

        async def handle_sse(request):
            async with sse.connect_sse(
                request.scope, request.receive, request._send,
            ) as streams:
                await server.run(
                    streams[0], streams[1],
                    server.create_initialization_options(),
                )
            return Response()

        app = Starlette(
            routes=[
                Route("/sse", endpoint=handle_sse),
                Mount("/messages/", app=sse.handle_post_message),
            ],
        )
        config = uvicorn.Config(app, host=host, port=port)
        uv_server = uvicorn.Server(config)
        await uv_server.serve()

    async def _serve_streamable_http(self, server, host: str, port: int):
        try:
            from mcp.server.streamable_http import StreamableHTTPServerTransport
            from starlette.applications import Starlette
            from starlette.routing import Mount
            import uvicorn
        except ImportError as exc:
            raise ImportError(
                "Streamable HTTP transport requires 'starlette' and 'uvicorn'. "
                "Install them with:  pip install starlette uvicorn"
            ) from exc

        import anyio

        transport = StreamableHTTPServerTransport(mcp_session_id=None)

        async def run_server():
            async with transport.connect() as (read_stream, write_stream):
                await server.run(
                    read_stream, write_stream,
                    server.create_initialization_options(),
                )

        app = Starlette(
            routes=[Mount("/mcp", app=transport.handle_request)],
        )
        config = uvicorn.Config(app, host=host, port=port, log_config=None)
        uv_server = uvicorn.Server(config)

        async with anyio.create_task_group() as tg:
            tg.start_soon(run_server)
            await uv_server.serve()
            tg.cancel_scope.cancel()


# ---------------------------------------------------------------------------
# High-level entry point
# ---------------------------------------------------------------------------


def create_mcp_server(
    module,
    *extra_modules,
    name: str | None = None,
    prefix: bool = False,
    **cli_kwargs,
) -> MCPServer:
    """Create an :class:`MCPServer` from one or more CLI modules.

    Parameters
    ----------
    module:
        A Python module (package) whose sub-modules export ``COMMANDS``.
    *extra_modules:
        Additional modules to merge into the same server.
    name:
        Server name shown to MCP clients.  Defaults to *module.__name__*.
    prefix:
        When ``True`` and multiple modules are provided, each module's
        tools are prefixed with the module name to avoid collisions
        (e.g. ``mypackage_run``).  Has no effect with a single module.
    **cli_kwargs:
        Forwarded to :class:`~argklass.cli.CommandLineInterface`
        (e.g. ``prog="mytool"``).

    Returns
    -------
    MCPServer
        Ready to ``.run()`` or to inspect via ``.tools``.

    Examples
    --------

    .. code-block:: python

        import mycommands
        from argklass.mcp import create_mcp_server

        server = create_mcp_server(mycommands, name="my-tools")

        # inspect discovered tools
        for tool in server.tools:
            print(tool.name, tool.schema)

        # run as stdio MCP server
        server.run()

    Multiple modules:

    .. code-block:: python

        import pkg_a, pkg_b
        server = create_mcp_server(pkg_a, pkg_b, prefix=True)
    """
    from .cli import CommandLineInterface

    if name is None:
        name = getattr(module, "__name__", "argklass-mcp")

    cli = CommandLineInterface(module, **cli_kwargs)
    server = MCPServer(name, cli)

    for mod in extra_modules:
        mod_prefix = getattr(mod, "__name__", "").rsplit(".", 1)[-1] if prefix else None
        server.add_module(mod, prefix=mod_prefix)

    return server


# ---------------------------------------------------------------------------
# python -m argklass.mcp <module> [--name NAME] [--transport TRANSPORT]
# ---------------------------------------------------------------------------


@dataclass
class MCPArgs:
    """Run an MCP server from one or more argklass CLI modules."""

    modules: List[str] = argument()  # Dotted import path(s) of CLI module(s)
    transport: str = argument(default="stdio", choices=["stdio", "sse", "streamable-http"])  # MCP transport protocol
    name: str = None  # Server name shown to MCP clients
    host: str = "127.0.0.1"  # Bind address for sse/streamable-http
    port: int = 8000  # Port for sse/streamable-http
    no_prefix: bool = False  # Don't prefix tool names when using multiple modules


def _main():
    import importlib

    from .arguments import parse

    args = parse(MCPArgs, prog="python -m argklass.mcp")

    modules = [importlib.import_module(m) for m in args.modules]
    use_prefix = len(modules) > 1 and not args.no_prefix
    server = create_mcp_server(
        modules[0], *modules[1:], name=args.name, prefix=use_prefix,
    )
    server.run(
        transport=args.transport,
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    _main()
