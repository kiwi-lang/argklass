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

__all__ = [
    "argument",
    "ArgumentParser",
    "group",
    "subparsers",
    "choice",
]
