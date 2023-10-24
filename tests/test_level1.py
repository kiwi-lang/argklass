from dataclasses import dataclass
from typing import List, Optional, Tuple, Union
from enum import Enum
from argklass import argument, ArgumentParser, group, subparsers

from argklass.arguments import is_list, is_optional, is_tuple, is_enum


class Color(Enum):
    RED = 1
    GREEN = 2
    BLUE = 3


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
    a: str = argument(help="Positional")
    b: int = argument(default=20, help="My argument")
    c: bool = argument(action="store_true", help="My argument")
    d: int = argument(default=1, choices=[0, 1, 2, 3, 4], help="choices")
    e: List[int] = argument(default=[0], help="list")
    f: Optional[int] = argument(default=None, help="Optional")
    p: Tuple[int, int] = argument(default=(1, 1), help="help p")
    g: Color = argument(default=Color.RED, help="help g")
    s: SubArgs = group(default=SubArgs, help="helps group")
    cmd: Union[cmd1, cmd2] = subparsers(cmd1=cmd1, cmd2=cmd2)


def test_annotations():
    ann = List[int]
    assert is_list(ann, [0])

    ann = Optional[int]
    assert is_optional(ann, [0])

    ann = Tuple[int, int]
    assert is_tuple(ann, None)

    ann = Color
    assert is_enum(ann, [0])


testargs = [
    "--b",
    "2",
    "--c",
    "positional",
    "--d",
    "3",
    "--e",
    "1",
    "2",
    "3",
    "4",
    "--p",
    "1",
    "2",
    "--g",
    "GREEN",
    "--aa",
    "432",
    "cmd2",
]


def test_argparse():
    parser = ArgumentParser()
    parser.add_arguments(MyArguments)

    parser.print_help()

    args = parser.parse_args(testargs)
    print(args)


def test_argparse_grouped():
    parser = ArgumentParser(group_by_dataclass=True)
    parser.add_arguments(MyArguments, create_group=True)

    args = parser.parse_args(testargs)
    print(args)
