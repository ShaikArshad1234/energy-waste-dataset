import copy

import pytest

from energygen.generate import generate
from energygen.params import STARTER, Params
from energygen.validate import validate

MEDIUM = Params(plants=2, meters=10, days=30, batches=200)


@pytest.fixture(scope="session")
def starter():
    return generate(STARTER)


@pytest.fixture(scope="session")
def medium():
    return generate(MEDIUM)


@pytest.fixture
def fresh(starter):
    """Deep copy of source tables + derived/gt so a test can corrupt them."""
    tables, derived, gt = starter
    return copy.deepcopy(tables), derived, gt
