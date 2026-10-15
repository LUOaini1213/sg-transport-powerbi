import datetime as dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from pbitest.dax import Evaluator  # noqa: E402
from pbitest.model import LoadedModel  # noqa: E402


@pytest.fixture(scope="session")
def model():
    return LoadedModel()


@pytest.fixture(scope="session")
def dax(model):
    return Evaluator(model)


def to_dax_filters(model, ctx):
    """{"Table.Column": values} -> {(table, column): values typed like the loaded model."""
    out = {}
    for key, vals in ctx.items():
        table, col = key.split(".", 1)
        kind = model.meta.tables[table].columns[col].data_type
        if kind == "dateTime":
            vals = {dt.date.fromisoformat(v) for v in vals}
        out[(table, col)] = set(vals)
    return out


def close(a, b, rel=1e-9):
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= rel * max(1.0, abs(a), abs(b))
