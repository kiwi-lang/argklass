__descr__ = "Argparse utility"
__version__ = "1.4.2"
__license__ = "BSD 3-Clause License"
__author__ = "Pierre Delaunay"
__author_email__ = "pierre@delaunay.io"
__copyright__ = "2023 Pierre Delaunay"
__url__ = "https://github.com/kiwi-lang/argklass"


from .arguments import ArgumentParser, argument, choice, group, subparsers

__all__ = [
    "argument",
    "ArgumentParser",
    "group",
    "subparsers",
    "choice",
]
