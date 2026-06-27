"""Shared fixtures and marks for the argklass test suite."""

import sys


def pytest_configure(config):
    if sys.version_info < (3, 14):
        config.addinivalue_line(
            "filterwarnings",
            "ignore:Nesting argument groups is deprecated:DeprecationWarning",
        )
