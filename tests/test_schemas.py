"""JSON schema validation of the PBIP/PBIR files, inside the test run.

Needs the local copy of Microsoft's schemas (bash tools/fetch_schemas.sh). Without it the test is skipped
locally; in CI SCHEMAS_REQUIRED=1 turns the skip into a failure.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / ".schemas" / "json-schemas"
sys.path.insert(0, str(ROOT / "tools"))
import validate_schemas  # noqa: E402


def test_pbir_files_match_microsoft_schemas():
    if not SCHEMAS.exists():
        if os.environ.get("SCHEMAS_REQUIRED"):
            pytest.fail("schemas missing: run bash tools/fetch_schemas.sh")
        pytest.skip("schemas not fetched")
    checked, failures = validate_schemas.validate(ROOT, SCHEMAS)
    assert not failures, "\n".join(failures)
    assert len(checked) == len(validate_schemas.project_files(ROOT))
    assert len(checked) >= 20
