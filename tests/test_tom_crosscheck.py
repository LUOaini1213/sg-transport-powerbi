"""Compare what Microsoft's TOM library read from the TMDL files with what pbitest.tmdl read.

tools/check_tmdl.ps1 writes .tom/tom_model.json. If the two readers disagree on a table, a column type, a measure's
DAX text or a relationship, the measure tests would be checking something other than what Power BI loads.
Skipped when the summary is absent; in CI TOM_REQUIRED=1 turns the skip into a failure.
"""
import json
import os
from pathlib import Path

import pytest

SUMMARY = Path(__file__).resolve().parents[1] / ".tom" / "tom_model.json"
TOM_TYPES = {"String": "string", "Int64": "int64", "Double": "double", "DateTime": "dateTime", "Boolean": "boolean"}


@pytest.fixture(scope="module")
def tom():
    if not SUMMARY.exists():
        if os.environ.get("TOM_REQUIRED"):
            pytest.fail("run pwsh tools/check_tmdl.ps1 first")
        pytest.skip("TOM summary not generated")
    return json.loads(SUMMARY.read_text(encoding="utf-8-sig"))


def test_same_tables_columns_and_types(tom, model):
    assert set(tom["tables"]) == set(model.meta.tables)
    for name, t in tom["tables"].items():
        mine = model.meta.tables[name]
        assert (t["dataCategory"] or "") == mine.data_category
        assert list(t["columns"]) == list(mine.columns)
        for cname, c in t["columns"].items():
            assert TOM_TYPES[c["dataType"]] == mine.columns[cname].data_type, f"{name}[{cname}]"
            assert c["sourceColumn"] == mine.columns[cname].source_column
            assert c["isKey"] == mine.columns[cname].is_key


def test_same_measure_text(tom, model):
    for name, t in tom["tables"].items():
        mine = model.meta.tables[name].measures
        assert list(t["measures"]) == list(mine)
        for mname, m in t["measures"].items():
            assert m["expression"].replace("\r\n", "\n").strip() == mine[mname].expression.strip(), mname
            assert m["formatString"] == mine[mname].format_string, mname


def test_same_partitions_and_relationships(tom, model):
    for name, t in tom["tables"].items():
        assert len(t["partitionSources"]) == 1
        assert t["partitionSources"][0].replace("\r\n", "\n").strip() == model.meta.tables[name].partition_source.strip()
    rels = {(r["from"], r["to"]) for r in tom["relationships"]}
    assert rels == {(f"{r.from_table}.{r.from_column}", f"{r.to_table}.{r.to_column}") for r in model.relationships}
    for r in tom["relationships"]:
        assert (r["fromCardinality"], r["toCardinality"], r["crossFilteringBehavior"], r["isActive"]) == \
            ("Many", "One", "OneDirection", True)
    assert tom["compatibilityLevel"] >= 1600
