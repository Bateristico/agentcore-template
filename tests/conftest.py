import pytest


def pytest_addoption(parser):
    parser.addoption("--run-aws", action="store_true", help="run tests that call real AWS")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-aws"):
        return
    skip = pytest.mark.skip(reason="needs --run-aws")
    for item in items:
        if "aws" in item.keywords:
            item.add_marker(skip)
