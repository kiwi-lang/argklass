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


.. code-block:: bash

   pip install argklass


Features
--------

Compact argparse definition

.. code-block:: python

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

   parser = ArgumentParser()
   parser.add_arguments(MyArguments)
   args = parser.parse_args()


Lower level interface, that gives you back all of argparse power

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


Save and load from configuration files

