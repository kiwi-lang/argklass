argklass
========

|pypi| |py_versions| |codecov| |docs| |tests| |style|

.. |pypi| image:: https://img.shields.io/pypi/v/argklass.svg
    :target: https://pypi.python.org/pypi/argklass
    :alt: Current PyPi Version

.. |py_versions| image:: https://img.shields.io/pypi/pyversions/argklass.svg
    :target: https://pypi.python.org/pypi/argklass
    :alt: Supported Python Versions

.. |codecov| image:: https://codecov.io/gh/kiwi-lang/argklass/branch/master/graph/badge.svg?token=40Cr8V87HI
   :target: https://codecov.io/gh/kiwi-lang/argklass

.. |docs| image:: https://readthedocs.org/projects/argklass/badge/?version=latest
   :target:  https://argklass.readthedocs.io/en/latest/?badge=latest

.. |tests| image:: https://github.com/kiwi-lang/argklass/actions/workflows/test.yml/badge.svg
   :target: https://github.com/kiwi-lang/argklass/actions/workflows/test.yml

.. |style| image:: https://github.com/kiwi-lang/argklass/actions/workflows/style.yml/badge.svg?branch=master
   :target: https://github.com/kiwi-lang/argklass/actions/workflows/style.yml


Inspired by `Simple Parsing <https://github.com/lebrice/SimpleParsing>`_, simplified
and extended to build extensive, extendable command line interface without much code.


.. code-block:: bash

   pip install argklass


Features
--------

* Automatic cli discovery and command plugin

   .. code-block:: text

      # Folder structure
      project/cli/
      ├── __init__.py         <= empty
      ├── editor/
      │   ├── __init__.py     <= ParentCommand(editor)
      │   ├── cook.py         <= Command(cook)
      │   ├── client.py       <= Command(client)
      │   ├── game.py         <= Command(game)
      │   └── open.py         <= Command(open)
      └── uat/
         ├── __init__.py     <= ParentCommand(uat)
         ├── localize.py     <= Command(localize)
         └── test.py         <= Command(test)

      #  editor/__init__.py
      from argklass.command import ParentCommand

      class Editor(ParentCommand):
         name = "editor"


      COMMANDS = Editor

      # cook.py
      from argklass.command import Command

      class Cook(Command):
         name = "cook"

         @staticmethod
         def execute(args) -> int:
            print("cook")

      COMMANDS = Cook

      #
      cli = CommandLineInterface(project.cli)
      cli.run()

      # or
      cli.run(["editor", "cook", "--help"])

      #
      cli editor cook --help
      cli uat localize --help


* New Argument format
   * able to show the entire command line interface with all its subparsers
   * new format mirror dataclass syntax

   .. code-block:: text

      editor                                           Set of commands to launch the editors in different modes
         server                                       Parameters added to the Map URL
         game                                         docstring ...
         client                                       docstring ...
         resavepackages                               docstring ...
         cook                                         docstring ...
         ml                                           Launch unreal engine with mladapter setup
         editor                                       Other arguments
         open                                         docstring ...
         localize                                     docstring ...
         worldpartition                               Convert a UE4 map using world partition
         -h, --help                                   Show help
      engine                                           Set of commands to manage engine installation/source
            add                                          docstring ...
            update                                       Update the engine source code
      format                                             docstring ...
            --profile: str                               docstring ...
            --file: str                                  docstring ...
            --fail_on_error: bool = False                docstring ...
            --col: int = 24                              docstring ...

* Compact argparse definition

   .. code-block:: python

      def workdir():
         d = os.getcwd()
         if os.access(d, os.W_OK):
            return d
         return None


      @dataclass
      class MyArguments:
         a  : str                                                    # Positional
         b  : int                = 20                                # My argument
         c  : bool               = False                             # My argument
         d  : int                = choice(0, 1, 2, 3, 4, default=1)  # choices
         e  : List[int]          = argument(default=[0])             # list
         f  : Optional[int]      = None                              # Optional
         p  : Tuple[int, int]    = (1, 1)                            # help p
         g  : Color              = Color.RED                         # help g
         s  : SubArgs            = SubArgs                           # helps group
         cmd: Union[cmd1, cmd2]  = subparsers(cmd1=cmd1, cmd2=cmd2)  # Command subparser
         de : str                = deduceable(workdir)

      parser = ArgumentParser()
      parser.add_arguments(MyArguments)
      args = parser.parse_args()

* Save and load arguments from configuration files

   .. code-block:: python

      parser = build_parser(commands)

      # load/save defaults before parsing
      save_defaults(parser, "config.hjson")
      apply_defaults(parser, "config.hjson")

      args = parser.parse_args(["editor", "editor"])

      # load save arguments after parsing
      save_as_config(parser, args, "dump.hjson")
      apply_config(parser, args, "dump.hjson")

* Lower level interface, that gives you back all of argparse power

   .. code-block:: python

      @dataclass
      class SubArgs:
         aa: str = argument(default="123")


      @dataclass
      class cmd1:
         args: str = "str1"


      @dataclass
      class cmd2:
         args: str = "str2"


      @dataclass
      class MyArguments:
         a: str                  = argument(help="Positional")
         b: int                  = argument(default=20, help="My argument")
         c: bool                 = argument(action="store_true", help="My argument")
         d: int                  = argument(default=1, choices=[0, 1, 2, 3, 4], help="choices")
         e: List[int]            = argument(default=[0], help="list")
         f: Optional[int]        = argument(default=None, help="Optional")
         p: Tuple[int, int]      = argument(default=(1, 1), help="help p")
         g: Color                = argument(default=Color.RED, help="help g")
         s: SubArgs              = group(default=SubArgs, help="helps group")
         cmd: Union[cmd1, cmd2]  = subparsers(cmd1=cmd1, cmd2=cmd2)


      parser = ArgumentParser()
      parser.add_arguments(MyArguments)
      args = parser.parse_args()


MCP Server
----------

argklass can expose your CLI commands as `MCP <https://modelcontextprotocol.io/>`_ tools,
letting AI agents call them directly.

.. code-block:: bash

   pip install "argklass[mcp]"

Quick start
^^^^^^^^^^^

The fastest way to run an MCP server is the built-in entry point — just point
it at your CLI module:

.. code-block:: bash

   # stdio (default) — for MCP clients that spawn the process
   python -m argklass.mcp mypackage.cli

   # SSE — for testing or web-based clients
   python -m argklass.mcp mypackage.cli --transport sse

   # Streamable HTTP — the newest MCP transport
   python -m argklass.mcp mypackage.cli --transport streamable-http

   # Custom host/port/name
   python -m argklass.mcp mypackage.cli --transport sse --host 0.0.0.0 --port 9000 --name my-tools

Programmatic usage
^^^^^^^^^^^^^^^^^^

For more control, use ``create_mcp_server`` directly:

.. code-block:: python

   import mycommands
   from argklass.mcp import create_mcp_server

   server = create_mcp_server(mycommands, name="my-tools")

   # inspect discovered tools
   for tool in server.tools:
       print(tool.name, tool.schema)

   # run as a stdio MCP server
   server.run()

   # or run with SSE
   server.run(transport="sse", host="0.0.0.0", port=9000)

The server walks the parser tree, discovers every leaf command, converts its
arguments to JSON Schema, and registers them as MCP tools.  When a tool is
invoked, the arguments are converted back to ``argv`` and the command runs
normally — stdout, stderr and exit code are returned to the caller.

You can also call tools directly for testing:

.. code-block:: python

   output = server.call("editor_cook", {"--dry-run": True})


Configuration (sysconfig)
-------------------------

``argklass.sysconfig`` lets you define configuration as dataclasses, with
values resolved from environment variables, a config dict, or defaults
(in that priority order).

.. code-block:: python

   from dataclasses import dataclass, field
   from argklass.sysconfig import ConfigContext

   ctx = ConfigContext(prefix="MYAPP")

   @dataclass
   class DatabaseConfig:
       host: str = ctx.configfield("db.host", str, "localhost")   # MYAPP_DB_HOST
       port: int = ctx.configfield("db.port", int, 5432)          # MYAPP_DB_PORT

   @dataclass
   class AppConfig:
       debug: bool = ctx.configfield("app.debug", bool, False)    # MYAPP_APP_DEBUG
       db: DatabaseConfig = field(default_factory=DatabaseConfig)

Each field is resolved at instantiation time.  Override via environment:

.. code-block:: bash

   export MYAPP_DB_HOST=db.prod.internal
   export MYAPP_DB_PORT=5433

Or programmatically with a config dict:

.. code-block:: python

   ctx.set_config({"db": {"host": "db.staging.internal"}})
   cfg = AppConfig()   # cfg.db.host == "db.staging.internal"

File I/O supports YAML, JSON and HJSON:

.. code-block:: python

   # save / load
   ctx.save_config(cfg, "config.yaml")
   cfg = ctx.load_config(AppConfig, "config.yaml")

   # load and apply as the context's config dict
   ctx.load_and_apply("overrides.yaml")

When several libraries use ``argklass`` in the same process, each one
creates its own ``ConfigContext`` with a unique prefix, keeping environment
variables and config dicts fully isolated.


Architecture
------------

argklass works by building the argument parser as a tree, adding
metadata to each nodes when necessary.

One of the core component is ``ArgumentParserIterator`` which traverse the parsing tree.
Each features, such as argument grouping into dataclasses or saving/loading configuration,
are implemented as a simple traversal.

This enable us to implement each feature independently from each other and make them optional.
