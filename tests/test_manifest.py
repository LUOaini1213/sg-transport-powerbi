"""The committed CSVs are the ones data/source_manifest.json describes.

scripts/export_data.py writes the SHA-256 of every output into the manifest. Without this check a hand edit to a
CSV, followed by a regenerated expected/visual_values.csv, would leave the manifest stale with every other test
green. The raw DataMall inputs are not public, so these hashes are what ties data/ to the recorded export.
"""
import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
MANIFEST = json.loads((DATA / "source_manifest.json").read_text(encoding="utf-8"))
CSVS = sorted(p.name for p in DATA.glob("*.csv"))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_manifest_lists_exactly_the_committed_csvs():
    assert sorted(MANIFEST["outputs_sha256"]) == CSVS


@pytest.mark.parametrize("name", CSVS)
def test_csv_matches_manifest_hash(name):
    assert sha256(DATA / name) == MANIFEST["outputs_sha256"][name], (
        f"data/{name} differs from the export recorded in data/source_manifest.json; "
        "re-run scripts/export_data.py instead of editing the CSV by hand")


def test_manifest_pins_a_full_commit():
    commit = MANIFEST["sg_bus_network_monitor_commit"]
    assert len(commit) == 40 and all(c in "0123456789abcdef" for c in commit)
