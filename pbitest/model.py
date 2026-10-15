"""Load the CSV extracts into the tables the semantic model declares, with the declared data types.

This mirrors what the Power Query partitions do (Csv.Document, PromoteHeaders, TransformColumnTypes with the
en-US culture): text stays text, an empty field in a number column becomes null, dates are parsed as dates.
"""
import datetime as dt
from pathlib import Path

import pandas as pd

from .tmdl import SemanticModel, parse_folder

ROOT = Path(__file__).resolve().parents[1]
DEFINITION = ROOT / "SGTransport.SemanticModel" / "definition"
DATA = ROOT / "data"


def _cast(series, data_type):
    if data_type == "string":
        return series.astype(object)
    if data_type == "int64":
        return pd.to_numeric(series.mask(series == ""), errors="raise").astype("Int64")
    if data_type in ("double", "decimal"):
        return pd.to_numeric(series.mask(series == ""), errors="raise").astype(float)
    if data_type == "dateTime":
        return series.map(lambda v: dt.date.fromisoformat(v) if v else None)
    if data_type == "boolean":
        lookup = {"true": True, "false": False}
        return series.map(lambda v: lookup[v.lower()] if v else None)
    raise ValueError(f"unsupported data type {data_type}")


class LoadedModel:
    def __init__(self, definition=DEFINITION, data=DATA):
        self.meta: SemanticModel = parse_folder(definition)
        self.frames = {}
        for name, t in self.meta.tables.items():
            raw = pd.read_csv(Path(data) / t.csv_file, dtype=str, keep_default_na=False)
            missing = [c.source_column for c in t.columns.values() if c.source_column not in raw.columns]
            if missing:
                raise ValueError(f"{t.csv_file} lacks columns {missing} declared in table {name}")
            self.frames[name] = pd.DataFrame({c.name: _cast(raw[c.source_column], c.data_type)
                                              for c in t.columns.values()})
        self.relationships = self.meta.relationships
        self.measures = self.meta.measures
        self.date_tables = {n: t.key_column for n, t in self.meta.tables.items() if t.data_category == "Time"}
