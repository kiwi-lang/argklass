from dataclasses import dataclass, fields
from enum import Enum
from typing import List, Optional, Tuple, Union

from argklass import argument, choice, subparsers
from argklass.arguments import DocstringIterator


class Color(Enum):
    RED = 1
    GREEN = 2
    BLUE = 3


@dataclass
class SubArgs:
    """My SubArgs docstring"""

    aa: str = argument(default="123")


@dataclass
class cmd1:
    """My cmd1 docstring"""

    args: str = "str1"


@dataclass
class cmd2:
    """My cmd2 docstring"""

    args: str = "str2"


@dataclass
class MyArguments:
    """My Arguments docstring"""

    a: str  # a help
    b: int = 20  # b help
    c: bool = False  # c help
    d: int = choice(0, 1, 2, 3, 4, default=1)  # d help
    e: List[int] = argument(default=[0])  # e help
    f: Optional[int] = None  # f help
    p: Tuple[int, int] = (1, 1)  # p help
    g: Color = Color.RED  # g help
    s: SubArgs = SubArgs()  # s help
    cmd: Union[cmd1, cmd2] = subparsers(cmd1=cmd1, cmd2=cmd2)  # cmd help


def test_find_docstring_a():
    docstr = DocstringIterator(MyArguments)

    assert docstr.get_dataclass_docstring() == MyArguments.__doc__

    found_count = 0
    for field in fields(MyArguments):
        founddocstring = docstr.find_field(field)

        if founddocstring:
            found_count += 1
            assert founddocstring.startswith(field.name)
        else:
            print("Field not found", field.name)

    assert found_count == 10


def get_build_platforms():
    return []


def guess_platform():
    return ""


def get_build_modes():
    return []


# fmt: off
@dataclass
class ArgsExample:
    target  : str                                                             # Name of the the target to build (UnrealPak, RTSGame, RTSGameEditor, etc...)
    platform: str = choice(*get_build_platforms(), default=guess_platform())  # Platform to build for, defaults to current platform (Win64, Linux, etc..)
    mode    : str = choice(*get_build_modes(), default="Development")         # Build mode (Tests, Debug, Development, Shipping)
    profile : Optional[str] = None                                            # Build multiple targets using a configuration
# fmt: on


def test_find_docstring_b():
    docstr = DocstringIterator(ArgsExample)

    # Dataclasses auto generate a docstring in this case
    # assert docstr.get_dataclass_docstring() == ArgsExample.__doc__

    found_count = 0
    for field in fields(ArgsExample):
        founddocstring = docstr.find_field(field)

        if founddocstring:
            found_count += 1
            print(founddocstring)
        else:
            print("Field not found", field.name)

    assert found_count == 4
