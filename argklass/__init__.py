"""Top level module for uetools"""

__descr__ = "Unreal Engine Tools"
__version__ = "1.2.1"
__license__ = "BSD 3-Clause License"
__author__ = "Pierre Delaunay"
__author_email__ = "pierre@delaunay.io"
__copyright__ = "2023 Pierre Delaunay"
__url__ = "https://github.com/kiwi-lang/argklass"


from .arguments import argument, ArgumentParser, group, subparsers, choice

__all__ = [argument, ArgumentParser, group, subparsers, choice]
