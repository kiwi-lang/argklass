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

    for field in fields(MyArguments):
        founddocstring = docstr.find_field(field)

        if founddocstring:
            assert founddocstring.startswith(field.name)
