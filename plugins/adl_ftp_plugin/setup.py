#!/usr/bin/env python
import os

from setuptools import find_packages, setup

PROJECT_DIR = os.path.dirname(__file__)
REQUIREMENTS_DIR = os.path.join(PROJECT_DIR, "requirements")
VERSION = "0.14.1"


def get_requirements(env):
    # The .in file, not the compiled .txt: install_requires must state what the
    # plugin needs, not a frozen resolution of it. Exact pins of transitive
    # packages (cryptography, sqlparse, ...) would otherwise be forced into the
    # ADL image and hold back their security fixes.
    with open(os.path.join(REQUIREMENTS_DIR, f"{env}.in")) as fp:
        return [
            x.strip()
            for x in fp.read().split("\n")
            if x.strip() and not x.strip().startswith("#") and not x.strip().startswith("-")
        ]


install_requires = get_requirements("base")

setup(
    name="adl-ftp-plugin",
    version=VERSION,
    url="https://github.com/wmo-raf/adl-ftp-plugin",
    author="WMO RAF",
    author_email="eotenyo@wmo.int",
    license="MIT",
    description="ADL Plugin for automated observation data loading from FTP storages",
    long_description="TODO",
    platforms=["linux"],
    package_dir={"": "src"},
    packages=find_packages("src"),
    include_package_data=True,
    install_requires=install_requires
)
