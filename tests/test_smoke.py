import importlib

import pytest


@pytest.mark.parametrize("name", ["api", "extract", "checks", "rules"])
def test_package_imports(name):
    importlib.import_module(name)
