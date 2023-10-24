#!/usr/bin/env python
from pathlib import Path

from setuptools import setup

with open("argklass/__init__.py") as file:
    for line in file.readlines():
        if "version" in line:
            version = line.split("=")[1].strip().replace('"', "")
            break

extra_requires = {"plugins": ["importlib_resources"]}
extra_requires["all"] = sorted(set(sum(extra_requires.values(), [])))

if __name__ == "__main__":
    setup(
        name="argklass",
        version=version,
        extras_require=extra_requires,
        description="Argparse generator from dataclass",
        long_description=(Path(__file__).parent / "README.rst").read_text(),
        author="Delaunay",
        author_email="pierre@delaunay.io",
        license="BSD 3-Clause License",
        url="https://argklass.readthedocs.io",
        classifiers=[
            "License :: OSI Approved :: BSD License",
            "Programming Language :: Python :: 3.8",
            "Programming Language :: Python :: 3.9",
            "Programming Language :: Python :: 3.10",
            "Operating System :: OS Independent",
        ],
        packages=[
            "argklass",
        ],
        setup_requires=["setuptools"],
        install_requires=[
            "importlib_resources",
            "hjson",
        ],
        package_data={
            "argklass.data": [
                "argklass/data",
            ],
        },
    )
